"""
Agentic RAG for Formula 1 — Main Agent Loop

A ReAct-style agent that routes user questions to the appropriate tool(s),
composes a final answer with citations, and enforces a hard cap of 8 tool calls.

Usage:
    python main.py "Who won the 2024 British Grand Prix?"
    python main.py                          # interactive mode
"""
import json
import os
import re
import sys
import time

import google.generativeai as genai
from dotenv import load_dotenv

from tools.query_data import QueryDataTool
from tools.search_docs import SearchDocsTool
from tools.web_search import WebSearchTool

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# ---------------------------------------------------------------------------
# System prompt — injected once at the start of every conversation
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert Formula 1 assistant. You have access to three tools.

TOOLS:
{tool_descriptions}

RESPONSE FORMAT — you MUST reply with valid JSON, nothing else.

To call a tool:
{{"action": "tool_call", "tool": "<tool_name>", "input": "<natural language query for the tool>"}}

To give the final answer (after you have enough information):
{{"action": "final_answer", "answer": "<your answer text>", "citations": "<which tools/sources provided the info>"}}

RULES:
1. Think step-by-step. Call ONE tool at a time.
2. After receiving a tool result, decide: do you have enough info, or do you need another tool?
3. For questions about 2023-2024 race STATISTICS (points, positions, wins), use query_data FIRST.
4. For questions about WHY something happened, narratives, or strategies, use search_docs.
5. For questions about events AFTER 2024, breaking news, or live standings, use web_search.
6. For trivial questions (math, general knowledge unrelated to F1 data), answer directly with NO tool call.
7. REFUSE to answer: investment advice, personal opinions on which team is "best", predictions, or anything outside your capability. Return a final_answer explaining why you cannot answer.
8. If a question asks about data you do NOT have (e.g., seasons before 2023), say so honestly. Do NOT hallucinate.
9. You have a MAXIMUM of 8 tool calls. Use them wisely.
10. Always cite your sources in the final answer.
"""


def build_tool_descriptions(tools: dict) -> str:
    """Build the tool description block for the system prompt."""
    lines = []
    for t in tools.values():
        lines.append(f"- {t.name}: {t.description}")
    return "\n".join(lines)


def parse_llm_json(text: str) -> dict:
    """Extract JSON from the LLM response, handling markdown fences and common issues."""
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:-1]).strip()
    # Try to find JSON object in the text
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError(f"No valid JSON found in LLM response: {text[:200]}")
    raw = match.group()
    # Attempt 1: parse as-is
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Attempt 2: fix common LLM issues — unescaped newlines & trailing commas
    cleaned = raw.replace("\n", " ").replace("\r", " ")
    cleaned = re.sub(r',\s*}', '}', cleaned)   # trailing comma before }
    cleaned = re.sub(r',\s*]', ']', cleaned)   # trailing comma before ]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Attempt 3: extract just the key fields with regex (last resort)
    action_m = re.search(r'"action"\s*:\s*"([^"]+)"', raw)
    tool_m = re.search(r'"tool"\s*:\s*"([^"]+)"', raw)
    input_m = re.search(r'"input"\s*:\s*"([^"]+)"', raw)
    answer_m = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL)
    citations_m = re.search(r'"citations"\s*:\s*"([^"]+)"', raw)
    if action_m:
        result = {"action": action_m.group(1)}
        if tool_m:
            result["tool"] = tool_m.group(1)
        if input_m:
            result["input"] = input_m.group(1)
        if answer_m:
            result["answer"] = answer_m.group(1)
        if citations_m:
            result["citations"] = citations_m.group(1)
        return result
    raise ValueError(f"No valid JSON found in LLM response: {text[:200]}")


class F1Agent:
    """ReAct-style agent with a hard cap of 8 tool calls per question."""

    MAX_STEPS = 8

    def __init__(self):
        self.tools = {
            t.name: t
            for t in [QueryDataTool(), SearchDocsTool(), WebSearchTool()]
        }
        self.model = genai.GenerativeModel("gemini-2.5-flash")
        self.system_prompt = SYSTEM_PROMPT.format(
            tool_descriptions=build_tool_descriptions(self.tools)
        )

    def run(self, question: str) -> dict:
        """
        Run the agent loop for a single question.

        Returns:
            dict with keys: question, answer, citations, trace, steps_used
        """
        trace = []
        # Build conversation as a list of content strings
        conversation = [
            self.system_prompt,
            f"\nUser question: {question}\n\nRespond with JSON.",
        ]

        for step in range(1, self.MAX_STEPS + 1):
            # Call the LLM (with retry for rate limits)
            raw_text = None
            for attempt in range(3):
                try:
                    response = self.model.generate_content("\n".join(conversation))
                    raw_text = response.text.strip()
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 2:
                        wait = (attempt + 1) * 10
                        print(f"  [Rate limited, retrying in {wait}s...]")
                        time.sleep(wait)
                        continue
                    trace.append({"step": step, "error": f"LLM call failed: {e}"})
                    break
            if raw_text is None:
                break

            # Parse the LLM's JSON decision
            try:
                decision = parse_llm_json(raw_text)
            except (json.JSONDecodeError, ValueError) as e:
                trace.append({"step": step, "error": f"JSON parse failed: {e}", "raw": raw_text[:300]})
                # Ask the LLM to retry with valid JSON
                conversation.append(f"\n[SYSTEM ERROR: Your response was not valid JSON. Reply with ONLY a JSON object.]\n")
                continue

            action = decision.get("action", "")

            # --- AUTO-CORRECT: LLM put tool name in "action" instead of using "tool_call" ---
            if action in self.tools:
                decision["tool"] = action
                decision["action"] = "tool_call"
                action = "tool_call"

            # ---- FINAL ANSWER ----
            if action == "final_answer":
                answer = decision.get("answer", "No answer provided.")
                citations = decision.get("citations", "None")
                return {
                    "question": question,
                    "answer": answer,
                    "citations": citations,
                    "trace": trace,
                    "steps_used": step,
                }

            # ---- TOOL CALL ----
            if action == "tool_call":
                tool_name = decision.get("tool", "")
                tool_input = decision.get("input", "")

                if tool_name not in self.tools:
                    error_msg = f"Unknown tool '{tool_name}'. Available: {list(self.tools.keys())}"
                    trace.append({"step": step, "error": error_msg})
                    conversation.append(f"\n[SYSTEM ERROR: {error_msg}]\n")
                    continue

                # Execute the tool
                tool = self.tools[tool_name]
                print(f"  Step {step}: tool={tool_name} input='{tool_input}'")
                try:
                    result = tool.run(tool_input)
                except Exception as e:
                    result = f"ERROR: {type(e).__name__}: {e}"

                trace.append({
                    "step": step,
                    "tool": tool_name,
                    "input": tool_input,
                    "result": result[:1000],  # cap trace size
                })

                # Feed the result back into the conversation
                conversation.append(
                    f"\nTool result from {tool_name}:\n{result}\n\n"
                    f"Based on this result, decide: do you have enough information to answer "
                    f"the user's question, or do you need to call another tool? "
                    f"Respond with JSON."
                )
                continue

            # Unknown action
            trace.append({"step": step, "error": f"Unknown action: {action}", "raw": raw_text[:300]})
            conversation.append("\n[SYSTEM ERROR: Unknown action. Use 'tool_call' or 'final_answer'.]\n")

        # ---- HARD CAP REACHED ----
        return {
            "question": question,
            "answer": (
                f"REFUSAL: I was unable to answer your question within the maximum "
                f"of {self.MAX_STEPS} tool calls. This may indicate the question is "
                f"too complex, ambiguous, or unanswerable with the available data. "
                f"Please try rephrasing your question."
            ),
            "citations": "None (hard cap reached)",
            "trace": trace,
            "steps_used": self.MAX_STEPS,
        }


def print_trace(result: dict):
    """Pretty-print the agent trace in the assignment-required format."""
    print("\n" + "=" * 70)
    print(f"Question: {result['question']}")
    print("-" * 70)
    for entry in result["trace"]:
        step = entry.get("step", "?")
        if "tool" in entry:
            print(f"  Step {step}: tool={entry['tool']} input='{entry['input']}'")
            # Show first 200 chars of result
            preview = entry["result"][:200].replace("\n", " ")
            print(f"           result={preview}...")
        elif "error" in entry:
            print(f"  Step {step}: ERROR — {entry['error']}")
    print("-" * 70)
    print(f"Final Answer: {result['answer']}")
    print(f"Citations: {result['citations']}")
    print(f"Steps used: {result['steps_used']} / {F1Agent.MAX_STEPS} max")
    print("=" * 70)


def main():
    agent = F1Agent()

    if len(sys.argv) > 1:
        # Single question mode
        question = " ".join(sys.argv[1:])
        result = agent.run(question)
        print_trace(result)
    else:
        # Interactive mode
        print("=" * 50)
        print("  F1 Agentic RAG — Interactive Mode")
        print("  Type 'quit' or 'exit' to stop.")
        print("=" * 50)
        while True:
            try:
                question = input("\n🏎️  Ask an F1 question: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break
            if question.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break
            if not question:
                continue
            result = agent.run(question)
            print_trace(result)


if __name__ == "__main__":
    main()
