"""Tool 2: Semantic search over F1 documents using ChromaDB + Gemini Embeddings."""
import os

import chromadb
import google.generativeai as genai
from dotenv import load_dotenv
from tools.base import BaseTool

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

VECTOR_STORE_PATH = "data/vector_store"
COLLECTION_NAME = "f1_documents"


class SearchDocsTool(BaseTool):
    """Searches embedded F1 documents for relevant passages."""

    @property
    def name(self) -> str:
        return "search_docs"

    @property
    def description(self) -> str:
        return (
            "Search F1 articles and analysis documents. Use for qualitative "
            "questions: reasons, explanations, opinions, team strategies, "
            "season narratives, driver quotes."
        )

    def run(self, query: str, n_results: int = 3) -> str:
        if not os.path.exists(VECTOR_STORE_PATH):
            return "ERROR: Vector store not found. Run 'python -m indexing.embed_docs' first."

        try:
            # Step 1: Embed the query
            embedding_result = genai.embed_content(
                model="models/gemini-embedding-exp-03-07",
                content=query,
                task_type="retrieval_query",
            )
            query_embedding = embedding_result["embedding"]

            # Step 2: Search ChromaDB
            client = chromadb.PersistentClient(path=VECTOR_STORE_PATH)
            collection = client.get_collection(COLLECTION_NAME)
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
            )

            if not results["documents"] or not results["documents"][0]:
                return "No relevant documents found."

            # Step 3: Format results with citations
            output_parts = []
            for i, (doc, meta) in enumerate(
                zip(results["documents"][0], results["metadatas"][0])
            ):
                source = meta.get("source", "unknown")
                chunk_id = meta.get("chunk_id", "?")
                output_parts.append(
                    f"[Source: {source} | Chunk: {chunk_id}]\n{doc.strip()}"
                )

            return "\n\n---\n\n".join(output_parts)

        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
