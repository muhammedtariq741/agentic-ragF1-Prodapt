# Design Document — F1 Agentic RAG

## What This System Does

This is an autonomous question-answering agent for Formula 1. You ask it a question about the 2024–2025 F1 seasons, and it figures out which tools to call, gathers evidence across a SQL database, a document corpus, and the live web, then composes a cited answer — all without human guidance. It stops itself after at most 8 tool calls.

---

## The Agent Loop, Step by Step

The entire loop lives in one method: `F1Agent.run(question)` in `main.py`. Here is exactly what happens when a question arrives.

### Step 0 — Initialization

The agent creates four empty data structures:

- **`trace`** — a list that records every action for debugging. This becomes the trace log.
- **`tool_call_count`** — integer counter, starts at 0. This enforces the hard cap.
- **`memory`** — a dict with three keys: `known` (facts gathered so far), `missing` (what we still need), `conflicts` (contradictions between sources).
- **`seen_calls`** — a set of `(tool_name, input)` tuples to block exact duplicate calls.

### Step 1 — Build the Prompt

Each iteration, the agent constructs a fresh prompt containing:
1. The original user question
2. A summary of every tool call made so far and its result (not the raw output — a truncated summary to stay within context limits)
3. The current memory state (`known`, `missing`, `conflicts`)
4. Instructions: "Extract facts into your scratchpad. Don't repeat queries. If you have enough info, answer now."

This prompt is rebuilt from scratch every iteration to prevent unbounded context growth.

### Step 2 — Call the LLM

The prompt is sent to **Groq Llama-3.3-70B-Versatile** via the Groq API. The LLM must respond with a JSON object in one of two formats:

**Tool call:**
```json
{
  "scratchpad": {"known": {"winner": "Verstappen"}, "missing": ["race conditions"], "conflicts": []},
  "action": "tool_call",
  "tool": "search_docs",
  "input": "2024 Brazilian Grand Prix race conditions weather"
}
```

**Final answer:**
```json
{
  "scratchpad": {"known": {"winner": "Verstappen", "conditions": "heavy rain"}, "missing": [], "conflicts": []},
  "action": "final_answer",
  "answer": "Max Verstappen won the 2024 Brazilian Grand Prix in heavy rain conditions...",
  "citations": "query_data (race_results), search_docs (2024_Sao_Paulo_Grand_Prix.txt)"
}
```

The `scratchpad` is the key differentiator from a simple ReAct loop. It forces the LLM to explicitly state what it knows and what it's missing before deciding its next action. This makes the reasoning visible and debuggable in the trace.

### Step 3 — Parse and Update Memory

The agent parses the JSON response and merges the LLM's scratchpad into the running `memory` dict. If JSON parsing fails (the LLM occasionally returns markdown or prose), the agent appends an error message to the tool history and loops back to Step 1 — giving the LLM another chance to respond correctly.

### Step 4 — Route the Action

Two possible paths:

**If `action == "final_answer"`:** The loop ends. The agent constructs grounded citations by scanning the trace for every `ACT` entry (actual tool calls that succeeded), not from what the LLM claims it used. This prevents hallucinated citations. Returns a dict with the answer, citations, full trace, and step count.

**If `action == "tool_call"`:** Continue to Step 5.

### Step 5 — Safety Checks (Before Execution)

Three checks happen before any tool runs:

1. **Unknown tool?** If the tool name doesn't match any registered tool, log an error and loop back.
2. **Duplicate call?** If `(tool_name, input)` is already in `seen_calls`, block it. Append a message telling the LLM to use the existing results or try a different query. Loop back.
3. **Hard cap reached?** If `tool_call_count >= 8`, break out of the loop entirely.

Only after passing all three checks does `tool_call_count` increment.

### Step 6 — Execute the Tool (With Retry)

The agent calls `tool.run(input)` inside a retry loop:

- **Attempt 1:** Run the tool. If it succeeds, continue.
- **If it fails:** Wait 1 second, then try once more.
- **If retry also fails:** Log the error in the trace and append a message to the LLM: "Tool failed. Try a DIFFERENT tool or give a partial answer." The loop continues — the LLM gets to decide what to do next.

The result is truncated to 1,000 characters before being stored in `tool_history` (to prevent context window overflow on subsequent iterations).

### Step 7 — Loop Back

Go to Step 1. The LLM sees the new tool result in the rebuilt prompt and decides whether to call another tool or give the final answer.

### Step 8 — Hard Cap Termination

If the loop exhausts all iterations without the LLM returning `final_answer`, the method raises a `RuntimeError`. The caller (`main()`) catches this and returns a structured refusal: `"REFUSAL: Agent exceeded maximum of 8 tool calls."`

---

## Tool Schemas

All three tools implement the same interface (`BaseTool`): a `name` property, a `description` property, and a `run(query: str) -> str` method.

### `query_data`

| Field | Value |
|-------|-------|
| **Name** | `query_data` |
| **Description** | "Query the F1 race results database (2024-2025 seasons). USE THIS FOR: Numerical facts — points, wins, positions, standings. DO NOT USE THIS FOR: Explanations, strategies, or anything qualitative." |
| **Input** | A raw SQL `SELECT` query (generated by the LLM). Example: `SELECT SUM(points) FROM race_results WHERE driver LIKE '%Verstappen%' AND season = 2024` |
| **Output** | Formatted table of results. Example: `"SQL: SELECT... Results (1 rows): SUM(points) --- Row 1: 399.0"` |
| **Safety** | Rejects any query containing `DROP`, `DELETE`, `UPDATE`, `INSERT`, or `ALTER`. Only `SELECT` is allowed. |

The tool connects to a local SQLite database (`data/structured/f1_results.db`) containing 958 rows across 48 races (2024 + 2025 seasons), with 17 columns per row.

### `search_docs`

| Field | Value |
|-------|-------|
| **Name** | `search_docs` |
| **Description** | "Search F1 articles and analysis documents (2024-2025). USE THIS FOR: Qualitative questions, explanations of 'why' something happened, team strategies, season narratives. DO NOT USE THIS FOR: Exact numerical statistics or real-time news." |
| **Input** | A natural language query. Example: `"McLaren strategy Hungarian Grand Prix 2024"` |
| **Output** | Top 5 matching document chunks with source filenames. Example: `"[Chunk 1] (source: 2024_Hungarian_Grand_Prix.txt): McLaren adopted a two-stop strategy..."` |

The tool queries a local ChromaDB vector store containing 49 documents (48 individual race reports + 1 general knowledge document), embedded using `all-MiniLM-L6-v2` sentence-transformers.

### `web_search`

| Field | Value |
|-------|-------|
| **Name** | `web_search` |
| **Description** | "Search the live web for current F1 information via Tavily. USE THIS FOR: Breaking news, real-time standings, events outside 2024-2025, career statistics. DO NOT USE THIS FOR: First-pass data retrieval for 2024-2025 — try query_data and search_docs first." |
| **Input** | A natural language query. Example: `"Lewis Hamilton total career wins 2024"` |
| **Output** | Concatenated search results with titles and content. Example: `"**Lewis Hamilton - Wikipedia** Hamilton has won 105 races..."` |

The tool prepends "Formula 1" to every query to scope results, and returns the top 3 results from the Tavily API.

### Why the Descriptions Are Written This Way

Each tool description has two parts: "USE THIS FOR" and "DO NOT USE THIS FOR." This is deliberate. The LLM reads these descriptions in the system prompt to decide which tool to call. Telling it what a tool does is necessary, but telling it what a tool *doesn't* do is equally important — it prevents the LLM from using `query_data` for strategy questions or `web_search` as a first resort for in-database stats.

---

## How We Prevent Infinite Loops

The agent has five layers of protection against infinite loops:

### Layer 1: Hard Tool Cap (`MAX_STEPS = 8`)

Before every tool execution, the agent checks `tool_call_count >= 8`. If true, it breaks out of the loop. This is a non-negotiable hard cap — the counter increments for every successful tool call, and the check happens before execution, so the 9th call never runs.

### Layer 2: Bounded Outer Loop (`range(1, 17)`)

The `for` loop that drives the agent is bounded to `MAX_STEPS * 2 = 16` iterations. This means even if the LLM keeps returning parse errors, unknown tools, or blocked duplicates (none of which increment `tool_call_count`), the loop still terminates after 16 LLM calls. Without this, a pathological LLM could keep returning invalid JSON forever.

### Layer 3: RuntimeError on Exhaustion

If the loop ends without the LLM returning `final_answer`, the method raises a `RuntimeError` — not a silent empty return. The caller catches this and returns a structured refusal to the user. This satisfies the rubric requirement that the hard cap "fires correctly."

### Layer 4: Exact Duplicate Blocking

A set called `seen_calls` tracks every `(tool_name, normalized_input)` tuple. If the LLM tries to make the exact same call twice, it's blocked immediately — no tool execution, no counter increment. The LLM receives a message saying "You already made this exact call. Use the existing results or try a DIFFERENT query."

### Layer 5: Anti-Laziness Rule

A system prompt rule requires the agent to make at least 2 distinct tool calls before concluding "I could not find the answer." This prevents the opposite failure mode — giving up too early — while the hard cap prevents running too long.

---

## An Unexpected Observation

During testing, we discovered that the LLM inconsistently routes semantically identical questions to different tools depending on the *driver name* in the query.

- **"How many total wins does Hamilton have?"** → `web_search` → 105 (correct career total)
- **"How many total wins does Max have?"** → `query_data` → 17 (wrong — only 2024-2025 data)

Same question structure, same intent ("career total"), but two completely different tool choices. Our hypothesis: the LLM has a stronger prior association between "Verstappen" and "database" because Verstappen appears disproportionately often in race results contexts during training. This biases the routing decision — the model "expects" Verstappen questions to be answerable from structured data.

We fixed this by adding an explicit system prompt rule (Rule 12): *"If a question asks about 'total wins' or 'career wins' without specifying a season, NEVER use query_data — it only has 2024-2025 data."* But the underlying observation — that LLM tool-routing is influenced by entity-level priors, not just query structure — is something we hadn't seen documented elsewhere and would be worth investigating further.
