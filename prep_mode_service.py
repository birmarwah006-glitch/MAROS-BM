"""
prep_mode_service.py — RUNTIME half of Prof Oak Prep Mode.

v2: FREE NAVIGATION. The student can jump to any concept in the plan at any
time — no more locked linear queue. Progress is tracked as a completed-set,
not an index. The tree UI calls get_prep_tree() to render every concept with
its status (done / current / pending) and its priority tier for coloring;
clicking a node calls jump_to_concept() to make it the one Oak teaches next.

Two things live here:

  1. Prep session state  (Supabase table `prep_sessions`, see prep_sessions.sql)
       - one row per (student, exam_type); mirrors how oak_chats is keyed
       - holds the finalized concept list + which ones are done + which one
         is currently active
       - only ONE session is 'active' at a time (the most recently touched);
         starting/resuming an exam flips it active, so /chat is never ambiguous
         about which exam it's teaching toward, while progress on the other
         exam is preserved rather than wiped.

  2. build_prep_context()  — given the student, returns the teaching block for
       the CURRENT concept (with past-paper RAG grounding) that gets appended
       to Oak's context, plus the concept id so /chat can mark it done/scored.

The rankings themselves are read-only build artifacts (prep_rankings.py) —
this module never scores anything, it just serves the frozen ranking + tracks
where each student is in it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, date
from pathlib import Path
from functools import lru_cache
from typing import Optional

from supabase_layer import get_sb
from prep_mode import (
    load_questions, filter_by_days_left, coverage_fraction, N_CONCEPTS,
)

DATA_DIR = Path(__file__).parent / "data"
VALID_EXAM_TYPES = ("midsem", "endsem")


# ─────────────────────────────────────────────
# RANKINGS  (read-only build artifacts)
# ─────────────────────────────────────────────

@lru_cache(maxsize=4)
def _load_ranking(exam_type: str) -> dict:
    """Load a frozen ranking file. Cached — it never changes between rebuilds,
    and a rebuild restarts the server anyway."""
    path = DATA_DIR / f"rankings_{exam_type}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing — run prep_rankings.py to generate it."
        )
    return json.loads(path.read_text())


def _concept_meta(exam_type: str, concept_id: str) -> Optional[dict]:
    for c in _load_ranking(exam_type)["concepts"]:
        if c["concept"] == concept_id:
            return c
    return None


def compute_days_left(target_date_iso: str) -> int:
    """Whole days from today to the exam date. Never negative for planning —
    caller decides what to do with a past date."""
    target = date.fromisoformat(target_date_iso[:10])
    return (target - date.today()).days


def _candidate_concepts(exam_type: str, days_left: int) -> list[dict]:
    """The concepts prep mode will OFFER for this exam + time budget, in
    importance order. Uses the exact same time-threshold math as the backtest
    (filter_by_days_left) so what a student is shown matches what the algorithm
    was validated on — fewer, higher-yield concepts when the exam is close."""
    ranking = _load_ranking(exam_type)
    concepts = ranking["concepts"]

    frac = coverage_fraction(days_left)
    n_show = max(1, round(frac * N_CONCEPTS))
    return concepts[:n_show]


# ─────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────

def _active_session(sb, owner_key: str) -> Optional[dict]:
    """Most recently touched 'active' session for this student, if any."""
    try:
        rows = (
            sb.table("prep_sessions").select("*")
            .eq("owner_key", owner_key).eq("status", "active")
            .order("updated_at", desc=True).limit(1)
            .execute()
        ).data or []
        return rows[0] if rows else None
    except Exception as e:
        print(f"[MAROS] prep active-session fetch failed: {e}")
        return None


def _get_row(sb, owner_key: str, exam_type: str) -> Optional[dict]:
    try:
        rows = (
            sb.table("prep_sessions").select("*")
            .eq("owner_key", owner_key).eq("exam_type", exam_type)
            .limit(1).execute()
        ).data or []
        return rows[0] if rows else None
    except Exception as e:
        print(f"[MAROS] prep row fetch failed: {e}")
        return None


def _upsert(sb, row: dict) -> None:
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    sb.table("prep_sessions").upsert(row, on_conflict="owner_key,exam_type").execute()


def _deactivate_others(sb, owner_key: str, keep_exam: str) -> None:
    """Only one active session at a time — pause any other active exam so /chat
    is unambiguous. Progress is kept (status just flips to 'paused')."""
    try:
        (sb.table("prep_sessions").update({"status": "paused"})
         .eq("owner_key", owner_key).eq("status", "active")
         .neq("exam_type", keep_exam).execute())
    except Exception as e:
        print(f"[MAROS] prep deactivate-others failed (non-fatal): {e}")


def start_prep_session(owner_key: str, exam_type: str, target_date_iso: str) -> dict:
    """Step 1 of kickoff: compute the time budget, pull the candidate concepts
    for the student to review/deselect. Writes a 'planning' row. Returns the
    candidate list for the deselection UI — does NOT lock the plan yet."""
    if exam_type not in VALID_EXAM_TYPES:
        raise ValueError(f"exam_type must be one of {VALID_EXAM_TYPES}")
    sb = get_sb()
    if not sb:
        raise RuntimeError("Supabase not configured")

    days_left = compute_days_left(target_date_iso)
    candidates = _candidate_concepts(exam_type, max(days_left, 0))
    ranking = _load_ranking(exam_type)

    _upsert(sb, {
        "owner_key": owner_key,
        "exam_type": exam_type,
        "target_date": target_date_iso[:10],
        "status": "planning",
        "candidates": candidates,
        "queue": [],
        "current_concept": None,
        "completed": [],
        "weak_concepts": [],
        "revisit_added": False,
    })

    return {
        "exam_type": exam_type,
        "days_left": days_left,
        "coverage_note": _coverage_note(days_left),
        "total_papers": ranking["total_papers"],
        "years": ranking["years"],
        "candidates": candidates,   # full concept dicts (rank, label, gloss, stats)
    }


def finalize_prep_plan(owner_key: str, exam_type: str,
                       deselected: list[str] | None = None) -> dict:
    """Step 2 of kickoff: lock the plan = candidates minus whatever the student
    already feels solid on. Activates the session (and pauses any other active
    exam). Returns the plan + the top concept to start on (student can jump
    to any other one from the tree instead)."""
    sb = get_sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    row = _get_row(sb, owner_key, exam_type)
    if not row:
        raise ValueError("No planning session — call start_prep_session first.")

    deselected = set(deselected or [])
    queue = [c["concept"] for c in row.get("candidates", [])
             if c["concept"] not in deselected]
    if not queue:  # student deselected everything — keep the single top concept
        queue = [row["candidates"][0]["concept"]] if row.get("candidates") else []

    row.update({
        "queue": queue,
        "current_concept": queue[0] if queue else None,
        "completed": [],
        "status": "active",
        "weak_concepts": [],
        "revisit_added": False,
    })
    _upsert(sb, row)
    _deactivate_others(sb, owner_key, keep_exam=exam_type)

    first = _concept_meta(exam_type, queue[0]) if queue else None
    return {"exam_type": exam_type, "queue": queue, "total": len(queue),
            "first_concept": first}


def get_prep_session(owner_key: str, exam_type: str | None = None) -> dict:
    """Resume payload. If exam_type is given, returns that row; otherwise the
    currently-active one. Flags a target date that has already passed so the UI
    can offer a restart instead of teaching toward a dead deadline."""
    sb = get_sb()
    if not sb:
        return {"active": False, "reason": "supabase not configured"}

    row = _get_row(sb, owner_key, exam_type) if exam_type else _active_session(sb, owner_key)
    if not row:
        return {"active": False}

    days_left = compute_days_left(row["target_date"]) if row.get("target_date") else None
    queue = row.get("queue", [])
    completed = row.get("completed", []) or []
    current_id = row.get("current_concept")
    current = _concept_meta(row["exam_type"], current_id) if current_id else None

    return {
        "active": row.get("status") == "active",
        "status": row.get("status"),
        "exam_type": row["exam_type"],
        "target_date": row.get("target_date"),
        "days_left": days_left,
        "expired": days_left is not None and days_left < 0,
        "queue": queue,
        "total": len(queue),
        "completed_count": len(completed),
        "current_concept": current,
        "done": bool(queue) and len(completed) >= len(queue),
    }


def get_prep_tree(owner_key: str, exam_type: str) -> dict:
    """Everything the tree UI needs to render: every concept in the locked
    plan with its rank/score (for the priority-tier color), its status
    (done / current / pending), plus overall progress. Tiers are computed by
    position in the ranked plan (top third / middle third / bottom third) so
    the coloring always reflects THIS student's plan, not the global ranking."""
    sb = get_sb()
    if not sb:
        return {"nodes": [], "total": 0, "completed_count": 0}

    row = _get_row(sb, owner_key, exam_type)
    if not row or not row.get("queue"):
        return {"nodes": [], "total": 0, "completed_count": 0}

    queue = row["queue"]
    completed = set(row.get("completed", []) or [])
    current_id = row.get("current_concept")
    n = len(queue)

    nodes = []
    for i, concept_id in enumerate(queue):
        meta = _concept_meta(exam_type, concept_id) or {}
        # tier by position in THIS plan (0 = highest priority)
        if i < n / 3:
            tier = "high"
        elif i < 2 * n / 3:
            tier = "medium"
        else:
            tier = "low"

        status = "done" if concept_id in completed else (
            "current" if concept_id == current_id else "pending"
        )

        nodes.append({
            "concept": concept_id,
            "label": meta.get("label", concept_id.replace("-", " ")),
            "rank": meta.get("rank"),
            "concept_score": meta.get("concept_score"),
            "papers_appeared": meta.get("papers_appeared"),
            "total_papers": meta.get("total_papers"),
            "tier": tier,
            "status": status,
        })

    return {
        "nodes": nodes,
        "total": n,
        "completed_count": len(completed),
    }


def jump_to_concept(owner_key: str, exam_type: str, concept_id: str) -> dict:
    """Student clicked a node in the tree — make that the concept Oak teaches
    next. Must be a concept in the locked plan for this exam."""
    sb = get_sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    row = _get_row(sb, owner_key, exam_type)
    if not row or not row.get("queue"):
        raise ValueError("No active prep plan for this exam.")
    if concept_id not in row["queue"]:
        raise ValueError(f"'{concept_id}' is not in this plan.")

    row["current_concept"] = concept_id
    row["status"] = "active"
    _upsert(sb, row)
    _deactivate_others(sb, owner_key, keep_exam=exam_type)

    meta = _concept_meta(exam_type, concept_id) or {}
    return {"exam_type": exam_type, "current_concept": meta}


# ─────────────────────────────────────────────
# TEACHING CONTEXT  (called by /chat when mode == "prep")
# ─────────────────────────────────────────────

def build_prep_context(owner_key: Optional[str]) -> tuple[str, Optional[str]]:
    """Return (context_block, current_concept_id) for the student's active prep
    session. Empty/None when there's no active session — the /chat handler
    just gets no extra context in that case.

    The context tells Oak WHICH single concept to teach now, why it matters for
    the exam (appearance stats + rank), the concept's scope (gloss), and grounds
    it in real past-paper material pulled from the full teaching corpus in
    ChromaDB (year papers, solutions, topic notes — everything, not just the
    analysis-eligible papers)."""
    if not owner_key:
        return "", None
    sb = get_sb()
    if not sb:
        return "", None

    row = _active_session(sb, owner_key)
    if not row:
        return "", None

    concept_id = row.get("current_concept")
    if not concept_id:
        return "", None

    exam_type = row["exam_type"]
    meta = _concept_meta(exam_type, concept_id) or {}
    label = meta.get("label", concept_id.replace("-", " "))
    gloss = meta.get("gloss", "")
    rank = meta.get("rank", "?")
    appeared = meta.get("papers_appeared", "?")
    total_papers = meta.get("total_papers", "?")

    days_left = compute_days_left(row["target_date"]) if row.get("target_date") else None
    when = f"{days_left} days away" if days_left is not None else "coming up"

    queue = row.get("queue", [])
    completed_count = len(row.get("completed", []) or [])

    # Ground the explanation in real past-paper material for THIS concept.
    rag_block = ""
    try:
        from rag import build_rag_context
        rag_query = f"{label}: {gloss[:160]}"
        rag = build_rag_context(rag_query, n_results=5)
        if rag:
            rag_block = (
                "\n\nGROUND YOUR EXPLANATION IN THIS PAST-PAPER MATERIAL "
                "(quote/adapt exam phrasing where useful):\n" + rag[:4000] + "\n"
            )
    except Exception as e:
        print(f"[MAROS] prep RAG grounding skipped (non-fatal): {e}")

    context = f"""
PREP MODE — focused {exam_type} exam prep. The exam is {when}.
The student has completed {completed_count} of {len(queue)} concepts in their plan and picked this one to study now.

TEACH ONLY THIS CONCEPT NOW:
CONCEPT: {label}
WHY IT MATTERS: importance rank #{rank} for {exam_type}; appeared in {appeared} of the last {total_papers} {exam_type} papers.
SCOPE OF THIS CONCEPT: {gloss}{rag_block}

Teach it with the What it is / How it works / Example / Exam angle structure, then end with ONE Quick Check styled like a real exam question on this concept. Do NOT move to another concept yourself — the student picks the next one from their plan once they pass the Quick Check.
"""
    return context, concept_id


def mark_prep_struggle(owner_key: str, concept_id: str) -> None:
    """Record that the student missed the Quick Check on this concept at least
    once. Purely informational for now (surfaced as a subtle flag, not a
    forced revisit lap, since navigation is free-form). Idempotent."""
    sb = get_sb()
    if not sb or not owner_key:
        return
    row = _active_session(sb, owner_key)
    if not row:
        return
    weak = row.get("weak_concepts", []) or []
    if concept_id not in weak:
        weak.append(concept_id)
        row["weak_concepts"] = weak
        try:
            _upsert(sb, row)
        except Exception as e:
            print(f"[MAROS] prep mark-struggle failed (non-fatal): {e}")


def mark_prep_concept_done(owner_key: str, concept_id: str) -> dict:
    """Student passed the current Quick Check → mark this concept complete.
    Does NOT auto-advance to another concept — free navigation means the
    student picks the next one from the tree. Returns updated progress."""
    sb = get_sb()
    if not sb or not owner_key:
        return {"marked": False}
    row = _active_session(sb, owner_key)
    if not row:
        return {"marked": False}

    completed = row.get("completed", []) or []
    if concept_id not in completed:
        completed.append(concept_id)
    row["completed"] = completed

    queue = row.get("queue", [])
    all_done = bool(queue) and len(completed) >= len(queue)
    if all_done:
        row["status"] = "done"

    try:
        _upsert(sb, row)
    except Exception as e:
        print(f"[MAROS] prep mark-done failed (non-fatal): {e}")
        return {"marked": False}

    return {"marked": True, "completed_count": len(completed),
            "total": len(queue), "all_done": all_done}


def _coverage_note(days_left: int) -> str:
    """Plain-language version of the time-threshold bucket, for the kickoff UI."""
    if days_left > 14:
        return "plenty of time — covering the broad high-yield set"
    if days_left >= 7:
        return "about a week — focusing on the top concepts"
    return "crunch time — drilling only the highest-yield concepts"
