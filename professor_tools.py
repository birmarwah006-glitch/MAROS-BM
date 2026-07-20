"""
professor_tools.py — MAROS v4 professor features
─────────────────────────────────────────────────
1. PDF → Quiz pipeline   : prof uploads a PDF of 5-10 questions → LLM parses →
                           prof reviews/edits → publishes → students take it
2. Module quiz review    : prof generates + approves AI questions for a module
                           BEFORE students see them (published quiz overrides autogen)
3. Oak question analytics: what students actually ASK Prof Oak, tagged by OS
                           concept via the existing MiniLM embeddings (no LLM cost)

Wire-up in main.py (one line after app = FastAPI(...)):

    from professor_tools import router as prof_router
    app.include_router(prof_router)
"""

import os
import json
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import requests
from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Query
from pydantic import BaseModel

from config import OUTPUTS_DIR, GROQ_HEADERS, GROQ_BASE_URL, GROQ_CHAT_MODEL
from supabase_layer import (
    get_sb, get_current_user, log_interaction,
    save_quiz_answer, update_mastery,
)

router = APIRouter()

PROF_QUIZZES_DIR = OUTPUTS_DIR / "_prof_quizzes"
PROF_QUIZZES_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# LLM CALLER — same Cerebras-primary / Groq-fallback pattern as main.py
# (duplicated here to avoid a circular import with main)
# ─────────────────────────────────────────────

CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_MODEL   = os.getenv("CEREBRAS_MODEL", "llama-3.3-70b")


def _llm(messages: list, temperature: float = 0.3, max_tokens: int = 4000) -> str:
    if CEREBRAS_API_KEY:
        try:
            res = requests.post(
                "https://api.cerebras.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {CEREBRAS_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model":       CEREBRAS_MODEL,
                    "messages":    messages,
                    "temperature": temperature,
                    "max_tokens":  max_tokens,
                },
                timeout=60,
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[MAROS] Cerebras failed ({e}), falling back to Groq")

    try:
        res = requests.post(
            f"{GROQ_BASE_URL}/chat/completions",
            headers=GROQ_HEADERS,
            json={
                "model":       GROQ_CHAT_MODEL,
                "messages":    messages,
                "temperature": temperature,
                "max_tokens":  max_tokens,
            },
            timeout=60,
        )
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]
    except Exception as e:
        # Print the real reason before it bubbles up — this is the line you're missing.
        # Common causes: GROQ_API_KEY missing/invalid, model doesn't exist,
        # rate limit, or Groq itself down.
        try:
            body = res.text[:500]
        except Exception:
            body = "(no response body — request likely never completed)"
        print(f"[MAROS] Groq ALSO failed: {e} | response body: {body}")
        raise
 

def _extract_json(raw: str) -> dict:
    """Tolerant JSON extraction — survives prose wrapping + markdown fences."""
    import re
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in LLM reply: {cleaned[:150]!r}")
    return json.loads(m.group(0))


# ═════════════════════════════════════════════
# 1) PDF → QUIZ PIPELINE
# ═════════════════════════════════════════════

PARSE_PROMPT = """You are a quiz digitization assistant for a CS professor at VNIT Nagpur (Operating Systems course).

Below is text extracted from a PDF the professor uploaded. It contains quiz/exam questions
(typically 5-10). Extract EVERY question into structured MCQ format.

RULES:
- If the document already gives options (A/B/C/D), keep them EXACTLY as written.
- If the document gives the correct answer, use it and set "answer_source": "document".
- If the correct answer is NOT in the document, determine it yourself using your OS knowledge
  and set "answer_source": "generated".
- If a question is NOT multiple-choice (short answer / descriptive), convert it into a fair MCQ
  with one correct option and three plausible distractors, and set "converted": true.
- Tag each question with the single most relevant OS concept (e.g. "CPU Scheduling",
  "Deadlocks", "Paging", "Virtual Memory", "Process Synchronization", "File Systems").
- Write a one-sentence explanation for the correct answer.

Return ONLY valid JSON, no markdown, no backticks:
{{
  "title": "short quiz title inferred from the document",
  "questions": [
    {{
      "question": "...",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "correct_answer": "A",
      "explanation": "...",
      "concept": "...",
      "answer_source": "document",
      "converted": false
    }}
  ]
}}

DOCUMENT TEXT:
{doc_text}
"""


@router.post("/professor/quiz/parse-pdf")
async def parse_quiz_pdf(file: UploadFile = File(...)):
    """Prof uploads a PDF of questions → returns a structured DRAFT quiz for review.
    Nothing is saved or visible to students until /professor/quiz/publish."""
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Upload a PDF.")

    content = await file.read()
    try:
        import fitz  # PyMuPDF — already a dependency (chat uploads, RAG)
        doc = fitz.open(stream=content, filetype="pdf")
        text = "\n".join(page.get_text("text") for page in doc)
        doc.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF text extraction failed: {e}")

    text = text.strip()
    if len(text) < 40:
        raise HTTPException(
            status_code=422,
            detail="Almost no text found in this PDF — it may be scanned images. Use a text-based PDF.",
        )

    try:
        raw    = _llm(
            [{"role": "user", "content": PARSE_PROMPT.format(doc_text=text[:20000])}],
            temperature=0.2,
            max_tokens=4000,
        )
        parsed = _extract_json(raw)
        questions = parsed.get("questions", [])
        if not questions:
            raise ValueError("LLM returned zero questions")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Question parsing failed: {e}")

    return {
        "draft": True,
        "source_filename": file.filename,
        "title": parsed.get("title") or Path(file.filename).stem,
        "questions": questions,
        "generated_answers": sum(1 for q in questions if q.get("answer_source") == "generated"),
        "converted_to_mcq":  sum(1 for q in questions if q.get("converted")),
    }


class ProfQuizQuestion(BaseModel):
    question:       str
    options:        dict
    correct_answer: str
    explanation:    str = ""
    concept:        Optional[str] = None


class ProfQuizPublish(BaseModel):
    title:     str
    questions: List[ProfQuizQuestion]


@router.post("/professor/quiz/publish")
async def publish_prof_quiz(body: ProfQuizPublish):
    """Save the reviewed/edited quiz. Immediately visible to students."""
    if not body.questions:
        raise HTTPException(status_code=400, detail="Quiz has no questions.")

    quiz_id  = str(uuid.uuid4())[:8]
    quiz_dir = PROF_QUIZZES_DIR / quiz_id
    quiz_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "quiz_id":      quiz_id,
        "title":        body.title,
        "questions":    [q.model_dump() for q in body.questions],
        "visible":      True,
        "published_at": datetime.utcnow().isoformat(),
    }
    (quiz_dir / "quiz.json").write_text(json.dumps(data, indent=2))
    log_interaction(student_id=None, event_type="prof_quiz_published",
                    payload={"quiz_id": quiz_id, "n_questions": len(body.questions)})
    print(f"[MAROS] Prof quiz {quiz_id} published — {len(body.questions)} questions")
    return data


@router.get("/professor/quizzes")
def list_prof_quizzes(visible_only: bool = Query(False)):
    out = []
    for d in PROF_QUIZZES_DIR.iterdir():
        qp = d / "quiz.json"
        if d.is_dir() and qp.exists():
            q = json.loads(qp.read_text())
            if visible_only and not q.get("visible", True):
                continue
            out.append(q)
    out.sort(key=lambda q: q.get("published_at", ""), reverse=True)
    return out


@router.patch("/professor/quizzes/{quiz_id}/visibility")
def toggle_prof_quiz_visibility(quiz_id: str, visible: bool = Query(...)):
    qp = PROF_QUIZZES_DIR / quiz_id / "quiz.json"
    if not qp.exists():
        raise HTTPException(status_code=404, detail="Quiz not found.")
    data = json.loads(qp.read_text())
    data["visible"] = visible
    qp.write_text(json.dumps(data, indent=2))
    return data


@router.delete("/professor/quizzes/{quiz_id}")
def delete_prof_quiz(quiz_id: str):
    qdir = PROF_QUIZZES_DIR / quiz_id
    if not qdir.exists():
        raise HTTPException(status_code=404, detail="Quiz not found.")
    shutil.rmtree(qdir)
    return {"deleted": quiz_id}


@router.get("/quizzes")
def student_list_prof_quizzes():
    """Students: all visible professor-published quizzes."""
    return list_prof_quizzes(visible_only=True)


# ─────────────────────────────────────────────
# STUDENT SUBMISSION — prof quiz taken inside Oak chat
# ─────────────────────────────────────────────
# Writes into the SAME `quiz_answers` table + fires the SAME
# `quiz_complete` interaction as /quiz/submit — so professor_analytics()
# picks these up with zero changes. Uses module_id = f"profquiz_{quiz_id}"
# to keep the namespace disjoint from module quizzes (`{job_id}_mod{NN}`).

import time as _time


class ProfQuizAnswerItem(BaseModel):
    question_index: int
    chosen_answer:  str   # "A" / "B" / "C" / "D"


class ProfQuizSubmit(BaseModel):
    answers: List[ProfQuizAnswerItem]


@router.post("/professor/quiz/{quiz_id}/submit")
async def submit_prof_quiz(quiz_id: str, body: ProfQuizSubmit, request: Request):
    """Student submits answers to a professor-published quiz taken inside Oak chat."""
    qp = PROF_QUIZZES_DIR / quiz_id / "quiz.json"
    if not qp.exists():
        raise HTTPException(status_code=404, detail="Quiz not found.")

    quiz      = json.loads(qp.read_text())
    questions = quiz.get("questions", [])
    if not questions:
        raise HTTPException(status_code=400, detail="Quiz has no questions.")

    user_id = await get_current_user(request)
    start   = _time.time()

    module_key    = f"profquiz_{quiz_id}"
    correct_count = 0
    results       = []   # per-question feedback for the client

    for ans in body.answers:
        if ans.question_index < 0 or ans.question_index >= len(questions):
            continue
        q          = questions[ans.question_index]
        chosen     = (ans.chosen_answer or "").strip().upper()
        correct    = (q.get("correct_answer") or "").strip().upper()
        is_correct = chosen == correct
        concept    = q.get("concept")

        if is_correct:
            correct_count += 1
            if user_id and concept:
                update_mastery(user_id, concept, 0.1)
        else:
            if user_id and concept:
                update_mastery(user_id, concept, -0.15)

        # Explanation authored by the professor IS the misconception feedback —
        # no LLM diagnosis call needed (that was for autogen wrong-answer analysis).
        misconception = q.get("explanation") if not is_correct else None

        save_quiz_answer(
            student_id           = user_id,
            module_id            = module_key,
            question_text        = q.get("question", ""),
            options              = q.get("options", {}),
            chosen_answer        = chosen,
            correct_answer       = correct,
            is_correct           = is_correct,
            concept_id           = concept,
            root_concept_id      = concept,
            misconception        = misconception,
            diagnosis_confidence = 1.0 if not is_correct else None,
        )

        results.append({
            "question_index": ans.question_index,
            "question":       q.get("question", ""),
            "chosen":         chosen,
            "correct_answer": correct,
            "is_correct":     is_correct,
            "explanation":    q.get("explanation", ""),
            "concept":        concept,
        })

    total      = len(body.answers)
    score      = correct_count / total if total else 0
    elapsed_ms = int((_time.time() - start) * 1000)

    # Fire the SAME event shape /quiz/submit fires — analytics counts unify.
    log_interaction(
        student_id       = user_id,
        event_type       = "quiz_complete",
        module_id        = module_key,
        payload          = {
            "quiz_id":      quiz_id,
            "title":        quiz.get("title", ""),
            "is_prof_quiz": True,
            "total":        total,
            "correct":      correct_count,
            "score":        score,
        },
        response_time_ms = elapsed_ms,
    )

    return {
        "quiz_id":      quiz_id,
        "title":        quiz.get("title", ""),
        "total":        total,
        "correct":      correct_count,
        "score":        score,
        "results":      results,
    }


# ═════════════════════════════════════════════
# 2) MODULE QUIZ REVIEW — approve AI questions before students see them
# ═════════════════════════════════════════════
# Flow: prof clicks a module → /professor/module-quiz/review generates a draft →
# prof edits/approves → /professor/module-quiz/publish writes quiz_modNN.json
# into the job's output dir. main.py's /quiz/generate is patched to return the
# published quiz when one exists (see PATCHES.md), so every student gets the
# SAME prof-approved questions instead of a random autogen each time.

class ModuleQuizReviewRequest(BaseModel):
    job_id:        str
    module_id:     int
    num_questions: int = 5


@router.post("/professor/module-quiz/review")
async def review_module_quiz(req: ModuleQuizReviewRequest):
    """Generate a DRAFT quiz for a module (same prompt as autogen) for prof review."""
    manifest_path = OUTPUTS_DIR / req.job_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest not found.")
    data   = json.loads(manifest_path.read_text())
    module = next((m for m in data["modules"] if m["module_id"] == req.module_id), None)
    if not module:
        raise HTTPException(status_code=404, detail=f"Module {req.module_id} not found.")

    prompt = f"""
You are a university CS education assistant at VNIT Nagpur.

CONCEPT: {module['concept']}

NOTES:
{module['notes']}

TRANSCRIPT EXCERPT:
{module['transcript'][:3000]}

Generate exactly {req.num_questions} multiple choice questions testing deep understanding.
Make questions specific, practical, and conceptual — not just definitional.

Return ONLY valid JSON, no markdown, no backticks:
{{
  "topic": "{module['concept']}",
  "questions": [
    {{
      "question": "the question text",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "correct_answer": "A",
      "explanation": "why this answer is correct"
    }}
  ]
}}
"""
    try:
        parsed = _extract_json(_llm([{"role": "user", "content": prompt}],
                                    temperature=0.3, max_tokens=2500))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Draft generation failed: {e}")

    return {
        "draft":     True,
        "job_id":    req.job_id,
        "module_id": req.module_id,
        "topic":     module["concept"],
        "questions": parsed.get("questions", []),
    }


class ModuleQuizPublish(BaseModel):
    job_id:    str
    module_id: int
    topic:     str
    questions: List[ProfQuizQuestion]


@router.post("/professor/module-quiz/publish")
async def publish_module_quiz(body: ModuleQuizPublish):
    """Save the approved module quiz. /quiz/generate serves it from now on."""
    job_dir = OUTPUTS_DIR / body.job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job output not found.")

    quiz = {
        "quiz_id":      str(uuid.uuid4()),
        "module_id":    body.module_id,
        "topic":        body.topic,
        "questions": [
            {
                "module_id":      body.module_id,
                "question":       q.question,
                "options":        q.options,
                "correct_answer": q.correct_answer,
                "explanation":    q.explanation,
            }
            for q in body.questions
        ],
        "generated_at": datetime.utcnow().isoformat(),
        "approved_by_professor": True,
    }
    (job_dir / f"quiz_mod{body.module_id:02d}.json").write_text(json.dumps(quiz, indent=2))
    print(f"[MAROS] Module quiz published — job {body.job_id} module {body.module_id}")
    return quiz


@router.get("/professor/module-quiz/{job_id}/{module_id}")
def get_published_module_quiz(job_id: str, module_id: int):
    qp = OUTPUTS_DIR / job_id / f"quiz_mod{module_id:02d}.json"
    if not qp.exists():
        return {"published": False}
    return {"published": True, **json.loads(qp.read_text())}


# ═════════════════════════════════════════════
# 3) OAK QUESTION ANALYTICS — what students actually ask
# ═════════════════════════════════════════════
# Queries are already logged in interaction_log (event_type='oak_response',
# payload.query). We tag each against OS concepts using the SAME MiniLM
# embedding model the RAG pipeline loads — zero extra LLM cost.

DEFAULT_OS_CONCEPTS = [
    "Processes and Threads",
    "CPU Scheduling",
    "Process Synchronization",
    "Semaphores and Mutexes",
    "Deadlocks",
    "Memory Management",
    "Paging",
    "Segmentation",
    "Virtual Memory",
    "File Systems",
    "Disk Scheduling",
    "I/O Systems",
]

_concept_embed_cache = {"names": None, "vecs": None}


def _concept_list() -> list:
    """OS concepts + every module concept found on disk (deduped)."""
    names = list(DEFAULT_OS_CONCEPTS)
    seen  = {n.lower() for n in names}
    if OUTPUTS_DIR.exists():
        for job_dir in OUTPUTS_DIR.iterdir():
            mp = job_dir / "manifest.json"
            if not mp.exists():
                continue
            try:
                for m in json.loads(mp.read_text()).get("modules", []):
                    c = (m.get("concept") or "").strip()
                    if c and c.lower() not in seen:
                        names.append(c)
                        seen.add(c.lower())
            except Exception:
                pass
    return names


def _tag_queries(queries: list) -> list:
    """Assign each query its closest concept via cosine similarity.
    Returns list of (query, concept, score). Falls back to 'General / Other'."""
    from rag import _get_embedding_model
    import numpy as np

    model = _get_embedding_model()
    names = _concept_list()

    # Cache concept vectors — recompute only if the concept list changed
    if _concept_embed_cache["names"] != names:
        vecs = model.encode(names)
        vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        _concept_embed_cache["names"] = names
        _concept_embed_cache["vecs"]  = vecs

    cvecs = _concept_embed_cache["vecs"]
    qvecs = model.encode(queries)
    qvecs = qvecs / np.linalg.norm(qvecs, axis=1, keepdims=True)

    sims = qvecs @ cvecs.T   # (n_queries, n_concepts)
    out  = []
    for i, q in enumerate(queries):
        j     = int(sims[i].argmax())
        score = float(sims[i][j])
        concept = names[j] if score >= 0.30 else "General / Other"
        out.append((q, concept, round(score, 3)))
    return out


@router.get("/professor/oak-questions")
async def oak_question_analytics(limit: int = Query(800)):
    """
    Summary + analysis of what students ask Prof Oak:
    - questions grouped by OS concept (embedding-tagged)
    - sample questions per concept
    - cross-reference against quiz error rates (asking about X vs failing X)
    """
    sb = get_sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        events = (
            sb.table("interaction_log")
            .select("payload, ts, student_id")
            .eq("event_type", "oak_response")
            .order("ts", desc=True)
            .limit(limit)
            .execute()
        ).data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Log fetch failed: {e}")

    # Extract student queries (skip prof chats + trivial messages)
    queries, meta = [], []
    for e in events:
        p = e.get("payload") or {}
        if p.get("role") == "professor":
            continue
        q = (p.get("query") or "").strip()
        if len(q) < 8 or len(q.split()) < 2:
            continue   # skip "ok", "thanks", "hi"
        queries.append(q)
        meta.append({"ts": e.get("ts"), "student_id": e.get("student_id"), "mode": p.get("mode")})

    if not queries:
        return {"total_questions": 0, "concepts": [], "message": "No Oak questions logged yet."}

    try:
        tagged = _tag_queries(queries)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Concept tagging failed: {e}")

    # ── Aggregate per concept ─────────────────────────────────────────
    buckets = {}
    for (q, concept, score), m in zip(tagged, meta):
        b = buckets.setdefault(concept, {"count": 0, "samples": [], "students": set()})
        b["count"] += 1
        if len(b["samples"]) < 4:
            b["samples"].append({"question": q[:180], "at": m["ts"], "mode": m["mode"]})
        if m["student_id"]:
            b["students"].add(m["student_id"])

    # ── Quiz error rates for cross-reference ──────────────────────────
    quiz_error = {}
    try:
        answers = sb.table("quiz_answers").select("root_concept_id, concept_id, is_correct").limit(5000).execute().data or []
        stats = {}
        for a in answers:
            c = (a.get("root_concept_id") or a.get("concept_id") or "").strip()
            if not c:
                continue
            s = stats.setdefault(c.lower(), {"total": 0, "wrong": 0, "name": c})
            s["total"] += 1
            if not a.get("is_correct"):
                s["wrong"] += 1
        quiz_error = {
            k: {"error_rate": round(v["wrong"] / v["total"], 2), "attempts": v["total"], "name": v["name"]}
            for k, v in stats.items() if v["total"] > 0
        }
    except Exception as e:
        print(f"[MAROS] Quiz cross-ref skipped: {e}")

    total = len(queries)
    concepts = []
    for name, b in buckets.items():
        qe = quiz_error.get(name.lower())
        concepts.append({
            "concept":          name,
            "count":            b["count"],
            "pct":              round(b["count"] / total * 100),
            "unique_students":  len(b["students"]),
            "samples":          b["samples"],
            "quiz_error_rate":  qe["error_rate"] if qe else None,
            "quiz_attempts":    qe["attempts"]   if qe else 0,
        })
    concepts.sort(key=lambda c: -c["count"])

    return {
        "total_questions": total,
        "window":          f"last {min(limit, len(events))} Oak interactions",
        "concepts":        concepts,
    }