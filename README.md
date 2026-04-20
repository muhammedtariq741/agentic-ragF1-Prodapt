# F1 Agentic RAG 🏎️💨

> An intelligent, autonomous Agentic Retrieval-Augmented Generation (RAG) system designed to answer complex Formula 1 questions across the 2023–2024 seasons. 

Unlike standard RAG pipelines, this system features a custom reasoning loop that autonomously orchestrates three distinct tools—navigating qualitative race documents, querying hard numerical databases, and searching the live web to synthesize highly accurate F1 answers.

---

## 🧠 Core Architecture & Data Corpus

Our agent makes decisions based on three curated F1 data pipelines:

1. **Unstructured Knowledge Base (ChromaDB)**
   - Ingests qualitative F1 season review articles and race analysis pieces (e.g., *sky sports, bbc sport, formula1.com*).
   - Designed to answer the "why" and "how" behind race strategies, season narratives, and driver drama.

2. **Structured Database (SQLite)**
   - A highly queried local database containing comprehensive race results for the 2023 & 2024 seasons (919 rows, 17 columns) via the Jolpica F1 API.
   - Designed to answer hard statistical queries regarding lap counts, finish times, and championship standings.

3. **Live Web Search (Tavily API)**
   - Designed strictly as a fallback engine and live-data pipeline for breaking real-time news, ensuring the agent is always up-to-date with immediate driver updates and rumors.

---

## 🛠️ Technology Stack

| Architecture Component | Technology Stack |
| :--- | :--- |
| **Generative LLM** | Google Gemini 2.0 Flash Lite |
| **Text Embeddings** | Gemini Embedded Models (`models/gemini-embedding-001`) |
| **Vector Store** | ChromaDB (Local persistent instance) |
| **Structured Data** | SQLite (Generated via Pandas) |
| **Web Search** | Tavily Web Search API |
| **Agent Orchestration** | Custom Python implementation (Zero framework lock-in) |

---

## 🚀 Getting Started

Follow these instructions to get your local F1 RAG agent up and running in minutes.

### 1. Installation
Clone the repository and spin up your virtual environment:
```bash
git clone <repo-url>
cd agentic-ragF1-Prodapt
python -m venv venv

# Activate the environment (Windows)
.\venv\Scripts\activate  

# Install dependencies
pip install -r requirements.txt
```

### 2. Environment Configuration
Duplicate the environment template and securely configure your API credentials.
```bash
cp .env.example .env
```
*Note: Open `.env` and insert your actual `GEMINI_API_KEY` and `TAVILY_API_KEY`.*

### 3. Data Ingestion Pipeline
Fetch the pristine numerical results from the Jolpica API and compile your SQLite database:
```bash
python -m indexing.fetch_f1_data
python -m indexing.load_data
```

### 4. Document Ingestion
To populate the vector store, place raw F1 race review articles (`.txt` files) into the `data/documents/` directory. *(See `data/documents/README.txt` for exact guidance).*

Once your articles are placed, chunk and embed them into the local ChromaDB instance:
```bash
python -m indexing.embed_docs
```

---

## 🎯 Usage & Evaluation

Initialize the agent to answer varying levels of complex questions.

**Single-Tool Statistics Query**
```bash
python main.py "How many races did Max Verstappen win in 2023?"
```

**Multi-Tool Reasoning Query**
```bash
python main.py "How did Verstappen's win count change from 2023 to 2024, and what reasons did pundits give for McLaren's sudden pace improvement?"
```

**Interactive Chat Console**
```bash
python main.py --interactive
```

**Performance Evaluation Suite**
```bash
python -m evaluation.run_eval
```
