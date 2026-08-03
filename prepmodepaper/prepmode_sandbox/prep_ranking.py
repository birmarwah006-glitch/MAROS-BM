"""
prep_rankings.py  —  BUILD-TIME step for Prof Oak Prep Mode.

Turns the hand-tagged PYQ dataset into two frozen, ranked concept files that
the live server reads at runtime:

    data/rankings_midsem.json
    data/rankings_endsem.json

This is the prep-mode equivalent of chipper writing manifest.json: a student
NEVER computes a ranking on the fly. You (or Prof Praveen) add papers ->
rerun build_data.py -> rerun this -> the app serves the new rankings. Nothing
downstream of here touches an LLM or the scoring math; it's just a lookup.

Why precompute instead of scoring per-request:
  - deterministic + spot-checkable (same reason tagging is cached)
  - the score depends on the WHOLE corpus, not the student, so it's identical
    for all ~130 students — computing it per request would be wasteful and
    would let a ranking drift between two students in the same cohort.

Decay per exam_type is set from the honest walk-forward tuning (tune_decay.py):
  endsem -> 0.3   (recency decay measurably helped the endsem mean)
  midsem -> 1.0   (too few midsem years to tune a decay we'd trust; leave off)

Run:  python prep_rankings.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from prep_mode import (
    load_questions, score_concepts, CONCEPTS, N_CONCEPTS,
)

DATA_DIR = Path(__file__).parent / "data"
ANALYSIS_FILE = DATA_DIR / "questions_analysis.json"  # midsem+endsem only

# exam_type -> chosen decay (see tune_decay.py rationale in the module docstring)
DECAY_BY_EXAM = {
    "midsem": 1.0,
    "endsem": 0.3,
}
SCORE_MODE = "normalized"  # matches the tuning runs; makes frequency actually count


def _appearance_stats(questions, concept: str) -> tuple[int, int]:
    """Raw (UN-decayed) count of distinct papers this concept appears in, and
    the total number of distinct papers in this subset. 'Paper' = one (year)
    sitting of this exam_type — a clean, honest 'showed up in N of M papers'
    figure for the student-facing justification, independent of the decay
    weighting that drives the score itself."""
    years_with = {q.year for q in questions if concept in q.concepts}
    total_years = {q.year for q in questions}
    return len(years_with), len(total_years)


def build_ranking(exam_type: str, questions) -> dict:
    subset = [q for q in questions if q.exam_type == exam_type]
    if not subset:
        return {"exam_type": exam_type, "concepts": [], "total_papers": 0,
                "years": [], "decay": DECAY_BY_EXAM.get(exam_type, 1.0),
                "mode": SCORE_MODE,
                "generated_at": datetime.now(timezone.utc).isoformat()}

    decay = DECAY_BY_EXAM.get(exam_type, 1.0)
    ranked = score_concepts(subset, mode=SCORE_MODE, decay=decay)
    years = sorted({q.year for q in subset})

    concepts_out = []
    for rank, s in enumerate(ranked, 1):
        appeared, total_papers = _appearance_stats(subset, s.concept)
        concepts_out.append({
            "rank": rank,
            "concept": s.concept,
            "label": s.concept.replace("-", " "),   # human-friendly for UI/chat
            "gloss": CONCEPTS[s.concept],
            "concept_score": round(s.concept_score, 4),
            "frequency_points": round(s.frequency_points, 3),
            "marks_weight": round(s.marks_weight, 1),
            "papers_appeared": appeared,
            "total_papers": total_papers,
            "appearance_rate": round(appeared / total_papers, 3) if total_papers else 0.0,
        })

    return {
        "exam_type": exam_type,
        "decay": decay,
        "mode": SCORE_MODE,
        "total_papers": len(years),
        "years": years,
        "n_concepts": N_CONCEPTS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "concepts": concepts_out,
    }


def main() -> None:
    if not ANALYSIS_FILE.exists():
        raise SystemExit(
            f"{ANALYSIS_FILE} not found — run build_data.py first so the "
            f"midsem+endsem analysis file exists."
        )
    questions = load_questions(str(ANALYSIS_FILE))

    for exam_type in ("midsem", "endsem"):
        ranking = build_ranking(exam_type, questions)
        out = DATA_DIR / f"rankings_{exam_type}.json"
        out.write_text(json.dumps(ranking, indent=2))
        top = ", ".join(c["concept"] for c in ranking["concepts"][:5])
        print(f"wrote {out.name}: {len(ranking['concepts'])} concepts, "
              f"{ranking['total_papers']} papers ({ranking['years']}), "
              f"decay={ranking['decay']}")
        print(f"    top 5: {top}")


if __name__ == "__main__":
    main()