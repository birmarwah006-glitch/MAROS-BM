"""
rag.py — In-process RAG retrieval for MAROS.
Extracted from the standalone RAG server (store.py).
No separate server needed — ChromaDB runs directly in the main process.
"""

import os
from typing import List, Dict

# Lazy-load heavy deps (SentenceTransformer takes ~2s to init)
_embedding_model = None
_chroma_collection = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        print("[MAROS] RAG embedding model loaded ✓")
    return _embedding_model


def _get_collection():
    global _chroma_collection
    if _chroma_collection is None:
        import chromadb
        persist_dir     = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
        collection_name = os.getenv("COLLECTION_NAME", "vnit_exam_rag")
        client = chromadb.PersistentClient(path=persist_dir)
        _chroma_collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        print(f"[MAROS] ChromaDB loaded — {_chroma_collection.count()} chunks in '{collection_name}' ✓")
    return _chroma_collection


ALLOWED_DOC_TYPES = {"year_paper", "solutions", "topic_notes", "unknown"}


def query_rag(query: str, n_results: int = 5) -> List[Dict]:
    """
    Query ChromaDB for relevant exam content.
    Returns list of {text, source, doc_type, score}.
    """
    model      = _get_embedding_model()
    collection = _get_collection()
    embedding  = model.encode([query]).tolist()[0]

    where_filter = {"doc_type": {"$in": list(ALLOWED_DOC_TYPES)}}

    try:
        results = collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        # Fallback: old chunks without doc_type
        results = collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

    retrieved = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        retrieved.append({
            "text":     doc,
            "source":   meta.get("source", "unknown"),
            "doc_type": meta.get("doc_type", "unknown"),
            "score":    round(1 - dist, 4),
        })
    return retrieved


def build_rag_context(query: str, n_results: int = 5) -> str:
    """
    Query RAG and format results as a context string for Oak.
    Returns empty string if no relevant chunks found.
    """
    chunks = query_rag(query, n_results)
    if not chunks:
        return ""

    lines = []
    for i, c in enumerate(chunks, 1):
        src = c["source"].split("/")[-1] if "/" in c["source"] else c["source"]
        lines.append(f"[Source {i}: {src} | relevance: {c['score']}]\n{c['text']}")

    return "\n\n---\n\n".join(lines)


def get_rag_stats() -> Dict:
    """Quick stats for debugging."""
    collection = _get_collection()
    return {
        "collection": collection.name,
        "total_chunks": collection.count(),
    }