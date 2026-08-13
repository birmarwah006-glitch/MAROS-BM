"""
Prof Oak - Prep Mode algorithm
==============================
Raw PYQ questions in -> concept-tagged, scored, ranked, time-filtered out.

Pipeline (matches the spec):
  1. tag_questions()      raw question text -> concept(s) via LLM (cached)
  2. score_concepts()     frequency_points + marks_weight per concept
  3. rank                 sort concepts by concept_score, descending
  4. filter_by_days_left  apply the days-remaining threshold
  5. mermaid_chart()      importance-ranking chart string
  6. backtest / walk_forward  precision@k validation

This is a scoring function, NOT a trained model. No epochs, no GPU.
Effort lives in tagging quality (step 1) and the concept glosses below.

Run `python prep_mode.py --demo` to exercise everything except the LLM
against synthetic tagged data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable

# ---------------------------------------------------------------------------
# CONCEPTS  --  EDIT THESE. The gloss after each name is fed to the tagger.
# The better the gloss, the fewer boundary mistakes -> the better precision@k.
# ---------------------------------------------------------------------------
CONCEPTS: dict[str, str] = {
    "computer-architecture":
        "Hardware foundations the OS builds on: instruction execution, "
        "registers, memory hierarchy, caches, interrupts. Raw hardware "
        "behavior, NOT the OS abstractions layered over it.",
    "cpu-virtualization":
        "The MECHANISM of sharing one CPU across many processes: limited "
        "direct execution, user/kernel mode switching, traps, system calls, "
        "timer interrupts. The mechanism, not the process abstraction and "
        "not the scheduling policy.",
    "processes":
        "The process ABSTRACTION: process API (fork/exec/wait), process "
        "states, the process control block, address-space layout. What a "
        "process is and how you create/control one.",
    "scheduling":
        "POLICIES for choosing what runs next: FIFO, SJF, STCF, Round Robin, "
        "MLFQ, lottery/proportional-share, metrics like turnaround and "
        "response time. The policy, not the CPU-sharing mechanism.",
    "memory-virtualization":
        "Giving each process a private virtual address space: address "
        "translation, base-and-bounds, segmentation, virtual vs physical "
        "addresses. The abstraction and translation idea; page specifics "
        "belong to paging.",
    "paging":
        "Page-based memory: pages/frames, page tables, TLBs, multi-level "
        "page tables, swapping to disk, page-replacement policies. Anything "
        "page/page-table/TLB/swap specific.",
    "concurrency":
        "The general concurrency PROBLEM: race conditions, critical "
        "sections, atomicity, why mutual exclusion is needed, shared-state "
        "hazards. The problem, above the specific thread API or lock impl.",
    "threads":
        "The thread abstraction and API: creating threads, thread vs "
        "process, shared address space, condition variables, semaphores, "
        "producer/consumer coordination.",
    "locks":
        "Lock implementation and correctness: spinlocks, test-and-set / "
        "compare-and-swap, mutexes, lock granularity/contention, and "
        "DEADLOCK (its four conditions, avoidance, detection).",
    "persistence":
        "I/O and storage foundations below the file system: devices, "
        "device drivers, the I/O stack, disks (HDD geometry, seek/rotation), "
        "RAID. How the OS talks to storage hardware.",
    "file-systems":
        "The file-system abstraction and implementation: files/directories, "
        "inodes, the file API (open/read/write), on-disk layout, locality "
        "(FFS-style). The FS data structures and API.",
    "data-integrity":
        "Keeping data correct across failures: crash consistency, "
        "journaling, fsck, write ordering, checksums, fault tolerance.",
}
VALID_IDS = set(CONCEPTS)
N_CONCEPTS = len(CONCEPTS)  # 12


# ---------------------------------------------------------------------------
# DATA MODEL
# ---------------------------------------------------------------------------
@dataclass
class Question:
    q_id: str
    year: int
    marks: float | None            # None = paper didn't list per-question marks
    text: str
    concepts: list[str] = field(default_factory=list)  # filled by the tagger
    exam_type: str = "endsem"      # "midsem" | "endsem" — defaults to endsem
    # (whole-syllabus) for backward compat with data that predates this field.


def load_questions(path: str) -> list[Question]:
    """Load raw questions from CSV or JSON. Required fields: q_id, year, text.
    Optional: marks (numeric), concepts (if you ever pre-tag), exam_type
    ("midsem"/"endsem" — defaults to "endsem" if absent)."""
    if path.endswith(".json"):
        rows = json.load(open(path, encoding="utf-8"))
    elif path.endswith((".csv", ".tsv")):
        import csv
        delim = "\t" if path.endswith(".tsv") else ","
        rows = list(csv.DictReader(open(path, encoding="utf-8"), delimiter=delim))
    else:
        raise ValueError("Give me a .json, .csv or .tsv file.")

    out = []
    for r in rows:
        marks_raw = r.get("marks", "")
        marks = float(marks_raw) if str(marks_raw).strip() not in ("", "None") else None
        concepts = r.get("concepts", [])
        if isinstance(concepts, str):
            concepts = [c.strip() for c in concepts.split(",") if c.strip()]
        out.append(Question(
            q_id=str(r["q_id"]),
            year=int(r["year"]),
            marks=marks,
            text=r["text"] if "text" in r else r["question_text"],
            concepts=concepts,
            exam_type=r.get("exam_type", "endsem"),
        ))
    return out


# ---------------------------------------------------------------------------
# STEP 1: TAGGING (LLM, multi-label, cached)
# ---------------------------------------------------------------------------
def _tag_prompt(question_text: str) -> str:
    catalog = "\n".join(f"- {cid}: {gloss}" for cid, gloss in CONCEPTS.items())
    return (
        "You are tagging an Operating Systems exam question to concepts.\n"
        "Choose EVERY concept the question genuinely tests (usually 1-2, "
        "occasionally 3). Do not force a single choice; do not pad the list.\n\n"
        f"Concepts:\n{catalog}\n\n"
        f"Question:\n\"\"\"{question_text}\"\"\"\n\n"
        'Reply with ONLY a JSON object, no prose:\n'
        '{"concepts": ["<id>", ...]}\n'
        "Use only ids from the list above."
    )


def default_llm_tagger(prompt: str) -> str:
    """OpenAI-compatible caller mirroring Prof Oak's Cerebras-primary /
    Groq-fallback routing. SWAP THIS for your existing Prof Oak router if you
    already have one -- pass it as `llm_call=` to tag_questions().
    Reads: CEREBRAS_API_KEY / CEREBRAS_BASE_URL / CEREBRAS_MODEL,
           GROQ_API_KEY / GROQ_BASE_URL / GROQ_MODEL."""
    import requests

    def _call(base, key, model):
        resp = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "temperature": 0,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    try:
        return _call(
            os.environ.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1"),
            os.environ["CEREBRAS_API_KEY"],
            os.environ.get("CEREBRAS_MODEL", "llama-3.3-70b"),
        )
    except Exception as e_primary:
        try:
            return _call(
                os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
                os.environ["GROQ_API_KEY"],
                os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
            )
        except Exception as e_fallback:
            raise RuntimeError(
                f"Both taggers failed. Cerebras: {e_primary}; Groq: {e_fallback}"
            )


def _parse_concepts(raw: str) -> list[str]:
    """Defensive JSON parse. Drops anything not in the valid id set so a
    hallucinated label can never poison the score."""
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
        got = data.get("concepts", [])
    except Exception:
        return []
    return [c for c in got if c in VALID_IDS]


def tag_questions(
    questions: list[Question],
    llm_call: Callable[[str], str] | None = None,
    cache_path: str = "tag_cache.json",
    verbose: bool = True,
) -> list[Question]:
    """Tag every question once, cache by q_id + text hash, reuse on rerun.
    Caching is deliberate: LLM tagging is nondeterministic, and a ranking you
    can't reproduce is a ranking you can't spot-check or backtest."""
    llm_call = llm_call or default_llm_tagger
    cache: dict = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

    tagged = skipped = 0
    for q in questions:
        h = hashlib.sha1(q.text.encode("utf-8")).hexdigest()[:12]
        hit = cache.get(q.q_id)
        if hit and hit.get("hash") == h:            # unchanged -> reuse
            q.concepts = hit["concepts"]
            skipped += 1
            continue
        concepts = _parse_concepts(llm_call(_tag_prompt(q.text)))
        q.concepts = concepts
        cache[q.q_id] = {"hash": h, "concepts": concepts}
        tagged += 1

    json.dump(cache, open(cache_path, "w"), indent=2)
    if verbose:
        print(f"[tag] {tagged} tagged, {skipped} reused from cache")
        untagged = [q.q_id for q in questions if not q.concepts]
        if untagged:
            print(f"[tag] WARNING {len(untagged)} question(s) got no concept "
                  f"-> excluded from scoring: {untagged[:8]}"
                  f"{'...' if len(untagged) > 8 else ''}")
    return questions


# ---------------------------------------------------------------------------
# STEP 2-3: SCORING + RANKING
# ---------------------------------------------------------------------------
@dataclass
class ConceptScore:
    concept: str
    frequency_points: float   # int-valued unless decay != 1.0, then a weighted sum
    marks_weight: float
    concept_score: float


def score_concepts(
    questions: Iterable[Question],
    mode: str = "spec_raw",     # "spec_raw" = freq + marks (per spec)
    w_freq: float = 0.5,        # "normalized" mode weights (0..1 each side)
    w_marks: float = 0.5,
    decay: float = 1.0,         # recency weighting: 1.0 = off (original behavior)
) -> list[ConceptScore]:
    """
    frequency_points = # questions tagged to the concept (+1 per multi-tag,
                        weighted by recency if decay < 1.0 -- see below)
    marks_weight     = total marks summed across those questions, same
                       recency weighting applied
                       (full marks to EACH concept a question touches --
                        parallel to the +1 rule, no splitting)

    mode="spec_raw"   : concept_score = frequency_points + marks_weight
                        (exact spec; note freq and marks live on different
                        scales, so marks usually dominates -- see --demo)
    mode="normalized" : min-max each component to 0..1 across the 12 concepts,
                        then w_freq*freq_n + w_marks*marks_n. Use this if you
                        want frequency to actually count.

    decay             : recency weighting. Each question is weighted by
                        decay ** (latest_year_in_this_call - question.year),
                        so decay=1.0 is off (every year counts equally --
                        original behavior). decay=0.9 means each year back
                        counts 10% less; decay=0.85 is more aggressive. Tune
                        this on walk_forward() same as any other hyperparam --
                        don't just pick a number and trust it. "latest_year"
                        is always the max year among the questions PASSED IN
                        to this call, so in a walk-forward fold it's the most
                        recent training year, not some global constant -- that
                        keeps each fold's weighting fair to what it could
                        actually have known at the time.
    """
    latest_year = max((q.year for q in questions), default=0)

    freq: dict[str, float] = defaultdict(float)
    marks: dict[str, float] = defaultdict(float)
    for q in questions:
        w = decay ** (latest_year - q.year) if decay != 1.0 else 1.0
        for c in q.concepts:
            freq[c] += w
            marks[c] += (q.marks or 0.0) * w

    def _norm(d: dict[str, float]) -> dict[str, float]:
        vals = list(d.values()) or [0.0]
        lo, hi = min(vals), max(vals)
        span = hi - lo
        return {c: ((v - lo) / span if span else 0.0) for c, v in d.items()}

    if mode == "normalized":
        fn, mn = _norm(freq), _norm(marks)
        raw_score = {c: w_freq * fn.get(c, 0) + w_marks * mn.get(c, 0)
                     for c in CONCEPTS}
    else:  # spec_raw
        raw_score = {c: freq.get(c, 0) + marks.get(c, 0) for c in CONCEPTS}

    scores = [ConceptScore(c, freq.get(c, 0), marks.get(c, 0.0), raw_score[c])
              for c in CONCEPTS]
    # rank descending; stable tie-break on frequency then name for determinism
    scores.sort(key=lambda s: (-s.concept_score, -s.frequency_points, s.concept))
    return scores


# ---------------------------------------------------------------------------
# STEP 4: TIME-THRESHOLD FILTER
# ---------------------------------------------------------------------------
def coverage_fraction(days_left: int, last_mile: float = 0.225) -> float:
    """>14d -> 0.80 | 7-14d -> 0.50 | <7d -> last_mile (spec says 20-25%,
    default 22.5%). Boundaries: 14 falls in the 7-14 band, 7 falls in it too,
    6 is last-mile."""
    if days_left > 14:
        return 0.80
    if days_left >= 7:
        return 0.50
    return last_mile


def filter_by_days_left(
    ranked: list[ConceptScore], days_left: int, last_mile: float = 0.225,
) -> list[ConceptScore]:
    frac = coverage_fraction(days_left, last_mile)
    n_show = max(1, round(frac * N_CONCEPTS))   # floor of 1, never show nothing
    return ranked[:n_show]


# ---------------------------------------------------------------------------
# STEP 6: MERMAID IMPORTANCE CHART
# ---------------------------------------------------------------------------
def mermaid_chart(ranked: list[ConceptScore], shown: int | None = None) -> str:
    """xychart-beta bar chart of concept_score. NOTE xychart-beta needs a
    recent Mermaid (v10.3+); if your pipeline is older, use mermaid_chart_fallback."""
    xs = " , ".join(f'"{s.concept}"' for s in ranked)
    ys = " , ".join(f"{s.concept_score:.1f}" for s in ranked)
    return (
        "xychart-beta\n"
        '    title "Concept importance (Prep Mode ranking)"\n'
        f"    x-axis [{xs}]\n"
        '    y-axis "concept_score"\n'
        f"    bar [{ys}]"
    )


def mermaid_chart_fallback(ranked: list[ConceptScore], shown: int) -> str:
    """Plain flowchart list for older Mermaid: top `shown` are marked kept."""
    lines = ["flowchart TB"]
    for i, s in enumerate(ranked):
        tag = "  \u2713 shown" if i < shown else "  \u00b7 trimmed"
        lines.append(f'    n{i}["{i+1}. {s.concept} \u2014 {s.concept_score:.1f}{tag}"]')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# STEP: BACKTEST  (precision@k)
# ---------------------------------------------------------------------------
def precision_at_k(
    questions: list[Question], test_year: int, k: int | None = None,
    mode: str = "spec_raw", decay: float = 1.0,
) -> tuple[float, list[str], set[str]]:
    """Train on years < test_year, predict top-k, compare to concepts that
    actually appeared in test_year. k defaults to the <7-day bucket size.
    decay is passed straight through to score_concepts (see its docstring) --
    weighting is computed fresh each fold, off the training set's own max
    year, so no future information leaks into an earlier fold."""
    if k is None:
        k = max(1, round(coverage_fraction(0) * N_CONCEPTS))   # last-mile size
    train = [q for q in questions if q.year < test_year]
    test = [q for q in questions if q.year == test_year]
    if not train or not test:
        raise ValueError(f"Need years both before and equal to {test_year}.")

    predicted = [s.concept for s in score_concepts(train, mode=mode, decay=decay)[:k]]
    actual = {c for q in test for c in q.concepts}
    hits = len(set(predicted) & actual)
    return hits / k, predicted, actual


def walk_forward(
    questions: list[Question], k: int | None = None, mode: str = "spec_raw",
    decay: float = 1.0,
) -> dict:
    """Slide the held-out year forward across every year that has priors.
    Averaged precision@k is a much stronger signal than a single split."""
    years = sorted({q.year for q in questions})
    rows = []
    for ty in years[1:]:
        try:
            p, pred, act = precision_at_k(questions, ty, k=k, mode=mode, decay=decay)
            rows.append({"test_year": ty, "precision": p,
                         "predicted": pred, "actual": sorted(act)})
        except ValueError:
            continue
    mean = sum(r["precision"] for r in rows) / len(rows) if rows else 0.0
    return {"mean_precision": mean, "folds": rows}


# ---------------------------------------------------------------------------
# REPORT HELPERS
# ---------------------------------------------------------------------------
def print_ranking(ranked: list[ConceptScore]) -> None:
    print(f"{'rank':<5}{'concept':<24}{'freq':>5}{'marks':>8}{'score':>9}")
    for i, s in enumerate(ranked, 1):
        print(f"{i:<5}{s.concept:<24}{s.frequency_points:>5}"
              f"{s.marks_weight:>8.0f}{s.concept_score:>9.1f}")


def print_scale_check(questions: list[Question]) -> None:
    """Shows the spec-raw vs normalized rankings side by side so you can see
    whether frequency is doing anything, or marks_weight is swallowing it."""
    raw = score_concepts(questions, mode="spec_raw")
    nrm = score_concepts(questions, mode="normalized")
    raw_order = [s.concept for s in raw]
    nrm_order = [s.concept for s in nrm]
    print("\n-- scale check: does frequency matter? --")
    print(f"{'#':<3}{'spec_raw (freq+marks)':<26}{'normalized 50/50':<26}")
    for i in range(N_CONCEPTS):
        flag = "" if raw_order[i] == nrm_order[i] else "  <-- differs"
        print(f"{i+1:<3}{raw_order[i]:<26}{nrm_order[i]:<26}{flag}")


# ---------------------------------------------------------------------------
# DEMO  (synthetic tagged data, exercises everything but the LLM)
# ---------------------------------------------------------------------------
def _demo_data() -> list[Question]:
    import random
    rng = random.Random(7)
    # made-up "true" popularity so the demo has structure to recover
    weight = {c: w for c, w in zip(CONCEPTS, [3,5,9,10,6,7,4,5,8,3,6,2])}
    pool = [c for c in CONCEPTS for _ in range(weight[c])]
    qs, qid = [], 0
    for year in range(2019, 2025):            # 6 years
        for _ in range(18):                   # ~18 questions/paper
            qid += 1
            k = rng.choice([1, 1, 1, 2])      # mostly single-concept
            cs = list({rng.choice(pool) for _ in range(k)})
            qs.append(Question(str(qid), year, rng.choice([2,5,8,10]),
                               f"synthetic q{qid}", concepts=cs))
    return qs


def _run_demo() -> None:
    print("=== PREP MODE DEMO (synthetic data, no LLM) ===\n")
    qs = _demo_data()
    print(f"{len(qs)} questions across years "
          f"{min(q.year for q in qs)}-{max(q.year for q in qs)}\n")

    ranked = score_concepts(qs, mode="spec_raw")
    print("Master importance ranking (spec_raw):")
    print_ranking(ranked)

    print_scale_check(qs)

    print("\n-- threshold filter --")
    for d in (20, 10, 4):
        shown = filter_by_days_left(ranked, d)
        print(f"days_left={d:>2}  ->  show {len(shown)} concepts: "
              f"{[s.concept for s in shown]}")

    print("\n-- backtest: walk-forward precision@k --")
    wf = walk_forward(qs)
    for f in wf["folds"]:
        print(f"  test {f['test_year']}: precision@k = {f['precision']:.2f}")
    print(f"  mean precision@k = {wf['mean_precision']:.2f}")

    print("\n-- mermaid (xychart-beta) --")
    print(mermaid_chart(ranked))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true",
                    help="run everything but the LLM against synthetic data")
    ap.add_argument("--data", help="path to raw PYQ file (.csv/.json)")
    ap.add_argument("--days-left", type=int, default=10)
    ap.add_argument("--mode", default="spec_raw",
                    choices=["spec_raw", "normalized"])
    args = ap.parse_args()

    if args.demo or not args.data:
        _run_demo()
    else:
        qs = load_questions(args.data)
        qs = tag_questions(qs)                      # uses default_llm_tagger
        ranked = score_concepts(qs, mode=args.mode)
        print_ranking(ranked)
        shown = filter_by_days_left(ranked, args.days_left)
        print(f"\nShowing {len(shown)} concepts for days_left={args.days_left}:")
        print([s.concept for s in shown])
        print("\n" + mermaid_chart(ranked))
        print("\nwalk-forward:", walk_forward(qs, mode=args.mode)["mean_precision"])