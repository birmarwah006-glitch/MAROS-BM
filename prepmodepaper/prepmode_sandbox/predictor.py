"""
paper_predictor.py — turns tagged questions.csv into a real predicted paper,
and answers Prof Oak's "give me the most important questions" on demand.

Sits next to prep_mode.py and reuses its Question model, tagging, and
score_concepts() ranking directly — this file adds the QUESTION-level layer
prep_mode.py deliberately doesn't do (it only ranks concept names, never
touches question text, on purpose — see its docstring).

Two consumers:
  1. /prep/paper  — build_predicted_paper() writes data/predicted_paper_
     {exam_type}.json, which prep_mode_service.get_predicted_paper() serves
     read-only. Rebuild with --build, restart the server to pick it up.
  2. Prof Oak chat — get_important_questions() is a direct, callable function
     (not a RAG similarity search) for "give me the most important
     questions" style asks. Ranks REAL past questions — os questions bank +
     midterm papers, via role — by the same concept scoring prep_mode.py
     already validated, so what Oak says IS the validated ranking, not
     whatever a vector search happened to retrieve.

WHY A VALIDATION GATE
----------------------
Live-demo incident: predicted questions showed orphaned answer fragments,
CO-mapping boilerplate glued onto question text, and a phantom "2027" year
(a bug in year-guessing elsewhere, not fixed here, but this gate stops any
garbage that slips through from ever reaching a student). Every question
here passes is_valid_question() before it can appear anywhere.

WHY question+answer ARE SEPARATE
----------------------------------
extract_questions.py (patched alongside this file) now pulls a worked
answer into its own `answer` field instead of leaving it glued to `text`.
Every PredictedQuestion below carries both, separately, so the frontend can
render "Question" and "Answer" as two distinct blocks — never one paragraph.

CLI
---
  python3 paper_predictor.py --data questions.csv --build --exam-type midsem
  python3 paper_predictor.py --data questions.csv --important --exam-type midsem -k 5
  python3 paper_predictor.py --data questions.csv --important --exam-type midsem -k 5 --concept paging
"""

from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
    load_dotenv("maros.env")
except ImportError:
    pass

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Callable, Iterable, Optional

from prep_mode import (
    CONCEPTS, Question, load_questions, tag_questions, score_concepts,
    default_llm_tagger, heuristic_concept_tagger,
)

DATA_DIR = Path(__file__).parent / "data"

# ---------------------------------------------------------------------------
# YEAR SANITIZING — a bad filename/folder-path year guess elsewhere in the
# pipeline should never reach a student as e.g. "from the 2027 paper".
# ---------------------------------------------------------------------------
VALID_YEAR_RANGE = (2017, 2025)
POOL_YEAR = 0


def year_label(year: int) -> str:
    return "question bank" if year == POOL_YEAR else f"the {year} paper"


def sanitize_years(qs: list[Question], verbose: bool = True) -> list[Question]:
    lo, hi = VALID_YEAR_RANGE
    good, bad = [], []
    for q in qs:
        (good if (q.year == POOL_YEAR or lo <= q.year <= hi) else bad).append(q)
    if bad and verbose:
        yrs = sorted({q.year for q in bad})
        print(f"[sanitize] DROPPED {len(bad)} rows with out-of-range years "
              f"{yrs} (valid: {lo}-{hi}). Fix the source filenames/years.")
    return good


# ---------------------------------------------------------------------------
# VALIDATION GATE — the actual fix for the broken-text demo incident.
# Applied to BOTH text and answer, since a solution can be just as broken
# (orphaned mid-sentence, boilerplate-contaminated) as a question can.
# ---------------------------------------------------------------------------
_BOILERPLATE = re.compile(
    r"course outcomes?|CO\d\s*:|mapping of course|question paper to co|"
    r"assessment of attainment|all the best|end of paper",
    re.IGNORECASE,
)
_ANSWER_ONLY = re.compile(
    r"^\s*(ans(wer)?\s*[:\)]|solution\s*[:\-]|a\.\s|sol\s*:)", re.IGNORECASE
)
_QUESTION_SIGNAL = re.compile(
    r"\?|what|why|how|explain|describe|state|consider|calculate|compute|"
    r"write|draw|find|derive|compare|differentiate|distinguish|define|"
    r"assume|given|show|list|which|prove|implement|design|discuss|match",
    re.IGNORECASE,
)
MIN_QUESTION_CHARS = 25
MAX_TEXT_CHARS = 4000


def is_valid_question(text: str) -> tuple[bool, str]:
    """Gate for the QUESTION half. A solution-shaped fragment (starts with
    "Ans:"/"A.") standing in as a question is exactly the demo bug — reject
    it rather than show a student an answer with no question."""
    t = (text or "").strip()
    if len(t) < MIN_QUESTION_CHARS:
        return False, "too_short"
    if len(t) > MAX_TEXT_CHARS:
        return False, "too_long_probably_swallowed_solution"
    if _ANSWER_ONLY.match(t):
        return False, "answer_without_question"
    if _BOILERPLATE.search(t[:400]) or _BOILERPLATE.search(t[-600:]):
        return False, "boilerplate_contamination"
    if not _QUESTION_SIGNAL.search(t):
        return False, "no_question_signal"
    return True, "ok"


def clean_answer(answer: Optional[str]) -> Optional[str]:
    """Gate + trim for the ANSWER half. Unlike the question gate, an answer
    doesn't need a "?" or a verb signal — it just needs to not be boilerplate
    contamination and not be absurdly long (same swallowed-solution risk)."""
    if not answer:
        return None
    a = answer.strip()
    if len(a) < 3 or len(a) > MAX_TEXT_CHARS:
        return None
    m = _BOILERPLATE.search(a)
    if m and m.start() > 10:
        a = a[:m.start()].rstrip(" -x\n\t")
    elif m:
        return None   # boilerplate from the very start — nothing usable left
    return a or None


def strip_boilerplate_tail(text: str) -> str:
    m = _BOILERPLATE.search(text)
    if m and m.start() > MIN_QUESTION_CHARS:
        return text[:m.start()].rstrip(" -x\n\t")
    return text


def filter_questions(qs: list[Question], verbose: bool = True) -> list[Question]:
    """Apply the gate to every question in place. Rejects the question
    entirely if `text` fails; a bad `answer` alone just gets dropped to
    None (a clean question with no solution is fine — a broken solution
    tacked onto a good question is not)."""
    kept, rejects = [], Counter()
    for q in qs:
        q.text = strip_boilerplate_tail(q.text)
        ok, reason = is_valid_question(q.text)
        if not ok:
            rejects[reason] += 1
            continue
        q.answer = clean_answer(q.answer)
        kept.append(q)
    if verbose and rejects:
        print(f"[gate] rejected {sum(rejects.values())}: {dict(rejects)}")
    if verbose:
        with_ans = sum(1 for q in kept if q.answer)
        print(f"[gate] {len(kept)} questions passed "
              f"({with_ans} with a clean answer, {len(kept)-with_ans} question-only)")
    return kept


# ---------------------------------------------------------------------------
# QUESTION TYPE TAGGING — a second axis on top of prep_mode's concepts
# (what the student must DO, not the topic), used to keep a predicted paper's
# question mix realistic (not all "explain" questions, etc).
# ---------------------------------------------------------------------------
QUESTION_TYPES: dict[str, str] = {
    "define_explain":
        "Define a term, explain a concept/mechanism/algorithm in words, "
        "'what is', 'explain the working of', 'describe'. Prose answer.",
    "numerical":
        "A calculation with concrete numbers: scheduling turnaround/waiting "
        "time tables, page-table/address translation math, disk access time, "
        "Banker's algorithm safety computation, EMAT, hit ratios.",
    "compare":
        "Differentiate / compare / contrast two or more things, 'distinguish "
        "between X and Y', advantages vs disadvantages tables.",
    "code_trace":
        "Read or write actual code: predict output of a fork() snippet, "
        "write pseudo-code/code for a mechanism, trace program execution.",
    "diagram":
        "Draw / illustrate: state transition diagrams, memory layout figures, "
        "architecture diagrams, 'with a neat diagram'.",
    "short_note":
        "'Write short notes on X (and Y)' — brief survey answers, often "
        "multi-topic, end-of-paper style.",
}
VALID_TYPES = set(QUESTION_TYPES)
DEFAULT_TYPE = "define_explain"

MARKS_BANDS = (("small", 0, 4), ("medium", 5, 7), ("large", 8, 999))


def marks_band(marks: Optional[float]) -> str:
    if marks is None:
        return "medium"
    for name, lo, hi in MARKS_BANDS:
        if lo <= marks <= hi:
            return name
    return "medium"


def _type_prompt(question_text: str) -> str:
    catalog = "\n".join(f"- {t}: {g}" for t, g in QUESTION_TYPES.items())
    return (
        "Classify this Operating Systems exam question into EXACTLY ONE "
        "question type (what the student must DO, not the topic).\n\n"
        f"Types:\n{catalog}\n\n"
        f'Question:\n"""{question_text}"""\n\n'
        'Reply with ONLY a JSON object, no prose:\n'
        '{"type": "<id>"}\n'
        "Use only ids from the list above."
    )


def heuristic_type_tagger(prompt: str) -> str:
    m = re.search(r'"""(.*?)"""', prompt, re.DOTALL)
    text = (m.group(1) if m else prompt).lower()
    if any(k in text for k in ["compute", "calculate", "turnaround time",
            "waiting time", "hit ratio", "access time", "emat",
            "how many bytes", "page table entries", "average "]):
        return json.dumps({"type": "numerical"})
    if any(k in text for k in ["compare", "differentiate", "distinguish between",
            "advantages and disadvantages", " vs ", "versus"]):
        return json.dumps({"type": "compare"})
    if any(k in text for k in ["write code", "write a program", "pseudocode",
            "trace the output", "predict the output", "implement the"]):
        return json.dumps({"type": "code_trace"})
    if any(k in text for k in ["diagram", "draw ", "illustrate", "sketch"]):
        return json.dumps({"type": "diagram"})
    if any(k in text for k in ["short note", "short notes", "write notes"]):
        return json.dumps({"type": "short_note"})
    return json.dumps({"type": DEFAULT_TYPE})


def _parse_type(raw: str) -> str:
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        got = json.loads(raw[start:end]).get("type", "")
    except Exception:
        return DEFAULT_TYPE
    return got if got in VALID_TYPES else DEFAULT_TYPE


def tag_types(
    questions: list[Question],
    llm_call: Optional[Callable[[str], str]] = None,
    cache_path: str = "type_cache.json",
    verbose: bool = True,
) -> dict[str, str]:
    llm_call = llm_call or default_llm_tagger
    cache: dict = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    out: dict[str, str] = {}
    tagged = reused = 0
    for q in questions:
        h = hashlib.sha1(q.text.encode("utf-8")).hexdigest()[:12]
        hit = cache.get(q.q_id)
        if hit and hit.get("hash") == h:
            out[q.q_id] = hit["type"]
            reused += 1
            continue
        qtype = _parse_type(llm_call(_type_prompt(q.text)))
        out[q.q_id] = qtype
        cache[q.q_id] = {"hash": h, "type": qtype}
        tagged += 1
    json.dump(cache, open(cache_path, "w"), indent=2)
    if verbose:
        print(f"[types] {tagged} tagged, {reused} reused from cache")
    return out


# ---------------------------------------------------------------------------
# SLOT RANKING — (concept, type) pairs, used only for the FULL PAPER build.
# get_important_questions() (Oak's chat) uses prep_mode's plain concept
# ranking directly instead — simpler and matches what the student already
# sees in the concept tree, so "most important questions" and "most
# important concepts" tell a consistent story.
# ---------------------------------------------------------------------------
def rank_slots(
    questions: Iterable[Question], types: dict[str, str],
) -> list[tuple[tuple[str, str], float]]:
    qs = [q for q in questions if q.concepts]
    s: dict[tuple[str, str], float] = defaultdict(float)
    for q in qs:
        w = 1.0 + (q.marks or 0.0)
        t = types.get(q.q_id, DEFAULT_TYPE)
        for c in q.concepts:
            s[(c, t)] += w
    return sorted(s.items(), key=lambda kv: (-kv[1], kv[0]))


# ---------------------------------------------------------------------------
# PAPER GENERATION  (/prep/paper artifact)
# ---------------------------------------------------------------------------
@dataclass
class Blueprint:
    exam_type: str
    n_papers: int
    target_n_questions: int
    typical_total_marks: float
    band_mix: dict[str, float]


def build_blueprint(questions: Iterable[Question], exam_type: str) -> Blueprint:
    per_year: dict[int, list[Question]] = defaultdict(list)
    for q in questions:
        if q.exam_type == exam_type:
            per_year[q.year].append(q)
    if not per_year:
        raise ValueError(f"No questions with exam_type={exam_type!r}")
    counts = [len(v) for v in per_year.values()]
    totals = [sum(q.marks or 0.0 for q in v) for v in per_year.values()]
    bands = Counter(marks_band(q.marks) for v in per_year.values() for q in v)
    n = sum(bands.values()) or 1
    return Blueprint(
        exam_type=exam_type,
        n_papers=len(per_year),
        target_n_questions=int(round(median(counts))),
        typical_total_marks=float(median(totals)),
        band_mix={b: bands.get(b, 0) / n for b, _, _ in MARKS_BANDS},
    )


@dataclass
class PredictedQuestion:
    q_no: int
    concept: str
    qtype: str
    marks: Optional[float]
    text: str
    answer: Optional[str]      # rendered as a SEPARATE block by the frontend
    source_year: int
    source_label: str          # "the 2023 paper" / "question bank" — render verbatim
    source_q_id: str
    slot_score: float


_ws = re.compile(r"\s+")

def _norm(t: str) -> str:
    return _ws.sub(" ", t.lower().strip())[:160]


MIN_ANALYSIS = 8


def generate_paper(
    train_qs: list[Question], types: dict[str, str], exam_type: str,
) -> dict:
    """Assemble a predicted paper: real questions (with real answers where
    available), picked to fill the ranked (concept, type) slots, matching
    the shape (question count, marks distribution) of real past papers."""
    analysis = [q for q in train_qs
                if q.exam_type == exam_type and q.concepts
                and q.role in ("analysis", "both") and q.year != POOL_YEAR]
    used_fallback = False
    if len(analysis) < MIN_ANALYSIS:
        used_fallback = True
        analysis = [q for q in train_qs if q.exam_type == exam_type and q.concepts]
        if not analysis:
            raise ValueError(f"No questions for exam_type={exam_type!r}")
        print(f"[generate_paper] WARNING: <{MIN_ANALYSIS} real analysis rows "
              f"for {exam_type!r} — FALLBACK. Placeholder, not a full prediction.")

    fill = {q.q_id: q for q in analysis}
    for q in train_qs:
        if q.exam_type == exam_type and q.concepts and q.role in ("pool", "both"):
            fill.setdefault(q.q_id, q)
    fill_pool = [q for q in fill.values() if is_valid_question(q.text)[0]]

    bp = build_blueprint(analysis, exam_type)
    if used_fallback:
        bp.target_n_questions = 10
        mv = [q.marks for q in analysis if q.marks]
        bp.typical_total_marks = round(
            (sum(mv) / len(mv) if mv else 7.0) * bp.target_n_questions, 1)

    ranked = rank_slots(analysis, types)

    need = {b: int(round(bp.band_mix[b] * bp.target_n_questions))
            for b, _, _ in MARKS_BANDS}
    while sum(need.values()) < bp.target_n_questions:
        need["medium"] += 1

    chosen: list[PredictedQuestion] = []
    used_ids: set[str] = set()
    used_texts: set[str] = set()

    def _pick(concept: str, qtype: str) -> Optional[Question]:
        cands = [q for q in fill_pool
                 if concept in q.concepts
                 and types.get(q.q_id, DEFAULT_TYPE) == qtype
                 and q.q_id not in used_ids
                 and _norm(q.text) not in used_texts]
        if not cands:
            return None
        cands.sort(key=lambda q: (
            0 if need.get(marks_band(q.marks), 0) > 0 else 1,
            0 if q.role in ("analysis", "both") else 1,
            0 if q.answer else 1,      # prefer a question WITH a worked answer
            -q.year,
            -(q.marks or 0.0),
        ))
        return cands[0]

    for _pass in range(3):
        for (concept, qtype), s in ranked:
            if len(chosen) >= bp.target_n_questions:
                break
            q = _pick(concept, qtype)
            if not q:
                continue
            used_ids.add(q.q_id)
            used_texts.add(_norm(q.text))
            b = marks_band(q.marks)
            if need.get(b, 0) > 0:
                need[b] -= 1
            chosen.append(PredictedQuestion(
                q_no=len(chosen) + 1, concept=concept, qtype=qtype,
                marks=q.marks, text=q.text, answer=q.answer,
                source_year=q.year, source_label=year_label(q.year),
                source_q_id=q.q_id, slot_score=round(s, 2),
            ))
        if len(chosen) >= bp.target_n_questions:
            break

    return {
        "exam_type": exam_type,
        "blueprint": asdict(bp),
        "used_fallback": used_fallback,
        "n_questions": len(chosen),
        "n_with_answer": sum(1 for pq in chosen if pq.answer),
        "total_marks": sum(pq.marks or 0.0 for pq in chosen),
        "questions": [asdict(pq) for pq in chosen],
        "slot_ranking": [{"concept": c, "qtype": t, "score": round(s, 2)}
                         for (c, t), s in ranked[:15]],
    }


# ---------------------------------------------------------------------------
# JUDGE + WALK-FORWARD  (validated accuracy, attached to the built artifact)
# ---------------------------------------------------------------------------
def judge_paper(paper: dict, real_qs: list[Question],
                types: dict[str, str]) -> dict:
    gen_c = {pq["concept"] for pq in paper["questions"]}
    gen_s = {(pq["concept"], pq["qtype"]) for pq in paper["questions"]}
    real_c = {c for q in real_qs for c in q.concepts}
    real_s = {(c, types.get(q.q_id, DEFAULT_TYPE))
              for q in real_qs for c in q.concepts}

    def _pr(g: set, r: set) -> tuple[float, float]:
        if not g or not r:
            return 0.0, 0.0
        i = len(g & r)
        return i / len(g), i / len(r)

    cp, cr = _pr(gen_c, real_c)
    sp, sr = _pr(gen_s, real_s)
    return {"concept_precision": round(cp, 3), "concept_recall": round(cr, 3),
            "slot_precision": round(sp, 3), "slot_recall": round(sr, 3)}


def walk_forward_paper(questions: list[Question], types: dict[str, str],
                       exam_type: str) -> dict:
    analysis = [q for q in questions
                if q.exam_type == exam_type and q.concepts
                and q.role in ("analysis", "both") and q.year != POOL_YEAR]
    full = [q for q in questions if q.exam_type == exam_type and q.concepts]
    years = sorted({q.year for q in analysis})
    folds = []
    for ty in years[1:]:
        train = [q for q in full if q.year < ty]
        test = [q for q in analysis if q.year == ty]
        if not train or not test:
            continue
        try:
            paper = generate_paper(train, types, exam_type)
        except ValueError:
            continue
        folds.append({"test_year": ty, "n_generated": paper["n_questions"],
                      "used_fallback": paper["used_fallback"],
                      **judge_paper(paper, test, types)})

    def _mean(key: str, real_only: bool = False) -> Optional[float]:
        vals = [f[key] for f in folds if f.get(key) is not None
                and (not real_only or not f["used_fallback"])]
        return round(sum(vals) / len(vals), 3) if vals else None

    return {
        "exam_type": exam_type, "folds": folds,
        "mean_concept_precision_all": _mean("concept_precision"),
        "mean_concept_precision_real_folds": _mean("concept_precision", True),
        "n_fallback_folds": sum(1 for f in folds if f["used_fallback"]),
    }


def build_predicted_paper(qs: list[Question], types: dict[str, str],
                          exam_type: str) -> Path:
    """The function --build calls: writes data/predicted_paper_{exam_type}.json,
    which prep_mode_service.get_predicted_paper() serves to /prep/paper."""
    paper = generate_paper(qs, types, exam_type)
    paper["backtest"] = walk_forward_paper(qs, types, exam_type)
    DATA_DIR.mkdir(exist_ok=True)
    out = DATA_DIR / f"predicted_paper_{exam_type}.json"
    out.write_text(json.dumps(paper, indent=2, ensure_ascii=False))
    print(f"[build] wrote {out}  ({paper['n_questions']} questions, "
          f"{paper['n_with_answer']} with a worked answer, "
          f"fallback={paper['used_fallback']})")
    return out


# ---------------------------------------------------------------------------
# PROF OAK: "give me the most important questions"
# ---------------------------------------------------------------------------
def get_important_questions(
    qs: list[Question], exam_type: str, k: int = 5,
    concept: Optional[str] = None, decay: float = 1.0,
) -> list[dict]:
    """Direct callable for Oak's chat handler — no RAG similarity search,
    no vector store round-trip. Ranks REAL questions (os questions bank +
    midterm papers, via role) by prep_mode's own validated concept scoring,
    so 'most important questions' agrees with the concept tree the student
    is already looking at. Every question returned has already passed the
    validation gate; `answer` is None (never fabricated) when the source
    had no worked solution.

    concept=None ranks across ALL concepts (Oak's generic "give me the most
    important questions"); pass a concept id to scope it ("most important
    paging questions")."""
    pool = [q for q in qs if q.exam_type == exam_type and q.concepts
            and is_valid_question(q.text)[0]
            and (concept is None or concept in q.concepts)]
    if not pool:
        return []

    ranked_concepts = score_concepts(pool, mode="spec_raw", decay=decay)
    order = [s.concept for s in ranked_concepts] if concept is None else [concept]

    seen_texts: set[str] = set()
    out: list[dict] = []
    for c in order:
        cands = sorted(
            (q for q in pool if c in q.concepts and _norm(q.text) not in seen_texts),
            key=lambda q: (0 if q.answer else 1, -(q.marks or 0.0), -q.year),
        )
        for q in cands:
            seen_texts.add(_norm(q.text))
            out.append({
                "text": q.text,
                "answer": q.answer,        # None if the source had no solution
                "marks": q.marks,
                "concept": c,
                "source_label": year_label(q.year),
            })
            break   # one per concept per pass, so the top-k spans concepts
        if len(out) >= k:
            break

    # if concepts ran out before hitting k (e.g. a narrow concept filter),
    # top up from the same pool by marks, still de-duped
    if len(out) < k:
        extra = sorted(
            (q for q in pool if _norm(q.text) not in seen_texts),
            key=lambda q: (0 if q.answer else 1, -(q.marks or 0.0), -q.year),
        )
        for q in extra:
            if len(out) >= k:
                break
            seen_texts.add(_norm(q.text))
            out.append({
                "text": q.text, "answer": q.answer, "marks": q.marks,
                "concept": (q.concepts[0] if q.concepts else "unclassified"),
                "source_label": year_label(q.year),
            })

    return out[:k]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--exam-type", default="midsem", choices=["midsem", "endsem"])
    ap.add_argument("--build", action="store_true",
                    help="write data/predicted_paper_{exam_type}.json")
    ap.add_argument("--important", action="store_true",
                    help="print top-k important questions (Oak's chat path)")
    ap.add_argument("--concept", default=None,
                    help="scope --important to one concept id")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--no-llm", action="store_true",
                    help="heuristic taggers instead of the LLM API")
    args = ap.parse_args()

    concept_call = heuristic_concept_tagger if args.no_llm else None
    type_call = heuristic_type_tagger if args.no_llm else None

    qs = load_questions(args.data)
    qs = sanitize_years(qs)
    qs = tag_questions(qs, llm_call=concept_call)
    qs = filter_questions(qs)
    types = tag_types(qs, llm_call=type_call)

    if args.important:
        qi = get_important_questions(qs, args.exam_type, k=args.k, concept=args.concept)
        scope = f" ({args.concept})" if args.concept else ""
        print(f"\nTop-{len(qi)} important questions for {args.exam_type}{scope}:\n")
        for i, q in enumerate(qi, 1):
            print(f"{i}. [{q['concept']}] {q['source_label']}"
                  f"{f', {q['marks']:.0f}m' if q['marks'] else ''}")
            print(f"   Q: {q['text'][:200]}{'...' if len(q['text']) > 200 else ''}")
            if q['answer']:
                print(f"   A: {q['answer'][:200]}{'...' if len(q['answer']) > 200 else ''}")
            else:
                print("   A: (no worked solution available for this one)")
            print()

    if args.build:
        build_predicted_paper(qs, types, args.exam_type)


if __name__ == "__main__":
    main()