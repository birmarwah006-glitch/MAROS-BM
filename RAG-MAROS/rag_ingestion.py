"""
ingest.py — the missing half of rag.py.

rag.py only QUERIES ChromaDB (query_rag / build_rag_context / get_rag_stats).
Nothing in the MAROS codebase WRITES to it. This script is that missing half:
it walks your organized folder structure, extracts text from PDFs/DOCX,
chunks it, embeds it with the same model rag.py queries with, and writes it
into the same ChromaDB collection rag.py reads from.

Run it once to populate the collection, then rag.py's query functions have
something to actually retrieve.

    python ingest.py --root /path/to/your/organized/folders
    python ingest.py --root ./sample_docs --dry-run   # see what would be ingested, no writes

FOLDER -> DOC_TYPE MAPPING
---------------------------
rag.py's query_rag() filters on doc_type, and only accepts:
    year_paper | solutions | topic_notes | unknown
Your folder names (classified, midterm, os questions bank, seasonal, etc.)
don't match those directly, so FOLDER_DOC_TYPE below maps each of your real
folder names to one of the four allowed types. EDIT THIS MAPPING — I've
guessed at the obvious ones; the folders I couldn't confidently classify
default to "unknown" so nothing gets silently mis-tagged. In particular
"classified" and "QN" are ambiguous from the name alone — check them.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# FOLDER -> DOC_TYPE MAPPING — EDIT THIS to match what's actually inside
# each folder. Keys are matched case-insensitively against folder names
# anywhere in the path (so "endsem" matches a folder literally named that).
# ---------------------------------------------------------------------------
FOLDER_DOC_TYPE = {
    "endsem":                   "year_paper",
    "midterm":                  "year_paper",
    "re-exam":                  "year_paper",
    "reexam":                   "year_paper",
    "seasonal":                 "year_paper",   # sessionals (CT1/CT2/TS1)
    "os questions bank":        "year_paper",
    "pactise questions":        "year_paper",   # practice questions
    "practice questions":       "year_paper",
    "qn":                       "year_paper",   # discussion / shortlisted questions
    "quizes":                   "year_paper",   # quiz questions — real OS assessment items
    "quizzes":                  "year_paper",
    "classified":               "year_paper",   # same "OS Questions bank" content, duplicated
    "concepts":                 "topic_notes",  # OSTEP textbook lives here
    "tpoic vise concept:pdfs":  "topic_notes",  # macOS shows the folder's "/" as ":"
    "tpoic vise concept/pdfs":  "topic_notes",  # in case the path uses a real slash
    "tpoic vise concept":       "topic_notes",
    "topic wise concept":       "topic_notes",
    "solutions":                "solutions",
}

ALLOWED_DOC_TYPES = {"year_paper", "solutions", "topic_notes", "unknown"}
SUPPORTED_EXT = {".pdf", ".docx", ".txt", ".md"}

CHUNK_WORDS   = 300   # words per chunk
CHUNK_OVERLAP = 50    # words of overlap between consecutive chunks


def classify_doc_type(file_path: Path, root: Path) -> str:
    """Walk the path components between root and the file, match each folder
    name (lowercased) against FOLDER_DOC_TYPE. First match wins, closest
    folder to the file takes priority. Falls back to 'unknown'."""
    try:
        rel_parts = file_path.relative_to(root).parts[:-1]  # folders only, not filename
    except ValueError:
        rel_parts = file_path.parts[:-1]

    for part in reversed(rel_parts):   # closest folder to the file first
        key = part.strip().lower()
        if key in FOLDER_DOC_TYPE:
            return FOLDER_DOC_TYPE[key]
    return "unknown"


def extract_text(file_path: Path) -> str:
    """Extract plain text from a PDF, DOCX, TXT, or MD file."""
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        import fitz
        doc = fitz.open(str(file_path))
        pages = [page.get_text("text") for page in doc]
        doc.close()
        return "\n".join(pages)
    elif ext == ".docx":
        import docx
        d = docx.Document(str(file_path))
        return "\n".join(p.text for p in d.paragraphs)
    elif ext in (".txt", ".md"):
        return file_path.read_text(encoding="utf-8", errors="ignore")
    else:
        raise ValueError(f"Unsupported extension: {ext}")


def chunk_text(text: str, chunk_words: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Simple word-count chunking with overlap. Good enough for exam-paper-
    length documents; swap for a smarter splitter later if needed."""
    words = text.split()
    if not words:
        return []
    chunks = []
    step = max(1, chunk_words - overlap)
    for start in range(0, len(words), step):
        chunk = " ".join(words[start:start + chunk_words])
        if chunk.strip():
            chunks.append(chunk)
        if start + chunk_words >= len(words):
            break
    return chunks


def find_documents(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXT]


def ingest(root: str, dry_run: bool = False, verbose: bool = True) -> dict:
    root = Path(root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Root folder not found: {root}")

    files = find_documents(root)
    if verbose:
        print(f"[ingest] found {len(files)} supported file(s) under {root}\n")

    plan = []  # (file_path, doc_type, n_chunks)
    all_chunks, all_metas, all_ids = [], [], []

    for f in files:
        doc_type = classify_doc_type(f, root)
        try:
            text = extract_text(f)
        except Exception as e:
            print(f"[ingest] SKIP (extract failed) {f}: {e}")
            continue

        chunks = chunk_text(text)
        plan.append((f, doc_type, len(chunks)))

        for i, chunk in enumerate(chunks):
            chunk_id = hashlib.sha1(f"{f}:{i}".encode()).hexdigest()[:16]
            all_chunks.append(chunk)
            all_metas.append({
                "source": str(f.relative_to(root)),
                "doc_type": doc_type,
            })
            all_ids.append(chunk_id)

    if verbose:
        by_type: dict[str, int] = {}
        for _, dt, n in plan:
            by_type[dt] = by_type.get(dt, 0) + 1
        print("[ingest] doc_type breakdown (file counts):")
        for dt, n in sorted(by_type.items()):
            flag = "" if dt in ALLOWED_DOC_TYPES else "  <-- NOT in rag.py's ALLOWED_DOC_TYPES, will be invisible to queries!"
            print(f"  {dt:<14}{n:>4} files{flag}")
        print(f"\n[ingest] {len(all_chunks)} total chunks prepared\n")

        print("[ingest] per-file plan:")
        for f, dt, n in plan:
            print(f"  [{dt:<12}] {n:>3} chunks  <-  {f.relative_to(root)}")

    if dry_run:
        print("\n[ingest] --dry-run: nothing written to ChromaDB.")
        return {"files": len(plan), "chunks": len(all_chunks), "written": False}

    if not all_chunks:
        print("[ingest] nothing to write — 0 chunks extracted.")
        return {"files": len(plan), "chunks": 0, "written": False}

    # ── Embed + write, using the SAME model + collection rag.py reads from ──
    import chromadb
    from sentence_transformers import SentenceTransformer

    print("\n[ingest] loading embedding model (all-MiniLM-L6-v2)...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    persist_dir     = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
    collection_name = os.getenv("COLLECTION_NAME", "vnit_exam_rag")
    client = chromadb.PersistentClient(path=persist_dir)
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    print(f"[ingest] embedding {len(all_chunks)} chunks...")
    embeddings = model.encode(all_chunks, show_progress_bar=verbose).tolist()

    # Batch in groups of 500 — Chroma has a max batch size on some backends.
    BATCH = 500
    for i in range(0, len(all_chunks), BATCH):
        collection.upsert(
            ids=all_ids[i:i + BATCH],
            embeddings=embeddings[i:i + BATCH],
            documents=all_chunks[i:i + BATCH],
            metadatas=all_metas[i:i + BATCH],
        )

    print(f"\n[ingest] done. Collection '{collection_name}' now has {collection.count()} chunks total "
          f"(persisted at {persist_dir}).")
    return {"files": len(plan), "chunks": len(all_chunks), "written": True}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, help="Path to your organized folder structure")
    ap.add_argument("--dry-run", action="store_true", help="Show the ingestion plan without writing to ChromaDB")
    args = ap.parse_args()
    ingest(args.root, dry_run=args.dry_run)