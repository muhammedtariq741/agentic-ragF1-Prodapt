# F1 Agentic RAG

An agentic RAG system that answers Formula 1 questions (2023-2024 seasons) by reasoning across three data sources: race documents, structured results data, and live web search.

## Corpus

This agent relies on the following data sources for Formula 1 information:

- **Unstructured:** F1 season review articles and race analysis (formula1.com, Sky Sports, BBC Sport)
- **Structured:** Race results for 2023-2024 seasons (919 rows, 17 columns) via the Jolpica F1 API
- **Web:** Live Tavily search for current standings, news, and driver updates

## Setup

```bash
# 1. Clone and set up
git clone <repo-url> && cd agentic-ragF1-Prodapt
python -m venv venv
.\venv\Scripts\activate  # Windows
pip install -r requirements.txt

# 2. Configure API keys
cp .env.example .env
# Edit .env with your actual keys

# 3. Fetch F1 data and build the database
python -m indexing.fetch_f1_data
python -m indexing.load_data

# 4. Add F1 article documents to data/documents/ (see data/documents/README.txt)

# 5. Index documents into vector store
python -m indexing.embed_docs
```

## Usage

```bash
# Single question
python main.py "How many races did Verstappen win in 2023?"

# Multi-tool question
python main.py "How did Verstappen's win count change from 2023 to 2024, and what reasons did analysts give?"

# Interactive mode
python main.py --interactive

# Run full evaluation
python -m evaluation.run_eval
```

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Google Gemini 2.0 Flash Lite |
| Embeddings | Gemini Embedding |
| Vector Store | ChromaDB (local) |
| Structured Data | SQLite via pandas |
| Web Search | Tavily API |
| Agent Loop | Custom Python (no frameworks) |
