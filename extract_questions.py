"""
extract_questions.py — turns raw PYQ PDFs into questions.csv.

Everything downstream (prep_mode.py's concept tagger, paper_predictor.py's
type tagger + slot ranking) expects ONE ROW PER QUESTION: q_id, year, text,
marks, exam_type. What you actually have is whole-paper PDFs. rag_ingestion.py
already solves the *document-chunk* version of this for the RAG's semantic
search (300-word passages) — this solves the *discrete-question* version,
which needs an LLM because real papers aren't formatted consistently enough
for a fixed regex to reliably find "Q3(b) [5 marks]" boundaries.

ROLE — the second thing this tags, on top of exam_type
--------------------------------------------------------
Per your split: endsem + midterm papers feed CONCEPT/SLOT ANALYSIS
(paper_predictor's ranking + backtest); the OS question bank + the midterm
papers themselves feed the PRACTICE POOL (what quiz/practice stages pull
questions from). Midterm papers are in both. Everything gets a `role`
column so downstream code filters on it instead of re-deriving it:
    role = "analysis" | "pool" | "both"
"""

from __future__ import annotations

# Load API keys from .env / maros.env automatically — don't depend on the
# shell already having them exported. override=False (dotenv default) means
# a value already exported in the current shell still wins if both exist.
try:
    from dotenv import load_dotenv
    load_dotenv(".env")
    load_dotenv("maros.env")
except ImportError:
    pass

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Optional

# FOLDER -> (role, exam_type) MAPPING — EDIT THIS to match what's actually in
# each folder, same discipline as rag_ingestion.py's FOLDER_DOC_TYPE. Folders
# not listed are SKIPPED (safer default than silently mis-tagging).
FOLDER_ROLE = {
    "endsem":              ("analysis", "endsem"),
    "midterm":              ("both",     "midsem"),
    "os questions bank":    ("pool",     "midsem"),   # EDIT if it's endsem-relevant too
    "quizes":               ("pool",     "midsem"),
    "re-exam":              ("pool",     "endsem"),   # supplementary exam, treated as endsem-equivalent — EDIT if wrong
    "seasonal":             ("pool",     "midsem"),   # sessionals (CT1/CT2/TS1)
    "qn":                   ("pool",     "midsem"),
    "pactise questions":    ("pool",     "midsem"),
    # "classified":         ("pool",     "midsem"),   # left OUT — rag_ingestion.py notes this duplicates os-questions-bank content
    # "concepts", "tpoic vise concept:pdfs": notes/textbook material, not questions — never include here
}

SUPPORTED_EXT = {".pdf", ".docx", ".txt", ".md"}

# Filenames in your folders are the year signal (17-25 == 2017-2025).
# Tries, in order: 2017-2029, then bare 17-29 as a standalone token.
_YEAR_FULL = re.compile(r"20[12]\d")
_YEAR_SHORT = re.compile(r"(?<!\d)(1[7-9]|2[0-9])(?!\d)")


def guess_year(filename: str) -> Optional[int]:
    m = _YEAR_FULL.search(filename)
    if m:
        return int(m.group())
    m = _YEAR_SHORT.search(filename)
    if m:
        return 2000 + int(m.group())
    return None


def guess_year_from_path(file_path: Path, root: Path) -> Optional[int]:
    """Try the filename first (guess_year), then fall back to checking each
    ancestor folder name up to root — covers layouts like
    endsem/2019/paper.docx where the year lives in the folder, not the file."""
    year = guess_year(file_path.name)
    if year is not None:
        return year
    try:
        parts = file_path.relative_to(root).parts[:-1]
    except ValueError:
        parts = file_path.parts[:-1]
    for part in reversed(parts):
        year = guess_year(part)
        if year is not None:
            return year
    return None


def classify_folder(file_path: Path, root: Path) -> Optional[tuple[str, str]]:
    """(role, exam_type) from the closest matching ancestor folder name, or
    None if nothing in FOLDER_ROLE matches -> file is skipped."""
    try:
        parts = file_path.relative_to(root).parts[:-1]
    except ValueError:
        parts = file_path.parts[:-1]
    for part in reversed(parts):
        hit = FOLDER_ROLE.get(part.strip().lower())
        if hit:
            return hit
    return None


def extract_text(file_path: Path) -> str:
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
    raise ValueError(f"Unsupported extension: {ext}")


# ---------------------------------------------------------------------------
# LLM SPLIT — one paper's raw text -> list of discrete questions
# ---------------------------------------------------------------------------
_SPLIT_PROMPT = """You will be given the raw extracted text of ONE Operating \
Systems exam paper (PDF text extraction, so formatting/spacing may be messy \
and there may be a header/footer/instructions block to ignore).

Split it into its individual EXAMINABLE QUESTIONS. Rules:
- Each sub-part that a student answers separately is its own question \
(e.g. Q3(a) and Q3(b) are two questions, unless they're clearly one \
inseparable prompt).
- Include the marks for each question if shown (as a plain number, no "M" \
or "marks" text). If not shown, omit the field.
- Ignore headers, instructions ("Answer any 5 of 7"), roll number lines, \
course codes, and blank OCR noise.
- Keep question text close to verbatim from the source — don't paraphrase.
- If the text doesn't look like an exam paper at all (e.g. it's a syllabus \
or notes doc), return an empty list.
- IMPORTANT: many source docs include a worked answer/solution right after \
each question (often marked "Ans:", "Solution:", "Sol:", "Answer:", or just \
following the question directly with no marker at all). If a solution IS \
present, pull it into a separate "answer" field — do NOT leave it glued \
inside "text". If no solution is present in the source, omit the "answer" \
field entirely (never invent one). Keep the answer close to verbatim too.

Reply with ONLY a JSON array, no prose, in this exact shape:
[{"marks": 5, "text": "...", "answer": "..."}, {"text": "... (no answer shown)"}]

PAPER TEXT:
\"\"\"__PAPER_TEXT__\"\"\"
"""

# Papers can be long; chunk to stay well inside context, then merge results.
_CHUNK_CHARS = 9000


def _build_split_prompt(chunk: str) -> str:
    # NOT str.format() — the template above contains literal {"..."} JSON
    # braces that .format() would misparse as fields. Plain substitution.
    return _SPLIT_PROMPT.replace("__PAPER_TEXT__", chunk)


# ---------------------------------------------------------------------------
# NO-LLM FALLBACK — regex-based question splitter for when the API is dead.
# Finds question-boundary lines like "Q1.", "Q3(a)", "2)", "Question 4:" and
# splits the paper text at each one. Noisier than the LLM split — it can
# misfire on numbered instructions/sub-bullets that aren't real questions —
# but needs ZERO API calls. Extracts marks from bracket/paren patterns like
# "[5]", "(5 Marks)", "[5M]" if present near the start of the chunk.
# ---------------------------------------------------------------------------
_Q_BOUNDARY = re.compile(
    r'(?m)^\s*(?:Q(?:uestion)?\.?\s*\.?\s*\d{1,2}\s*(?:\(?[a-zA-Z]\)?)?\s*[\.\):]?|'
    r'\d{1,2}\s*[a-zA-Z]?\s*[\.\)])\s*'
)
_MARKS_INLINE = re.compile(
    r'[\[\(]\s*(\d{1,2})\s*(?:marks?|m)\s*[\]\)]|(\d{1,2})\s*marks?\b|'
    r'[\[\(]\s*(\d{1,2})\s*[\]\)]',
    re.IGNORECASE,
)
_INSTRUCTION_LINE = re.compile(
    r'answer any|max(?:imum)? marks|time\s*:|roll\s*no|course\s*code|'
    r'total\s*marks|instructions?:|semester\s*:|date\s*:',
    re.IGNORECASE,
)
# No-LLM answer recovery: only when there's an UNAMBIGUOUS marker — this is
# deliberately conservative (misses answers with no marker) rather than
# guessing where prose ends and a solution begins without an LLM to judge it.
_ANSWER_MARKER = re.compile(
    r'\n\s*(?:Ans(?:wer)?|Sol(?:ution)?)\s*[:.\-]\s*', re.IGNORECASE
)


def heuristic_split(paper_text: str) -> list[dict]:
    text = paper_text.strip()
    if not text:
        return []
    bounds = [m.start() for m in _Q_BOUNDARY.finditer(text)]
    if not bounds:
        return []                      # nothing that looks like a question marker
    bounds.append(len(text))

    out = []
    for i in range(len(bounds) - 1):
        chunk = text[bounds[i]:bounds[i + 1]].strip()
        chunk = _Q_BOUNDARY.sub("", chunk, count=1).strip()
        if len(chunk) < 15 or _INSTRUCTION_LINE.search(chunk[:60]):
            continue
        m = _MARKS_INLINE.search(chunk)
        marks = None
        if m:
            marks = float(m.group(1) or m.group(2) or m.group(3))
        # split off a marked answer if one exists — pattern must appear past
        # the first 15 chars so a chunk that STARTS with "Ans:" (i.e. this
        # boundary already IS an answer to a previous question) isn't split
        am = _ANSWER_MARKER.search(chunk, 15)
        answer = None
        q_text = chunk
        if am:
            q_text = chunk[:am.start()].strip()
            answer = chunk[am.end():].strip() or None
        out.append({"text": q_text, "marks": marks, "answer": answer})
    return out


def _parse_split(raw: str) -> list[dict]:
    try:
        start, end = raw.index("["), raw.rindex("]") + 1
        items = json.loads(raw[start:end])
    except Exception:
        return []
    out = []
    for it in items:
        text = str(it.get("text", "")).strip()
        if not text or len(text) < 8:
            continue
        marks = it.get("marks")
        try:
            marks = float(marks) if marks not in (None, "") else None
        except (TypeError, ValueError):
            marks = None
        answer = str(it.get("answer", "")).strip() or None
        out.append({"text": text, "marks": marks, "answer": answer})
    return out


def _with_retry(llm_call: Callable[[str], str], max_retries: int = 5) -> Callable[[str], str]:
    """Wrap llm_call with exponential backoff on rate limits (429) so one
    transient cap doesn't kill an 86-file run. Cerebras/Groq both surface
    429 as requests.exceptions.HTTPError; treat ANY exception as retryable
    since default_llm_tagger already exhausts its own primary/fallback pair
    before raising — by the time we see an error here, both providers failed."""
    import time

    def _call(prompt: str) -> str:
        delay = 3.0
        last_err = None
        for attempt in range(max_retries):
            try:
                return llm_call(prompt)
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    print(f"[extract] LLM call failed ({e}); retrying in {delay:.0f}s "
                          f"(attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
        raise last_err

    return _call


def split_into_questions(
    paper_text: str,
    llm_call: Callable[[str], str],
    no_llm: bool = False,
) -> list[dict]:
    text = paper_text.strip()
    if not text:
        return []
    if no_llm:
        return heuristic_split(text)
    llm_call = _with_retry(llm_call)
    chunks = [text[i:i + _CHUNK_CHARS] for i in range(0, len(text), _CHUNK_CHARS)]
    out: list[dict] = []
    for chunk in chunks:
        prompt = _build_split_prompt(chunk)
        out.extend(_parse_split(llm_call(prompt)))
    # de-dupe near-identical splits at chunk boundaries
    seen, deduped = set(), []
    for q in out:
        key = re.sub(r"\s+", " ", q["text"].lower())[:120]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)
    return deduped


# ---------------------------------------------------------------------------
# PIPELINE
# ---------------------------------------------------------------------------
def find_documents(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXT]


def extract(
    root: str,
    out_csv: str,
    llm_call: Callable[[str], str] | None = None,
    dry_run: bool = False,
    no_llm: bool = False,
    cache_path: str = "extract_cache.json",
    verbose: bool = True,
) -> dict:
    root = Path(root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Root folder not found: {root}")

    if llm_call is None:
        from prep_mode import default_llm_tagger
        llm_call = default_llm_tagger

    cache: dict = json.load(open(cache_path)) if Path(cache_path).exists() else {}

    files = find_documents(root)
    skipped_folder, skipped_year, needs_year = [], [], []
    rows = []

    for idx, f in enumerate(files, 1):
        hit = classify_folder(f, root)
        if hit is None:
            skipped_folder.append(f)
            continue
        role, exam_type = hit

        if verbose and not dry_run:
            print(f"[extract] ({idx}/{len(files)}) {f.relative_to(root)}")

        year = guess_year_from_path(f, root)
        # Year is load-bearing ONLY for analysis rows — the walk-forward
        # backtest trains on year<N, tests on year N. Pool rows (practice/quiz
        # question bank) never key on year, so a missing one is fine there:
        # tag them year=0 (sentinel) and keep them instead of dropping.
        if year is None:
            if role == "pool":
                year = 0
            elif role == "both":
                # useful as pool, useless as analysis without a year —
                # downgrade to pool-only rather than drop or fake a year.
                role, year = "pool", 0
            else:                    # pure analysis — genuinely needs a year
                needs_year.append(f)
                continue

        file_key = str(f.relative_to(root))
        file_hash = hashlib.sha1(file_key.encode()).hexdigest()[:10]
        cache_hit = cache.get(file_key)

        if cache_hit and cache_hit.get("mtime") == f.stat().st_mtime:
            questions = cache_hit["questions"]
        else:
            try:
                text = extract_text(f)
            except Exception as e:
                print(f"[extract] SKIP (read failed) {f}: {e}")
                continue
            if dry_run:
                questions = [{"text": "(dry-run: not sent to LLM)", "marks": None, "answer": None}]
            else:
                try:
                    questions = split_into_questions(text, llm_call, no_llm=no_llm)
                except Exception as e:
                    # retries exhausted on this file — log and move on rather
                    # than losing every already-processed file's progress
                    print(f"[extract] SKIP (LLM failed after retries) {f}: {e}")
                    continue
                cache[file_key] = {"mtime": f.stat().st_mtime, "questions": questions}
                # save after EVERY file — if a later file exhausts retries and
                # raises, everything processed so far is still on disk, not lost
                json.dump(cache, open(cache_path, "w"), indent=2)

        for i, q in enumerate(questions):
            rows.append({
                "q_id": f"{file_hash}-{i}",
                "year": year,
                "marks": q["marks"] if q["marks"] is not None else "",
                "text": q["text"],
                "answer": q.get("answer") or "",
                "exam_type": exam_type,
                "role": role,
                "source": file_key,
            })

    if not dry_run:
        json.dump(cache, open(cache_path, "w"), indent=2)

    if verbose:
        print(f"[extract] {len(files)} files found under {root}")
        if skipped_folder:
            uniq = sorted({classify_folder_name(f, root) for f in skipped_folder})
            print(f"[extract] {len(skipped_folder)} files SKIPPED — folder not in "
                  f"FOLDER_ROLE mapping: {uniq}")
        if needs_year:
            print(f"[extract] {len(needs_year)} files SKIPPED — couldn't guess year "
                  f"from filename:")
            for f in needs_year[:15]:
                print(f"    {f.relative_to(root)}")
            if len(needs_year) > 15:
                print(f"    ... and {len(needs_year) - 15} more")
        by_role = {}
        for r in rows:
            by_role[r["role"]] = by_role.get(r["role"], 0) + 1
        print(f"[extract] {len(rows)} questions extracted. By role: {by_role}")

    if dry_run:
        print("[extract] --dry-run: nothing written.")
        return {"files": len(files), "questions": len(rows), "written": False}

    if rows:
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["q_id", "year", "marks", "text",
                                                "answer", "exam_type", "role",
                                                "source"])
            w.writeheader()
            w.writerows(rows)
        print(f"[extract] wrote {out_csv} ({len(rows)} questions)")

    return {"files": len(files), "questions": len(rows), "written": bool(rows)}


def classify_folder_name(f: Path, root: Path) -> str:
    try:
        parts = f.relative_to(root).parts[:-1]
    except ValueError:
        parts = f.parts[:-1]
    return parts[-1] if parts else "(root)"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, help="Path to RAG-MAROS (or wherever the PDFs live)")
    ap.add_argument("--out", default="questions.csv")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be read/skipped, no LLM calls, no writes")
    ap.add_argument("--no-llm", action="store_true",
                    help="Regex-based question splitting instead of the LLM — "
                         "zero API calls, noisier splits. For when the API is "
                         "rate-limited/dead. Re-run without this flag later "
                         "for cleaner splits (cache means only files not yet "
                         "successfully split get re-processed).")
    args = ap.parse_args()
    extract(args.root, args.out, dry_run=args.dry_run, no_llm=args.no_llm)