"""Embed F1 documents into ChromaDB vector store."""
import os

import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

load_dotenv()

DOCS_DIR = "data/documents"
VECTOR_STORE_PATH = "data/vector_store"
COLLECTION_NAME = "f1_documents"
CHUNK_SIZE = 500  # characters per chunk
CHUNK_OVERLAP = 50


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if c.strip()]


def embed_documents():
    """Read all .txt files from DOCS_DIR, chunk, embed, and store in ChromaDB."""
    if not os.path.exists(DOCS_DIR):
        print(f"Documents directory not found: {DOCS_DIR}")
        return

    txt_files = [f for f in os.listdir(DOCS_DIR) if f.endswith(".txt")]
    if not txt_files:
        print(f"No .txt files found in {DOCS_DIR}")
        return

    print(f"Found {len(txt_files)} document(s) to embed.")

    # Prepare chunks
    all_chunks = []
    all_metadata = []
    all_ids = []

    for filename in txt_files:
        filepath = os.path.join(DOCS_DIR, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()

        chunks = chunk_text(text)
        print(f"  {filename}: {len(chunks)} chunks")

        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_metadata.append({"source": filename, "chunk_id": i})
            all_ids.append(f"{filename}_{i}")

    # Embed all chunks
    print(f"\nEmbedding {len(all_chunks)} chunks...")
    # ChromaDB will automatically embed these locally using sentence-transformers!

    # Store in ChromaDB
    print(f"\nStoring in ChromaDB at {VECTOR_STORE_PATH}...")
    client = chromadb.PersistentClient(path=VECTOR_STORE_PATH)

    # Delete existing collection if it exists
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=emb_fn
    )
    collection.add(
        documents=all_chunks,
        metadatas=all_metadata,
        ids=all_ids,
    )

    print(f"[OK] Stored {len(all_chunks)} chunks in collection '{COLLECTION_NAME}'")


if __name__ == "__main__":
    embed_documents()
