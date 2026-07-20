"""
ingest_papers.py — one-time (or re-runnable) ingestion of exam papers into
ChromaDB for MAROS's RAG pipeline. Walks exam_papers/, extracts text from
PDFs (preferring PDF over DOCX when both exist for the same base name),
chunks it, embeds with the same model rag.py queries with, and writes into
the same collection/persist path rag.py reads from.

Run manually whenever you add new papers:
    python3 ingest_papers.py
"""

import os
import re
from pathlib import Path

import fitz  # PyMuPDF — already a MAROS dependency
import chromadb
from sentence_transformers import SentenceTransformer

# ── Config — MUST match rag.py's defaults exactly ──────────────────────
PERSIST_DIR     = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "vnit_exam_rag")
PAPERS_ROOT     = Path("exam_papers")

CHUNK_SIZE    = 1200   # chars per chunk — small enough for focused retrieval
CHUNK_OVERLAP = 200    # keep context across chunk boundaries

ALLOWED_DOC_TYPES = {"year_paper", "solutions", "topic_notes", "unknown"}


# ─────────────────────────────────────────────
# DOC TYPE INFERENCE — heuristic, based on filename
# ─────────────────────────────────────────────

def infer_doc_type(filename: str) -> str:
    name = filename.lower()
    if any(k in name for k in ("solution", "soln", "answers", "marks")):
        return "solutions"
    if any(k in name for k in ("quiz", "practise", "practice", "discussion", "exercise")):
        return "topic_notes"
    if any(k in name for k in ("exam", "sem", "midterm", "mid term", "mid-term", "ese", "reexam", "moderation")):
        return "year_paper"
    return "unknown"


# ─────────────────────────────────────────────
# TEXT EXTRACTION
# ─────────────────────────────────────────────

def extract_pdf_text(path: Path) -> str:
    try:
        doc = fitz.open(str(path))
        text = "\n".join(page.get_text("text") for page in doc)
        doc.close()
        return text
    except Exception as e:
        print(f"[ingest] PDF extraction failed for {path.name}: {e}")
        return ""


def extract_docx_text(path: Path) -> str:
    try:
        from docx import Document  # python-docx
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        print(f"[ingest] DOCX extraction failed for {path.name}: {e}")
        return ""


# ─────────────────────────────────────────────
# CHUNKING — simple sliding window on chars, snapped to paragraph breaks
# ─────────────────────────────────────────────

def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        # Try to snap the end to a paragraph or sentence boundary for cleaner chunks
        if end < len(text):
            snap = text.rfind("\n\n", start, end)
            if snap == -1:
                snap = text.rfind(". ", start, end)
            if snap != -1 and snap > start + (size // 2):
                end = snap + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap if end - overlap > start else end
    return chunks


# ─────────────────────────────────────────────
# FILE DISCOVERY — dedupe DOCX when a same-name PDF exists
# ─────────────────────────────────────────────

def collect_files(root: Path) -> list[Path]:
    all_files = list(root.rglob("*.pdf")) + list(root.rglob("*.docx"))
    pdf_stems = {f.stem.lower() for f in all_files if f.suffix.lower() == ".pdf"}

    selected = []
    for f in all_files:
        if f.name.startswith("~$"):  # skip Word lock files
            continue
        if f.suffix.lower() == ".docx" and f.stem.lower() in pdf_stems:
            continue  # PDF version of this same file already covers it
        selected.append(f)
    return selected


# ─────────────────────────────────────────────
# MAIN INGESTION
# ─────────────────────────────────────────────

def main():
    if not PAPERS_ROOT.exists():
        print(f"[ingest] {PAPERS_ROOT} not found — nothing to ingest.")
        return

    files = collect_files(PAPERS_ROOT)
    print(f"[ingest] Found {len(files)} files to process (after PDF/DOCX dedup).")

    print("[ingest] Loading embedding model...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    client = chromadb.PersistentClient(path=PERSIST_DIR)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    existing_count = collection.count()
    if existing_count > 0:
        print(f"[ingest] Collection already has {existing_count} chunks. "
              f"Clearing before re-ingesting to avoid duplicates...")
        client.delete_collection(COLLECTION_NAME)
        collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    total_chunks = 0
    skipped = 0

    for i, path in enumerate(files, 1):
        doc_type = infer_doc_type(path.name)
        if doc_type not in ALLOWED_DOC_TYPES:
            doc_type = "unknown"

        if path.suffix.lower() == ".pdf":
            text = extract_pdf_text(path)
        else:
            text = extract_docx_text(path)

        if not text.strip():
            print(f"[ingest] ({i}/{len(files)}) SKIP — no text extracted: {path.name}")
            skipped += 1
            continue

        chunks = chunk_text(text)
        if not chunks:
            skipped += 1
            continue

        embeddings = model.encode(chunks).tolist()
        ids = [f"{path.stem}_{j}" for j in range(len(chunks))]
        metadatas = [{"source": str(path), "doc_type": doc_type} for _ in chunks]

        # Chroma IDs must be globally unique — prefix with a running index too,
        # in case two files share the exact same stem in different subfolders.
        ids = [f"{i}_{cid}" for cid in ids]

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )
        total_chunks += len(chunks)
        print(f"[ingest] ({i}/{len(files)}) {path.name} → {len(chunks)} chunks [{doc_type}]")

    print(f"\n[ingest] Done — {total_chunks} chunks indexed from {len(files) - skipped} files "
          f"({skipped} skipped, no text extracted).")
    print(f"[ingest] Collection '{COLLECTION_NAME}' at '{PERSIST_DIR}' now has {collection.count()} chunks.")


if __name__ == "__main__":
    main()