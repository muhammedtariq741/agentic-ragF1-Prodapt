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

Every time you respond, you MUST use the following format. First, use a "scratchpad" to reflect on previous tool results and plan your next step. Then, provide your action.

To call a tool:
{{"scratchpad": {{"known": {{"fact_name": {{"value": "...", "source": "tool_name", "confidence": "high|medium|low"}}}}, "missing": ["list of facts still needed"], "conflicts": []}}, "action": "tool_call", "tool": "<tool_name>", "input": "<natural language query for the tool>"}}

To give the final answer (after you have enough information):
{{"scratchpad": {{"known": {{...}}, "missing": [], "conflicts": []}}, "action": "final_answer", "answer": "<your answer text>", "citations": "<which tools/sources provided the info>"}}

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
    scratchpad_m = re.search(r'"scratchpad"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL)
    if action_m:
        result = {"action": action_m.group(1)}
        if tool_m: result["tool"] = tool_m.group(1)
        if input_m: result["input"] = input_m.group(1)
        if answer_m: result["answer"] = answer_m.group(1)
        if citations_m: result["citations"] = citations_m.group(1)
        if scratchpad_m: result["scratchpad"] = scratchpad_m.group(1)
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
        """Run the agent loop for a single question (max 8 steps)."""
        trace = []
        tool_call_count = 0
        memory = {"known": {}, "missing": [], "conflicts": []}
        conversation = [self.system_prompt, f"\nUser question: {question}\n\nRespond with JSON."]

        for step in range(1, 50):
            # 1. Call LLM with basic retry
            raw_text = None
            for _ in range(3):
                try:
                    raw_text = self.model.generate_content("\n".join(conversation)).text.strip()
                    break
                except Exception as e:
                    if "429" in str(e): time.sleep(10)
            if not raw_text:
                return {"question": question, "answer": "ERROR: LLM failed to respond after 3 attempts.", 
                        "citations": "None", "trace": trace, "steps_used": tool_call_count}

            # 2. Parse JSON decision and State Tracker
            try:
                decision = parse_llm_json(raw_text)
                action = decision.get("action", "")
                scratchpad = decision.get("scratchpad", {})
                if isinstance(scratchpad, dict):
                    # Merge LLM's structured memory into our running memory
                    for k, v in scratchpad.get("known", {}).items():
                        memory["known"][k] = v
                    memory["missing"] = scratchpad.get("missing", memory["missing"])
                    memory["conflicts"] = scratchpad.get("conflicts", memory["conflicts"])
                trace.append({"step": step, "state": "REFLECT & PLAN", "memory": memory.copy()})
            except Exception as e:
                trace.append({"step": step, "state": "ERROR", "error": f"Parse error: {e}", "raw": raw_text[:100]})
                conversation.append("\n[SYSTEM ERROR: Invalid JSON. Reply with pure JSON.]\n")
                continue

            # 3. Auto-correct missing 'tool_call' syntax
            if action in self.tools:
                decision.update({"tool": action, "action": "tool_call"})
                action = "tool_call"

            # 4. Route Action
            if action == "final_answer":
                grounded = [f"{e['tool']} (input: '{e['input']}')" 
                            for e in trace if e.get("state") == "ACT"]
                grounded_str = "; ".join(grounded) if grounded else "None"
                return {"question": question, "answer": decision.get("answer", "None"), 
                        "citations": grounded_str + " | LLM note: " + decision.get("citations", ""), "trace": trace, "steps_used": tool_call_count}
            
            elif action == "tool_call":
                t_name, t_input = decision.get("tool", ""), decision.get("input", "")
                if t_name not in self.tools:
                    trace.append({"step": step, "error": f"Unknown tool '{t_name}'"})
                    conversation.append(f"\n[SYSTEM ERROR: Unknown tool '{t_name}']\n")
                    continue
                
                if tool_call_count >= self.MAX_STEPS:
                    trace.append({"step": step, "state": "ERROR", "error": "Hard cap reached"})
                    break
                tool_call_count += 1
                
                print(f"  Step {step}: tool={t_name} input='{t_input}'")
                try: result = self.tools[t_name].run(t_input)
                except Exception as e: result = f"ERROR: {e}"

                trace.append({"step": step, "state": "ACT", "tool": t_name, "input": t_input, "result": str(result)[:300]})
                conversation.append(f"\nTool result from {t_name}:\n{result}\n\nCurrent memory state: {json.dumps(memory)}\nReview the data. Update your scratchpad memory (known/missing/conflicts). Respond with JSON.")
            
            else:
                trace.append({"step": step, "state": "ERROR", "error": f"Unknown action: {action}"})
                conversation.append("\n[SYSTEM ERROR: Unknown action. Use tool_call or final_answer.]\n")

        # 5. Hard cap fallback
        return {"question": question, "answer": "REFUSAL: Maximum of 8 tool calls reached.", 
                "citations": "None", "trace": trace, "steps_used": tool_call_count}

def print_trace(result: dict):
    """Pretty-print the agent trace in the assignment-required format."""
    print("\n" + "=" * 70)
    print(f"Question: {result['question']}")
    print("-" * 70)
    for entry in result["trace"]:
        step = entry.get("step", "?")
        state = entry.get("state", "?")
        if state == "REFLECT & PLAN":
            mem = entry.get("memory", {})
            known_count = len(mem.get("known", {}))
            missing = mem.get("missing", [])
            conflicts = mem.get("conflicts", [])
            print(f"  Step {step} [{state}]: known={known_count} facts, missing={missing}")
            if conflicts:
                print(f"           conflicts={conflicts}")
        elif state == "ACT":
            print(f"  Step {step} [{state}]: tool={entry.get('tool')} input='{entry.get('input')}'")
            preview = entry.get('result', '')[:200].replace("\n", " ")
            print(f"           result={preview}...")
        elif state == "ERROR":
            print(f"  Step {step} [ERROR]: {entry.get('error')}")
    print("-" * 70)
    print(f"Final Answer: {result['answer']}")
    print(f"Citations: {result['citations']}")
    print(f"Steps used: {result['steps_used']} / {F1Agent.MAX_STEPS} max")
    print("=" * 70)


def print_trace_simple(result: dict):
    """Compact trace format for evaluation reports and PDF export."""
    print(f"\nQ: {result['question']}")
    tools_used = [e.get("tool") for e in result["trace"] if e.get("state") == "ACT"]
    print(f"Tools: {', '.join(tools_used) if tools_used else 'None (direct answer)'}")
    print(f"Steps: {result['steps_used']}/{F1Agent.MAX_STEPS}")
    print(f"Answer: {result['answer'][:300]}")
    print(f"Citations: {result['citations']}")
    print()


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
