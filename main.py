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

from utils.llm import generate_llm_response
from dotenv import load_dotenv

from tools.query_data import QueryDataTool
from tools.search_docs import SearchDocsTool
from tools.web_search import WebSearchTool

load_dotenv()

# ---------------------------------------------------------------------------
# System prompt — injected once at the start of every conversation
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert Formula 1 assistant. You have access to three tools.

TOOLS:
{tool_descriptions}

DATABASE SCHEMA (for query_data tool):
Table: race_results
Columns: season (INT, 2024 or 2025), round (INT), grand_prix (TEXT, e.g. "Bahrain Grand Prix"), circuit (TEXT), date (TEXT), driver (TEXT, full name), driver_code (TEXT, 3-letter), driver_nationality (TEXT), constructor (TEXT, team name), grid_position (INT), finish_position (INT, 1=winner), position_text (TEXT, "1" or "R" for retired), points (REAL), laps_completed (INT), status (TEXT), fastest_lap_rank (TEXT), fastest_lap_time (TEXT)

F1 Location Mapping:
  "Abu Dhabi"/"Yas Marina" → grand_prix="Abu Dhabi Grand Prix" | "Australia"/"Melbourne" → "Australian Grand Prix" | "Austria"/"Spielberg" → "Austrian Grand Prix" | "Azerbaijan"/"Baku" → "Azerbaijan Grand Prix" | "Bahrain" → "Bahrain Grand Prix" | "Belgium"/"Spa" → "Belgian Grand Prix" | "Britain"/"Silverstone" → "British Grand Prix" | "Canada"/"Montreal" → "Canadian Grand Prix" | "China"/"Shanghai" → "Chinese Grand Prix" | "Netherlands"/"Zandvoort" → "Dutch Grand Prix" | "Imola" → "Emilia Romagna Grand Prix" | "Hungary"/"Budapest" → "Hungarian Grand Prix" | "Italy"/"Monza" → "Italian Grand Prix" | "Japan"/"Suzuka" → "Japanese Grand Prix" | "Las Vegas" → "Las Vegas Grand Prix" | "Mexico" → "Mexico City Grand Prix" | "Miami" → "Miami Grand Prix" | "Monaco" → "Monaco Grand Prix" | "Qatar"/"Lusail" → "Qatar Grand Prix" | "Saudi Arabia"/"Jeddah" → "Saudi Arabian Grand Prix" | "Singapore" → "Singapore Grand Prix" | "Spain"/"Barcelona" → "Spanish Grand Prix" | "Brazil"/"Interlagos" → "São Paulo Grand Prix" | "USA"/"Austin"/"COTA" → "United States Grand Prix"

RESPONSE FORMAT — you MUST reply with valid JSON, nothing else.

Every time you respond, you MUST use the following format. First, use a "scratchpad" to reflect on previous tool results and plan your next step. Then, provide your action.

To call query_data, pass a raw SQL SELECT query as the input:
{{"scratchpad": {{"known": {{...}}, "missing": [...], "conflicts": []}}, "action": "tool_call", "tool": "query_data", "input": "SELECT driver, points FROM race_results WHERE grand_prix = 'Miami Grand Prix' AND season = 2024"}}

To call search_docs or web_search, pass a natural language query:
{{"scratchpad": {{"known": {{...}}, "missing": [...], "conflicts": []}}, "action": "tool_call", "tool": "search_docs", "input": "why did Norris win Miami 2024"}}

To give the final answer (after you have enough information):
{{"scratchpad": {{"known": {{...}}, "missing": [], "conflicts": []}}, "action": "final_answer", "answer": "<your answer text>", "citations": "<which tools/sources provided the info>"}}

SQL RULES for query_data:
- ONLY generate SELECT queries. No DROP, DELETE, UPDATE, INSERT.
- Use LIKE for partial text matching (e.g., driver LIKE '%Verstappen%').
- Use the location mapping above to match grand_prix values exactly.
- For "wins", use finish_position = 1.
- For "podiums", use finish_position <= 3 (P1, P2, and P3 are ALL podiums — wins count as podiums).
- "Monza" = Italian GP, "Imola" = Emilia Romagna GP (different races).
- For "total points", "championship standings", or "season points", ALWAYS use SUM(points) with GROUP BY driver. Each row in the table is ONE race result, not a season total.
- For "total wins" or "how many wins", use COUNT(*) with GROUP BY and finish_position = 1.

RULES:
1. Think step-by-step. Call ONE tool at a time.
2. After receiving a tool result, decide: do you have enough info, or do you need another tool?
3. For questions about 2024 or 2025 race STATISTICS (points, positions, wins, podiums), use query_data FIRST. The database contains BOTH 2024 AND 2025 season data.
4. For questions about WHY something happened, narratives, or strategies, use search_docs.
5. For questions about events AFTER 2025, breaking news, or live standings, use web_search.
6. For trivial questions (math, general knowledge unrelated to F1 data), answer directly with NO tool call.
7. REFUSE to answer: investment advice, personal opinions on which team is "best", predictions, or anything outside your capability. Return a final_answer explaining why you cannot answer.
8. If a question asks about data you do NOT have (e.g., seasons before 2024), say so honestly. Do NOT hallucinate.
9. You have a MAXIMUM of 8 tool calls. Use them wisely.
10. Always cite your sources in the final answer.
11. SANITY CHECK: If query_data returns a suspiciously low number (e.g., a "season total" of 25 points), your SQL is probably wrong. Try rewriting the query with SUM() and GROUP BY, or fall back to web_search.
12. CRITICAL — CAREER STATS vs SEASON STATS: The database ONLY contains race-by-race results for the 2024 and 2025 seasons. It does NOT store career totals or historical records. If a question asks about "total wins", "career wins", "how many wins does X have", "total podiums", or "how many championships/titles" WITHOUT specifying a season (2024 or 2025), you MUST use search_docs or web_search — NEVER query_data. query_data will only return 2024-2025 counts, which is NOT the career total. Only use query_data when the question explicitly mentions 2024 or 2025.
13. ANTI-HALLUCINATION: NEVER invent or guess specific numbers (titles, stats, dates) that are not EXPLICITLY stated in a tool result. If a web_search result does not contain the exact number you need, either do another web_search with a more specific query, or say "I could not find the exact figure." Quoting approximate or partial info from the source is fine, but fabricating precise numbers is FORBIDDEN.
14. FORMAT: Round all decimal numbers to 2 decimal places in your final answer (e.g., 7.041666... → 7.04).
15. ANTI-LAZINESS: You MUST make at least 2 different tool calls before concluding "I could not find the answer." If the first search returns insufficient data, try a different query or a different tool. Only give up after genuinely exhausting your options.
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
        self.system_prompt = SYSTEM_PROMPT.format(
            tool_descriptions=build_tool_descriptions(self.tools)
        )
        self.telemetry = {name: {"calls": 0, "ms": 0} for name in self.tools}

    def _build_context(self, question, tool_history, memory):
        """Build the LLM user prompt from scratch each iteration."""
        parts = [f"User question: {question}"]
        if tool_history:
            parts.append("\n--- TOOL RESULTS SO FAR ---")
            for th in tool_history:
                parts += [f"Tool: {th['tool']} | Query: {th['input']}",
                          f"Result: {th['result_summary']}", "---"]
        parts += [f"\nCurrent memory state: {json.dumps(memory)}",
                  "\nCRITICAL: Extract ALL facts into scratchpad. No duplicate queries.",
                  "If results contain the answer, output final_answer NOW. Reply JSON only."]
        return "\n".join(parts)

    def _run_tool_with_retry(self, t_name, t_input):
        """Execute a tool with one retry on failure. Returns (result, error)."""
        for attempt in range(1, 3):
            try:
                return self.tools[t_name].run(t_input), None
            except Exception as e:
                if attempt == 1:
                    print(f"  ['{t_name}' failed, retrying...]")
                    time.sleep(1)
                else:
                    return None, e
        return None, RuntimeError("Retry exhausted")

    def _critique_answer(self, question, proposed_answer, tool_history):
        """Bonus C: Post-loop self-critique. Returns (final_answer, critique_note)."""
        print("  [CRITIQUE & FIX] Reviewing proposed final answer...")
        critique_sys = (
            "You are an expert F1 evaluator. Critique the proposed answer based ONLY on the provided tool results. "
            "Look for hallucinations, missing context, or failures to directly answer the user's question. "
            "If the answer is perfect, return it exactly as is. If it has flaws, return a corrected version. "
            'Respond with JSON: {"critique": "your analysis", "answer": "the final answer"}'
        )
        critique_user = f"Original Question: {question}\n\nTool Results:\n"
        for th in tool_history:
            critique_user += f"Tool: {th['tool']} | Result: {th['result_summary']}\n"
        critique_user += f"\nProposed Answer: {proposed_answer}"
        try:
            raw = generate_llm_response(critique_sys, critique_user)
            parsed = parse_llm_json(raw)
            return parsed.get("answer", proposed_answer), parsed.get("critique", "No critique.")
        except Exception as e:
            return proposed_answer, f"Critique failed: {e}"

    def _build_citations(self, trace, decision):
        """Build grounded citation string from trace + LLM's own citation note."""
        grounded = "; ".join(f"{e['tool']} (input: '{e['input']}')"
                             for e in trace if e.get("state") == "ACT") or "None"
        llm_note = decision.get("citations", "")
        if isinstance(llm_note, list):
            llm_note = ", ".join(str(c) for c in llm_note)
        return f"{grounded} | LLM note: {llm_note}"

    def run(self, question: str) -> dict:
        """Run the agent loop for a single question (max 8 tool calls)."""
        trace, tool_history, seen_calls = [], [], set()
        memory = {"known": {}, "missing": [], "conflicts": []}
        tool_call_count = 0

        for step in range(1, self.MAX_STEPS * 2 + 1):
            # 1. Ask LLM
            try:
                raw = generate_llm_response(
                    self.system_prompt,
                    self._build_context(question, tool_history, memory))
            except Exception as e:
                trace.append({"step": step, "state": "ERROR", "error": str(e)})
                return {"question": question, "answer": "ERROR: LLM failed.",
                        "citations": "None", "trace": trace, "steps_used": tool_call_count}
            if not raw:
                return {"question": question, "answer": "ERROR: LLM failed to respond.",
                        "citations": "None", "trace": trace, "steps_used": tool_call_count}

            # 2. Parse JSON decision & update scratchpad memory
            try:
                decision = parse_llm_json(raw)
                action = decision.get("action", "")
                sp = decision.get("scratchpad", {})
                if isinstance(sp, dict):
                    memory["known"].update(sp.get("known", {}))
                    memory["missing"] = sp.get("missing", memory["missing"])
                    memory["conflicts"] = sp.get("conflicts", memory["conflicts"])
                trace.append({"step": step, "state": "REFLECT & PLAN", "memory": memory.copy()})
            except Exception as e:
                trace.append({"step": step, "state": "ERROR", "error": f"Parse error: {e}"})
                tool_history.append({"tool": "SYSTEM", "input": "parse",
                                     "result_summary": "ERROR: Invalid JSON. Reply PURE JSON only."})
                continue

            # 3. Auto-correct bare tool name as action
            if action in self.tools:
                decision.update({"tool": action, "action": "tool_call"})
                action = "tool_call"

            # 4. Final answer → critique → return
            if action == "final_answer":
                proposed = decision.get("answer", "None")
                final, critique = self._critique_answer(question, proposed, tool_history)
                trace.append({"step": "FINAL", "state": "CRITIQUE & FIX",
                              "original_answer": proposed, "critique": critique, "fixed_answer": final})
                return {"question": question, "answer": final,
                        "citations": self._build_citations(trace, decision),
                        "trace": trace, "steps_used": tool_call_count}

            # 5. Tool call
            elif action == "tool_call":
                t_name, t_input = decision.get("tool", ""), decision.get("input", "")
                if t_name not in self.tools:
                    tool_history.append({"tool": t_name, "input": t_input,
                                         "result_summary": f"ERROR: Unknown tool '{t_name}'"})
                    continue
                call_key = (t_name, t_input.strip().lower())
                if call_key in seen_calls:
                    tool_history.append({"tool": t_name, "input": t_input,
                                         "result_summary": "BLOCKED: Duplicate. Try different query."})
                    continue
                seen_calls.add(call_key)
                if tool_call_count >= self.MAX_STEPS:
                    break
                tool_call_count += 1
                t0 = time.time()
                result, err = self._run_tool_with_retry(t_name, t_input)
                elapsed = round((time.time() - t0) * 1000)
                self.telemetry[t_name]["calls"] += 1
                self.telemetry[t_name]["ms"] += elapsed
                print(f"  Step {tool_call_count}: tool={t_name} ({elapsed}ms) input='{t_input}'")
                if err:
                    tool_history.append({"tool": t_name, "input": t_input,
                                         "result_summary": f"ERROR: {err}. Try different tool."})
                    continue
                trace.append({"step": step, "state": "ACT", "tool": t_name,
                              "input": t_input, "result": str(result)[:300]})
                tool_history.append({"tool": t_name, "input": t_input,
                                     "result_summary": str(result)[:1000]})
            else:
                trace.append({"step": step, "state": "ERROR", "error": f"Unknown action: {action}"})

        raise RuntimeError(f"Agent exceeded {self.MAX_STEPS} tool calls. "
                           f"Used {tool_call_count}/{self.MAX_STEPS} across {step} iterations.")

def print_trace(result: dict, agent: F1Agent = None):
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
        elif state == "CRITIQUE & FIX":
            print(f"  [CRITIQUE & FIX] Original: {entry.get('original_answer')[:100]}...")
            print(f"                   Critique: {entry.get('critique')}")
    print("-" * 70)
    print(f"Final Answer: {result['answer']}")
    print(f"Citations: {result['citations']}")
    print(f"Steps used: {result['steps_used']} / {F1Agent.MAX_STEPS} max")
    if agent:
        print(f"\n  {'Tool':<15} {'Calls':>6} {'Avg ms':>8}")
        print(f"  {'-'*32}")
        for tool, stats in agent.telemetry.items():
            avg = stats['ms'] // stats['calls'] if stats['calls'] else 0
            print(f"  {tool:<15} {stats['calls']:>6} {avg:>7}ms")
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
        try:
            result = agent.run(question)
        except RuntimeError as e:
            result = {"question": question, "answer": f"REFUSAL: {e}", 
                      "citations": "None", "trace": [], "steps_used": F1Agent.MAX_STEPS}
        print_trace(result, agent)
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
            try:
                result = agent.run(question)
            except RuntimeError as e:
                result = {"question": question, "answer": f"REFUSAL: {e}", 
                          "citations": "None", "trace": [], "steps_used": F1Agent.MAX_STEPS}
            print_trace(result, agent)


if __name__ == "__main__":
    main()
