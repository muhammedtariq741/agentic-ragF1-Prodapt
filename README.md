# F1 Agentic RAG 

> Ask any question about the 2024–2025 Formula 1 seasons — stats, strategies, race narratives, or live news — and an autonomous AI agent decides which tools to call, gathers the evidence, and composes a cited answer. All in ≤ 8 steps.

```
🏎️  Ask an F1 question: How many podiums did Hamilton get in 2024?

  Step 1: tool=query_data  input='SELECT COUNT(*) FROM race_results WHERE driver LIKE '%Hamilton%' AND finish_position <= 3 AND season = 2024'
  Step 2: tool=search_docs input='Hamilton 2024 Silverstone British Grand Prix'

Final Answer: Lewis Hamilton achieved 5 podium finishes in 2024. His British GP
win at Silverstone was his first victory in nearly three years and set the record
for most wins at a single circuit (9).
Citations: query_data (race_results), search_docs (2024_British_Grand_Prix.txt)
Steps used: 2 / 8 max
```

---

## Quick Start

**One command does everything** — creates the venv, installs deps, prompts for API keys, builds the vector store, and launches the agent:

```bash
git clone https://github.com/muhammedtariq741/agentic-ragF1-Prodapt.git
cd agentic-ragF1-Prodapt
python3 run.py
```

> You need two free API keys (30 seconds each):
> - **Groq** → https://console.groq.com (LLM inference — free tier)
> - **Tavily** → https://tavily.com (web search — free tier)

### Manual Setup (if you prefer)

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                               # Add your GROQ_API_KEY and TAVILY_API_KEY
python -m indexing.embed_docs                       # Build ChromaDB vector store
python main.py                                      # Launch interactive mode
```

---

## Usage

```bash
# Interactive chat
python main.py

# Single question
python main.py "How many races did Lando Norris win in 2025?"

# Run the 20-question evaluation suite
python -m evaluation.run_eval
```

---

## Architecture

The system uses a **Plan + Scratchpad + Reflection** agent loop — not a black-box framework call. Every decision is made by code we wrote and can explain line by line.

```
                         ┌─────────────────┐
                         │  User Question  │
                         └────────┬────────┘
                                  ▼
                    ┌─────────────────────────────┐
                    │  LLM Reasoning (Groq 70B)   │
                    │  ┌───────────────────────┐  │
                    │  │ Scratchpad:            │  │
                    │  │  known: {...}          │  │
                    │  │  missing: [...]        │  │
                    │  │  conflicts: [...]      │  │
                    │  └───────────────────────┘  │
                    └─────────┬───────────────────┘
                              │ JSON decision
                    ┌─────────▼─────────┐
                    │   Route Action    │
                    └──┬──────┬──────┬──┘
                       │      │      │
              ┌────────▼┐ ┌───▼────┐ ┌▼─────────┐
              │query_data│ │search_ │ │web_search│
              │  (SQL)   │ │ docs   │ │ (Tavily) │
              │  SQLite  │ │ChromaDB│ │  Live    │
              └────────┬┘ └───┬────┘ └┬─────────┘
                       │      │       │
                       └──────▼───────┘
                         Tool Result
                              │
                    ┌─────────▼─────────┐
                    │ Reflect: enough?  │──No──→ Loop (≤8 tool calls)
                    └─────────┬─────────┘
                              │ Yes
                    ┌─────────▼─────────┐
                    │   Final Answer    │
                    │   + Citations     │
                    └───────────────────┘
```

### The Agent Loop (`main.py` → `F1Agent.run()`)

1. **Receives** a user question
2. **Builds** a context prompt with the scratchpad memory and all prior tool results
3. **Calls the LLM** (Groq Llama-3.3-70B) to decide: which tool to call, or return `final_answer`
4. **Executes** the chosen tool with retry-once error handling
5. **Reflects** on the result — updates `known` facts, `missing` gaps, and `conflicts`
6. **Repeats** steps 2–5, up to a hard cap of **8 tool calls** (enforced via `RuntimeError`)
7. **Composes** the final answer with grounded citations from actual tool calls

### Three Tools

| Tool | Purpose | Data Source | When to Use |
|------|---------|-------------|-------------|
| `query_data` | SQL queries over race results | SQLite (958 rows, 48 races) | Points, wins, positions, standings |
| `search_docs` | Semantic search over race reports | ChromaDB (49 documents) | Strategy, narratives, "why/how" |
| `web_search` | Live web search | Tavily API | Breaking news, career stats, post-2025 |

Each tool implements `BaseTool` (name, description, `run()`) and does **one thing well**. The LLM-facing descriptions tell the model exactly when to use each tool — and when *not* to.

---

## Data Corpus

| Source | Content | Size |
|--------|---------|------|
| **SQLite DB** | Race-by-race results (2024 + 2025 seasons) via Jolpica F1 API | 958 rows × 17 columns |
| **ChromaDB** | 48 individual race documents (Wikipedia) + 1 general knowledge doc | 49 `.txt` files |
| **Web** | Live fallback for career stats, breaking news, post-2025 data | On-demand via Tavily |

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| **Reasoning LLM** | Groq — Llama-3.3-70B-Versatile |
| **Embeddings** | `all-MiniLM-L6-v2` (sentence-transformers) |
| **Vector Store** | ChromaDB (local persistent) |
| **Structured Data** | SQLite + Pandas |
| **Web Search** | Tavily API |
| **Agent Loop** | Custom Python (zero framework lock-in) |

---

## Project Structure

```
├── main.py                    # Agent loop + CLI entry point (360 lines)
├── run.py                     # One-click setup & launcher
├── tools/
│   ├── base.py                # BaseTool ABC
│   ├── query_data.py          # SQL tool (SQLite)
│   ├── search_docs.py         # Semantic search tool (ChromaDB)
│   └── web_search.py          # Web search tool (Tavily)
├── utils/
│   └── llm.py                 # Groq LLM caller with retry logic
├── indexing/
│   ├── fetch_f1_data.py       # Jolpica API → CSV → SQLite pipeline
│   ├── embed_docs.py          # Document chunking → ChromaDB embedding
│   └── load_data.py           # CSV → SQLite loader
├── evaluation/
│   ├── run_eval.py            # 20-question automated test suite
│   ├── eval_questions.json    # Test cases (4 categories)
│   └── eval_report.md         # Results + failure analysis
├── data/
│   ├── documents/             # 49 race report .txt files
│   └── structured/            # f1_results.db + f1_results.csv
├── requirements.txt
├── .env.example               # Template for API keys
└── .gitignore                 # Ensures .env is never committed
```

---

## Safety & Termination

| Feature | Implementation |
|---------|---------------|
| **8-step hard cap** | `MAX_STEPS = 8` — raises `RuntimeError` if exceeded |
| **Bounded loop** | Outer loop capped at `MAX_STEPS * 2` LLM iterations |
| **Duplicate blocking** | Exact `(tool, input)` dedup via `seen_calls` set |
| **Tool error handling** | Retry once → inform LLM → fallback to different tool |
| **Refusal questions** | Investment advice, predictions, opinions → polite refusal, 0 tools |
| **Out-of-scope data** | Pre-2024 questions → honest "I don't have that data" |
| **SQL injection** | DROP/DELETE/UPDATE/INSERT/ALTER blocked at tool level |
| **Secrets** | `.env` in `.gitignore` — never committed |

---

## Evaluation

**Score: 18/20 (90%)** across 4 categories:

| Category | Passed | Total |
|----------|--------|-------|
| Single-tool (query_data, search_docs) | 6 | 6 |
| Multi-tool (query_data + search_docs + web_search) | 4 | 6 |
| Refusal (out-of-scope, opinions, predictions) | 4 | 4 |
| Edge cases (empty input, trivial math, old seasons) | 4 | 4 |

See [`evaluation/eval_report.md`](evaluation/eval_report.md) for detailed results and a 4-mode failure analysis covering semantic drift, attention bias, evaluator limitations, and cross-document synthesis gaps.

---

## Interesting Observations

During testing, we noticed the agent inconsistently routes "total wins" questions. For Hamilton, it correctly used `web_search` (returning 105 career wins). For Verstappen, it used `query_data` — returning only 17 (2024-2025 wins), not his career total of ~63. The same question pattern, two different tool choices. This is a known attention bias in autoregressive models: the LLM's prior association between "Verstappen" and "database" (since he appears frequently in our local data) biased the routing decision. We fixed this by adding an explicit system prompt rule distinguishing career stats from season stats.
