"""
Agentic RAG for Formula 1 — Main Agent Loop

A ReAct-style agent that routes user questions to the appropriate tool(s),
composes a final answer with citations, and enforces a hard cap of 8 tool calls.

Usage:
    python main.py "Who won the 2024 British Grand Prix?"
    python main.py                          # interactive mode
"""
import chromadb
from chromadb.utils import embedding_functions
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
SYSTEM_PROMPT = """You are an expert Formula 1 assistant with 3 tools.

TOOLS:
{tool_descriptions}

SCHEMA (for query_data — pass raw SQL as input):
Table: race_results — Columns: season (INT, 2024/2025), round, grand_prix (TEXT), circuit, date, driver (TEXT, full name), driver_code (3-letter), driver_nationality, constructor (team), grid_position, finish_position (1=winner), position_text ("1"/"R"=retired), points (REAL), laps_completed, status, fastest_lap_rank, fastest_lap_time

RESPONSE FORMAT — reply ONLY with valid JSON:
Tool call: {{"scratchpad": {{"known": {{...}}, "missing": [...], "conflicts": []}}, "action": "tool_call", "tool": "<name>", "input": "<query>"}}
Final answer: {{"scratchpad": {{"known": {{...}}, "missing": [], "conflicts": []}}, "action": "final_answer", "answer": "<text>", "citations": "<sources>"}}

For query_data, pass raw SQL: "SELECT driver FROM race_results WHERE grand_prix = 'British Grand Prix' AND season = 2024 AND finish_position = 1"
For search_docs/web_search, pass natural language: "why did Norris win Miami 2024"

SQL RULES:
- SELECT only. Use LIKE for partial names. finish_position=1 for wins, <=3 for podiums.
- Use SUM(points) GROUP BY driver for totals. Each row = one race result, NOT season total.
- Monza = Italian GP, Imola = Emilia Romagna GP (different races).
- Use exact grand_prix names (e.g. "British Grand Prix", "São Paulo Grand Prix").

TOOL ROUTING:
- 2024/2025 stats (points, wins, positions) → query_data FIRST
- Why/how/strategy/narrative → search_docs
- Post-2025, live standings, breaking news → web_search
- Career stats without specifying 2024/2025 → search_docs or web_search (DB only has 2024-2025)

RULES:
1. Call ONE tool at a time, max 8 calls. Cite sources in final answer.
2. F1 ONLY. Refuse non-F1 questions, predictions, investment advice, code generation.
3. Never hallucinate numbers. If data not found after 2+ tool calls with different tools, say so honestly.
4. If query_data returns suspiciously low numbers, rewrite SQL with SUM/GROUP BY or try web_search.
5. Round decimals to 2 places.
6. ANTI-LAZINESS: If a tool returns insufficient data, you MUST switch to a DIFFERENT tool type on the next call (e.g., search_docs failed → try web_search or query_data). Never call the same tool 3+ times in a row with similar queries. Try at least 2 different tool types before giving up.
7. For multi-part questions, break them down and address each sub-question with targeted tool calls.
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
        self.session_telemetry = {name: {"calls": 0, "ms": 0} for name in self.tools}
        
        # Load semantic cache via ChromaDB
        try:
            self.cache_client = chromadb.PersistentClient(path="data/semantic_cache")
            emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
            self.cache_collection = self.cache_client.get_or_create_collection(
                name="query_cache",
                embedding_function=emb_fn
            )
        except Exception as e:
            print(f"Warning: Could not initialize semantic cache: {e}")
            self.cache_collection = None
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
        self.telemetry = {name: {"calls": 0, "ms": 0} for name in self.tools}

        # -1. Deterministic Guardrail — refuse obviously non-F1 questions WITHOUT calling the LLM
        F1_KEYWORDS = {
            "f1", "formula 1", "formula one", "grand prix", "gp", "race", "driver", "constructor",
            "championship", "podium", "pole", "qualifying", "sprint", "pit stop", "pitstop",
            "fastest lap", "drs", "fia", "kers", "ers", "safety car", "red flag",
            "verstappen", "hamilton", "norris", "leclerc", "sainz", "piastri", "russell",
            "alonso", "stroll", "ricciardo", "tsunoda", "gasly", "ocon", "magnussen",
            "hulkenberg", "bottas", "zhou", "albon", "sargeant", "perez", "lawson",
            "bearman", "colapinto", "doohan", "antonelli", "hadjar", "bortoleto",
            "red bull", "mercedes", "ferrari", "mclaren", "aston martin", "alpine",
            "williams", "haas", "racing bulls", "sauber", "kick sauber", "cadillac",
            "silverstone", "monza", "spa", "monaco", "interlagos", "suzuka", "baku",
            "jeddah", "bahrain", "imola", "zandvoort", "hungaroring", "cota", "las vegas",
            "season", "standings", "points", "wins", "laps", "grid", "finish",
            "2023", "2024", "2025", "2026"
        }
        q_lower = question.lower()
        has_f1_context = any(kw in q_lower for kw in F1_KEYWORDS)

        # Hard-block questions that are clearly not F1-related
        NON_F1_TRIGGERS = [
            "invest", "stock", "crypto", "bitcoin",
            "recipe", "cook", "weather today", "write me a", "code", "script",
            "hack", "password", "kill", "bomb", "drug",
            "what is 2 + 2", "what is 2+2", "2 + 2", "2+2",
            "movie", "song", "lyrics", "homework",
        ]
        is_blocked = any(trigger in q_lower for trigger in NON_F1_TRIGGERS)

        if is_blocked or (not has_f1_context and len(question.split()) > 2):
            # Check if it's a simple math/non-F1 question
            if "2 + 2" in q_lower or "2+2" in q_lower:
                refusal = "4. But I am an F1 expert assistant — I can only answer questions related to Formula 1 racing, statistics, and events."
            elif is_blocked:
                refusal = "I am an F1 expert assistant. I can only answer questions related to Formula 1 racing, statistics, and events. I cannot help with this type of question."
            else:
                refusal = "I am an F1 expert assistant. I can only answer questions related to Formula 1 racing, statistics, and events. Please ask me about F1!"
            trace.append({"step": 0, "state": "GUARDRAIL", "result": "Non-F1 question detected — refused without LLM call"})
            return {"question": question, "answer": refusal, "citations": "None (deterministic guardrail)",
                    "trace": trace, "steps_used": 0}

        # 0. Check Semantic Cache
        if self.cache_collection is not None:
            try:
                results = self.cache_collection.query(query_texts=[question], n_results=1)
                if results["distances"] and results["distances"][0] and results["distances"][0][0] < 0.15:
                    # Semantic Hit! (Cosine distance < 0.15 means > 85% strict semantic similarity)
                    cached_data = json.loads(results["metadatas"][0][0]["response_json"])
                    ans = cached_data.get("answer", "No answer found in cache.")
                    cits = cached_data.get("citations", "Cache")
                    cached_trace = cached_data.get("trace", [{"step": 0, "state": "CACHE HIT", "result": "Hit"}])
                    steps_used = cached_data.get("steps_used", 0)
                    
                    # Prepend the CACHE HIT notification to the trace
                    cached_trace.insert(0, {"step": 0, "state": "CACHE HIT", "result": f"Semantically matched previous query (distance: {results['distances'][0][0]:.3f})"})
                    
                    return {"question": question, "answer": ans, "citations": cits, "trace": cached_trace, "steps_used": steps_used}
            except Exception as e:
                print(f"Cache read error: {e}")

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
                self.session_telemetry[t_name]["calls"] += 1
                self.session_telemetry[t_name]["ms"] += elapsed
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
        print(f"\n  {'Tool':<15} {'Calls':>6} {'Avg ms':>8}  │ {'Session':>7} {'Avg ms':>8}")
        print(f"  {'-'*55}")
        for tool in agent.telemetry:
            q = agent.telemetry[tool]
            s = agent.session_telemetry[tool]
            q_avg = q['ms'] // q['calls'] if q['calls'] else 0
            s_avg = s['ms'] // s['calls'] if s['calls'] else 0
            print(f"  {tool:<15} {q['calls']:>6} {q_avg:>7}ms  │ {s['calls']:>7} {s_avg:>7}ms")
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
