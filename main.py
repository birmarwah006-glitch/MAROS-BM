# main.py
import os
import uuid
import shutil
import time
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import (
    HOST, PORT,
    UPLOADS_DIR,
    OUTPUTS_DIR,
    CORS_ORIGINS,
    GROQ_HEADERS,
    GROQ_BASE_URL,
    GROQ_CHAT_MODEL
)
from models import (
    Job, Manifest,
    QuizGenerateRequest, Quiz, QuizQuestion,
    ChatRequest, ChatMessage, ChatRole
)
import jobs
import chipper
import requests
import json

from podcastengine import generate_podcast, extract_from_pdf

# ─── Supabase v2 layer ───────────────────────────────────────────────────────
from supabase_layer import (
    get_current_user,
    require_user,
    log_interaction,
    get_student_mastery,
    update_mastery,
    get_ready_to_learn,
    build_diagnosis_prompt,
    save_quiz_answer,
    build_mastery_context,
    web_search,
)

from rag import build_rag_context, get_rag_stats


# ─── New request/response models for v2 ──────────────────────────────────────

class QuizSubmitAnswer(BaseModel):
    question_text:  str
    options:        dict
    chosen_answer:  str
    correct_answer: str
    concept_id:     Optional[str] = None

class QuizSubmitRequest(BaseModel):
    job_id:    str
    module_id: int
    answers:   List[QuizSubmitAnswer]

class QuizSubmitResult(BaseModel):
    total:          int
    correct:        int
    score:          float
    misconceptions: List[dict]

class YouTubeIngestRequest(BaseModel):
    url: str


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────

app = FastAPI(
    title       = "MAROS — Marwah Operating System",
    description = "Backend server for AdaptLearn · VNIT Nagpur",
    version     = "1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = CORS_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"]
)

PAPERS_DIR      = OUTPUTS_DIR / "_papers"
ASSIGNMENTS_DIR = OUTPUTS_DIR / "_assignments"
PAPERS_DIR.mkdir(parents=True, exist_ok=True)
ASSIGNMENTS_DIR.mkdir(parents=True, exist_ok=True)

# ── v4: professor tools (PDF→quiz, quiz review, Oak question analytics) ──
from professor_tools import router as prof_router
app.include_router(prof_router)


# ─────────────────────────────────────────────
# ROOT
# ─────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "system"  : "MAROS",
        "status"  : "online",
        "version" : "1.0.0"
    }


# ─────────────────────────────────────────────
# JOBS
# ─────────────────────────────────────────────

@app.post("/jobs", response_model=Job)
async def submit_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    allowed = {"video/mp4", "video/quicktime", "video/x-msvideo",
               "audio/mpeg", "audio/wav", "audio/x-wav"}
    if file.content_type not in allowed:
        raise HTTPException(
            status_code = 400,
            detail      = f"Unsupported file type: {file.content_type}. Use MP4, MOV, AVI, MP3, or WAV."
        )

    job       = jobs.create_job()
    suffix    = Path(file.filename).suffix
    save_path = UPLOADS_DIR / f"{job.job_id}{suffix}"

    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    print(f"[MAROS] Job {job.job_id} created — file saved to {save_path}")
    background_tasks.add_task(chipper.run_pipeline, save_path, job.job_id)
    return job


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    return job


@app.get("/jobs/{job_id}/manifest", response_model=Manifest)
def get_manifest(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    if job.status != "done":
        raise HTTPException(status_code=202, detail=f"Job is still {job.status}.")

    manifest_path = OUTPUTS_DIR / job_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest file not found on disk.")

    return Manifest.model_validate_json(manifest_path.read_text())


@app.get("/lectures")
def list_lectures():
    """All processed lectures (any manifest.json on disk), newest first.
    Survives server restarts — reads directly from disk, not the in-memory job store."""
    lectures = []
    if OUTPUTS_DIR.exists():
        for job_dir in OUTPUTS_DIR.iterdir():
            manifest_path = job_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text())
                first_concept = data["modules"][0]["concept"] if data.get("modules") else "Lecture"
                yt_path = job_dir / "youtube.json"
                source  = "upload"
                title   = first_concept
                if yt_path.exists():
                    source = "youtube"
                    yt_meta = json.loads(yt_path.read_text())
                    if yt_meta.get("title"):
                        title = yt_meta["title"]
                lectures.append({
                    "job_id":        data["job_id"],
                    "title":         title,
                    "total_modules": data.get("total_modules", len(data.get("modules", []))),
                    "generated_at":  data.get("generated_at"),
                    "source":        source,
                    "modules": [
                        {"module_id": m["module_id"], "concept": m["concept"]}
                        for m in data.get("modules", [])
                    ],
                })
            except Exception as e:
                print(f"[MAROS] Skipping bad manifest {job_dir.name}: {e}")

    lectures.sort(key=lambda l: l.get("generated_at") or "", reverse=True)
    return lectures


# ─────────────────────────────────────────────
# MODULES
# ─────────────────────────────────────────────

@app.get("/modules/{job_id}")
def get_modules(job_id: str):
    manifest_path = OUTPUTS_DIR / job_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest not found.")
    data = json.loads(manifest_path.read_text())
    return data["modules"]


@app.get("/modules/{job_id}/{module_id}/video")
def get_module_video(job_id: str, module_id: int):
    job_dir = OUTPUTS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job output not found.")
    matches = list(job_dir.glob(f"Module_{module_id:02d}_*.mp4"))
    if not matches:
        raise HTTPException(status_code=404, detail=f"Video for module {module_id} not found.")
    return FileResponse(path=str(matches[0]), media_type="video/mp4", filename=matches[0].name)


@app.get("/modules/{job_id}/{module_id}/notes")
def get_module_notes(job_id: str, module_id: int):
    job_dir = OUTPUTS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job output not found.")
    matches = list(job_dir.glob(f"Module_{module_id:02d}_*_notes.txt"))
    if not matches:
        raise HTTPException(status_code=404, detail=f"Notes for module {module_id} not found.")
    return {"module_id": module_id, "notes": matches[0].read_text()}


# ─────────────────────────────────────────────
# QUIZ
# ─────────────────────────────────────────────
@app.post("/quiz/generate", response_model=Quiz)
async def generate_quiz(req: QuizGenerateRequest):
    published = OUTPUTS_DIR / req.job_id / f"quiz_mod{req.module_id:02d}.json"
    if published.exists():
        data = json.loads(published.read_text())
        return Quiz(
            quiz_id      = data["quiz_id"],
            module_id    = data["module_id"],
            topic        = data["topic"],
            questions    = [QuizQuestion(**q) for q in data["questions"]],
            generated_at = datetime.utcnow(),
        )

    manifest_path = OUTPUTS_DIR / req.job_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest not found.")

    data    = json.loads(manifest_path.read_text())
    modules = data["modules"]
    module  = next((m for m in modules if m["module_id"] == req.module_id), None)
    if not module:
        raise HTTPException(status_code=404, detail=f"Module {req.module_id} not found.")

    concept    = module["concept"]
    notes      = module["notes"][:4000]        # ← FIX: cap notes, was uncapped
    transcript = module["transcript"]

    prompt = f"""
You are a university CS education assistant at VNIT Nagpur.

CONCEPT: {concept}

NOTES:
{notes}

TRANSCRIPT EXCERPT:
{transcript[:3000]}

Generate exactly {req.num_questions} multiple choice questions testing deep understanding.
Make questions specific, practical, and conceptual — not just definitional.

Return ONLY valid JSON, no markdown, no backticks:
{{
  "topic": "{concept}",
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

    # FIX: request json_object mode + bigger token budget, with one retry
    def _generate_once():
        res = requests.post(
            "https://api.cerebras.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": CEREBRAS_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 3000,                      # ← FIX: was 2048
                "response_format": {"type": "json_object"},  # ← FIX: force valid JSON
            },
            timeout=60,
        )
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]

    text = None
    for attempt in range(2):
        try:
            text = _generate_once()
            break
        except Exception as e:
            print(f"[MAROS] Quiz gen attempt {attempt+1} failed for module {req.module_id}: {e}")
            if attempt == 1:
                raise HTTPException(status_code=502, detail=f"Quiz generation failed: {e}")

    try:
        parsed = _extract_json_object(text)   # ← FIX: tolerant extraction, not strict json.loads
        questions = [
            QuizQuestion(
                module_id      = req.module_id,
                question       = q["question"],
                options        = q["options"],
                correct_answer = q["correct_answer"],
                explanation    = q["explanation"]
            )
            for q in parsed["questions"]
        ]
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse quiz response: {e}")

    return Quiz(
        quiz_id      = str(uuid.uuid4()),
        module_id    = req.module_id,
        topic        = concept,
        questions    = questions,
        generated_at = datetime.utcnow()
    )

# ─────────────────────────────────────────────
# QUIZ SUBMIT — v2: saves answers + diagnoses misconceptions
# v3.1 FIX: diagnosis hardened — bigger token budget, tolerant JSON
# extraction, and a guaranteed fallback so students ALWAYS see feedback
# on wrong answers even if the LLM call fails.
# ─────────────────────────────────────────────

import re as _re_diag

def _extract_json_object(raw: str) -> dict:
    """Pull the first {...} JSON object out of an LLM reply, tolerating
    prose before/after and markdown fences. Raises on failure."""
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    m = _re_diag.search(r"\{.*\}", cleaned, _re_diag.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in: {cleaned[:120]!r}")
    return json.loads(m.group(0))


@app.post("/quiz/submit", response_model=QuizSubmitResult)
async def submit_quiz(
    req: QuizSubmitRequest,
    request: Request,
):
    user_id = await get_current_user(request)
    start   = time.time()

    correct_count  = 0
    misconceptions = []

    # Get module concept for context
    module_concept = None
    manifest_path  = OUTPUTS_DIR / req.job_id / "manifest.json"
    if manifest_path.exists():
        data   = json.loads(manifest_path.read_text())
        module = next((m for m in data["modules"] if m["module_id"] == req.module_id), None)
        if module:
            module_concept = module["concept"]

    for i, ans in enumerate(req.answers):
        is_correct = ans.chosen_answer.strip().upper() == ans.correct_answer.strip().upper()
        if is_correct:
            correct_count += 1

        root_concept_id  = None
        misconception    = None
        diagnosis_conf   = None

        # ── Diagnose wrong answers with LLM ──────────────────────────
        if not is_correct:
            concept_label = ans.concept_id or module_concept or "unknown"
            try:
                diag_prompt = build_diagnosis_prompt(
                    question = ans.question_text,
                    chosen   = ans.chosen_answer,
                    correct  = ans.correct_answer,
                    concept  = concept_label,
                )
                diag_raw = _call_llm(
                    messages    = [{"role": "user", "content": diag_prompt}],
                    temperature = 0.2,
                    max_tokens  = 500,   # FIX: was 300 — truncated JSON killed the parse
                )
                # FIX: tolerant extraction — survives prose wrapping + fences
                diag = _extract_json_object(diag_raw)

                root_concept_id = diag.get("root_concept", concept_label)
                misconception   = diag.get("misconception", "")
                diagnosis_conf  = diag.get("confidence", 0.5)

                diag["question_num"] = i + 1
                misconceptions.append(diag)

                # Update mastery — decay on wrong answer
                if user_id:
                    update_mastery(user_id, root_concept_id, -0.15 * diagnosis_conf)

            except Exception as e:
                print(f"[MAROS] Diagnosis failed (non-fatal): {e}")
                # FIX: guaranteed fallback — student ALWAYS sees per-question
                # feedback even when the diagnosis LLM call fails.
                root_concept_id = concept_label
                misconception   = (
                    f"You chose {ans.chosen_answer}, but the correct answer "
                    f"was {ans.correct_answer}. Review this concept: {concept_label}."
                )
                diagnosis_conf  = 0.0
                misconceptions.append({
                    "question_num":  i + 1,
                    "root_concept":  concept_label,
                    "misconception": misconception,
                    "confidence":    0.0,
                })
                # Still decay mastery a little — the answer WAS wrong
                if user_id:
                    update_mastery(user_id, concept_label, -0.05)

        else:
            # Correct answer → boost mastery
            if user_id and (ans.concept_id or module_concept):
                update_mastery(user_id, ans.concept_id or module_concept, 0.1)

        # ── Save answer to Supabase ───────────────────────────────────
        save_quiz_answer(
            student_id           = user_id,
            module_id            = f"{req.job_id}_mod{req.module_id:02d}",
            question_text        = ans.question_text,
            options              = ans.options,
            chosen_answer        = ans.chosen_answer,
            correct_answer       = ans.correct_answer,
            is_correct           = is_correct,
            concept_id           = ans.concept_id or module_concept,
            root_concept_id      = root_concept_id,
            misconception        = misconception,
            diagnosis_confidence = diagnosis_conf,
        )

    # ── Log the quiz completion ───────────────────────────────────────
    elapsed_ms = int((time.time() - start) * 1000)
    score      = correct_count / len(req.answers) if req.answers else 0

    log_interaction(
        student_id      = user_id,
        event_type      = "quiz_complete",
        module_id       = f"{req.job_id}_mod{req.module_id:02d}",
        payload         = {
            "total":          len(req.answers),
            "correct":        correct_count,
            "score":          score,
            "misconceptions": misconceptions,
        },
        response_time_ms = elapsed_ms,
    )

    return QuizSubmitResult(
        total          = len(req.answers),
        correct        = correct_count,
        score          = score,
        misconceptions = misconceptions,
    )


# ─────────────────────────────────────────────
# YOUTUBE INGEST — paste a link instead of uploading
# ─────────────────────────────────────────────

@app.post("/jobs/youtube", response_model=Job)
async def ingest_youtube(
    req: YouTubeIngestRequest,
    background_tasks: BackgroundTasks,
):
    """Download audio from YouTube link → feed into Chipper pipeline.
    Video itself is NOT downloaded — students watch via YouTube embed."""
    job       = jobs.create_job()
    save_path = UPLOADS_DIR / f"{job.job_id}.mp3"

    # Extract YouTube video ID and save as sidecar for the student player
    import re as _re
    vid_match = _re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})", req.url)
    video_id  = vid_match.group(1) if vid_match else None

    job_out_dir = OUTPUTS_DIR / job.job_id
    job_out_dir.mkdir(parents=True, exist_ok=True)

    # Get the real video title (fast metadata-only call)
    video_title = None
    try:
        title_res = subprocess.run(
            ["yt-dlp", "--print", "title", "--no-playlist", "--skip-download", req.url],
            capture_output=True, text=True, timeout=30,
        )
        video_title = (title_res.stdout or "").strip().splitlines()[0] if title_res.stdout else None
    except Exception as e:
        print(f"[MAROS] Could not fetch YT title: {e}")

    (job_out_dir / "youtube.json").write_text(json.dumps({
        "source": "youtube",
        "url": req.url,
        "video_id": video_id,
        "title": video_title,
    }))

    def _download_and_process(url: str, output: Path, job_id: str):
        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "-x",                         # extract audio only
                    "--audio-format", "mp3",
                    "--audio-quality", "5",        # medium quality, small file
                    "-o", str(output.with_suffix("")),  # yt-dlp adds extension
                    "--no-playlist",               # single video only
                    url,
                ],
                capture_output=True, text=True, timeout=600,
            )
            # yt-dlp may name it slightly differently
            actual = output if output.exists() else output.with_suffix(".mp3")
            if not actual.exists():
                matches = list(UPLOADS_DIR.glob(f"{job_id}*"))
                actual  = matches[0] if matches else output

            print(f"[MAROS] YouTube download done → {actual}")
            chipper.run_pipeline(actual, job_id)

            # Delete the audio after processing — we don't need to store it
            try:
                actual.unlink()
                print(f"[MAROS] Cleaned up audio file")
            except Exception:
                pass

        except Exception as e:
            print(f"[MAROS] YouTube ingest failed: {e}")
            jobs.fail_job(job_id, str(e))

    background_tasks.add_task(_download_and_process, req.url, save_path, job.job_id)
    print(f"[MAROS] YouTube job {job.job_id} queued — {req.url} (video_id: {video_id})")
    return job


@app.get("/jobs/{job_id}/youtube")
async def get_youtube_info(job_id: str):
    """Returns YouTube source info if this job came from a YouTube link."""
    yt_path = OUTPUTS_DIR / job_id / "youtube.json"
    if not yt_path.exists():
        return {"source": "upload"}
    return json.loads(yt_path.read_text())


# ─────────────────────────────────────────────
# STUDENT FILE UPLOADS — Oak reads PDFs/images in chat
# ─────────────────────────────────────────────

STUDENT_UPLOADS: dict = {}   # session-scoped: {student_key: [{filename, text}, ...]}

@app.post("/chat/upload")
async def upload_chat_file(
    file: UploadFile = File(...),
    request: Request = None,
):
    """Student drops a PDF/image into Oak chat. Extract text, hold in memory for context."""
    user_id = await get_current_user(request) if request else None
    key     = user_id or "anonymous"

    extracted = ""
    fname     = file.filename or "file"

    try:
        content = await file.read()

        if fname.lower().endswith(".pdf"):
            import fitz  # PyMuPDF — already used by RAG extractor
            doc = fitz.open(stream=content, filetype="pdf")
            pages = []
            for page in doc:
                pages.append(page.get_text("text"))
            doc.close()
            extracted = "\n".join(pages)[:15000]   # cap context size

        elif fname.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            # Vision via Groq's llama vision model
            import base64
            b64 = base64.b64encode(content).decode()
            mime = "image/png" if fname.lower().endswith(".png") else "image/jpeg"
            res = requests.post(
                f"{GROQ_BASE_URL}/chat/completions",
                headers=GROQ_HEADERS,
                json={
                    "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Transcribe and describe everything in this image — text, diagrams, equations. Be thorough but concise."},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        ],
                    }],
                    "max_tokens": 1000,
                },
                timeout=60,
            )
            res.raise_for_status()
            extracted = res.json()["choices"][0]["message"]["content"]

        elif fname.lower().endswith((".txt", ".md")):
            extracted = content.decode(errors="ignore")[:15000]

        else:
            raise HTTPException(status_code=400, detail="Supported: PDF, PNG, JPG, TXT")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")

    # Store per-student (keep last 3 files max)
    if key not in STUDENT_UPLOADS:
        STUDENT_UPLOADS[key] = []
    STUDENT_UPLOADS[key].append({"filename": fname, "text": extracted})
    STUDENT_UPLOADS[key] = STUDENT_UPLOADS[key][-3:]

    log_interaction(
        student_id = user_id,
        event_type = "file_upload",
        payload    = {"filename": fname, "chars_extracted": len(extracted)},
    )

    return {"filename": fname, "chars": len(extracted), "message": "Oak can now reference this file."}


# ─────────────────────────────────────────────
# OAK CHAT PERSISTENCE — threads survive logout (v3)
# ─────────────────────────────────────────────
# Supabase table (run once in SQL Editor):
#
#   create table if not exists oak_chats (
#     owner_key  text not null,
#     mode       text not null,
#     messages   jsonb default '[]'::jsonb,
#     updated_at timestamptz default now(),
#     primary key (owner_key, mode)
#   );

def _save_chat_turn(owner_key: str, mode: str, user_msg: str, oak_msg: str):
    """Append a user+assistant turn to the persistent thread. Non-fatal on failure."""
    if not owner_key:
        return
    from supabase_layer import get_sb
    sb = get_sb()
    if not sb:
        return
    try:
        row = (
            sb.table("oak_chats").select("messages")
            .eq("owner_key", owner_key).eq("mode", mode)
            .execute()
        ).data
        msgs = (row[0]["messages"] if row else []) or []
        msgs += [
            {"role": "user",      "content": user_msg},
            {"role": "assistant", "content": oak_msg},
        ]
        msgs = msgs[-60:]   # keep last 30 turns per thread
        sb.table("oak_chats").upsert(
            {
                "owner_key":  owner_key,
                "mode":       mode,
                "messages":   msgs,
                "updated_at": datetime.utcnow().isoformat(),
            },
            on_conflict="owner_key,mode",
        ).execute()
    except Exception as e:
        print(f"[MAROS] Chat save failed (non-fatal): {e}")


@app.get("/chat/history")
async def get_chat_history(request: Request, scope: str = Query("student")):
    """Return all saved Oak threads for this user (or the professor)."""
    from supabase_layer import get_sb
    if scope == "professor":
        owner_key = "professor"
    else:
        owner_key = await get_current_user(request)
    if not owner_key:
        return {"threads": {}}
    sb = get_sb()
    if not sb:
        return {"threads": {}}
    try:
        rows = (
            sb.table("oak_chats").select("mode, messages")
            .eq("owner_key", owner_key)
            .execute()
        ).data or []
        return {"threads": {r["mode"]: r["messages"] for r in rows}}
    except Exception as e:
        print(f"[MAROS] Chat history fetch failed: {e}")
        return {"threads": {}}


# ─────────────────────────────────────────────
# STUDENT MASTERY & ROADMAP — v2 endpoints
# ─────────────────────────────────────────────

def _fetch_all(sb, table: str, order_col: str = None, desc: bool = False) -> list:
    """Fetch ALL rows from a table, paging past PostgREST's 1000-row cap."""
    rows, page, size = [], 0, 1000
    while True:
        q = sb.table(table).select("*")
        if order_col:
            q = q.order(order_col, desc=desc)
        batch = q.range(page * size, (page + 1) * size - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < size:
            return rows
        page += 1


@app.get("/professor/analytics")
async def professor_analytics():
    """
    Class-wide analytics for the professor dashboard.
    Returns: student count, quiz stats, weak concepts, misconceptions,
    top/bottom performers, recent activity.
    """
    from supabase_layer import get_sb
    sb = get_sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        # All quiz answers
        answers = _fetch_all(sb, "quiz_answers")

        # All interaction events
        events = sb.table("interaction_log").select("*").order("ts", desc=True).limit(500).execute().data or []

        # Student profiles
        profiles = _fetch_all(sb, "student_profiles")

        # ── Aggregate stats ──────────────────────────────────────────
        total_answers   = len(answers)
        total_correct   = sum(1 for a in answers if a.get("is_correct"))
        total_students  = len(set(a["student_id"] for a in answers if a.get("student_id"))) or len(profiles)

        # ── Weak concepts: wrong answer rate per concept ─────────────
        concept_stats = {}
        for a in answers:
            c = a.get("root_concept_id") or a.get("concept_id") or "unknown"
            if c not in concept_stats:
                concept_stats[c] = {"total": 0, "wrong": 0}
            concept_stats[c]["total"] += 1
            if not a.get("is_correct"):
                concept_stats[c]["wrong"] += 1

        weak_concepts = [
            {
                "concept": c,
                "total_attempts": s["total"],
                "wrong": s["wrong"],
                "error_rate": round(s["wrong"] / s["total"], 2) if s["total"] else 0,
            }
            for c, s in concept_stats.items()
        ]
        weak_concepts.sort(key=lambda x: (-x["error_rate"], -x["total_attempts"]))

        # ── Common misconceptions ────────────────────────────────────
        misconceptions = [
            {
                "concept": a.get("root_concept_id") or a.get("concept_id"),
                "misconception": a.get("misconception"),
                "question": (a.get("question_text") or "")[:120],
                "at": a.get("answered_at"),
            }
            for a in answers
            if a.get("misconception")
        ][:20]

        # ── Per-student summary ──────────────────────────────────────
        student_stats = {}
        for a in answers:
            sid = a.get("student_id")
            if not sid:
                continue
            if sid not in student_stats:
                student_stats[sid] = {"total": 0, "correct": 0}
            student_stats[sid]["total"] += 1
            if a.get("is_correct"):
                student_stats[sid]["correct"] += 1

        # Map profiles for names
        profile_map = {p["id"]: p for p in profiles}
        students = [
            {
                "student_id": sid,
                "name": profile_map.get(sid, {}).get("name", "Unknown"),
                "roll_no": profile_map.get(sid, {}).get("roll_no", ""),
                "total_answers": s["total"],
                "correct": s["correct"],
                "accuracy": round(s["correct"] / s["total"], 2) if s["total"] else 0,
            }
            for sid, s in student_stats.items()
        ]
        students.sort(key=lambda x: x["accuracy"])

        # ── Top / bottom performers (min 5 answers to qualify) ────────
        qualified         = [s for s in students if s["total_answers"] >= 5]
        top_performers    = sorted(qualified, key=lambda x: -x["accuracy"])[:5]
        bottom_performers = [s for s in qualified if s["accuracy"] < 0.5][:5]

        # ── Recent activity ──────────────────────────────────────────
        recent = [
            {
                "event": e["event_type"],
                "at": e["ts"],
                "student": profile_map.get(e.get("student_id"), {}).get("name", "Anonymous"),
            }
            for e in events[:30]
        ]

        return {
            "total_students": total_students,
            "total_answers": total_answers,
            "class_accuracy": round(total_correct / total_answers, 2) if total_answers else 0,
            "weak_concepts": weak_concepts[:10],
            "misconceptions": misconceptions,
            "students": students,
            "top_performers": top_performers,
            "bottom_performers": bottom_performers,
            "recent_activity": recent,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analytics failed: {e}")


@app.post("/professor/report")
async def generate_class_report():
    """LLM-written class report: who's weak on what, and what to do about it."""
    analytics = await professor_analytics()

    if analytics["total_answers"] == 0:
        return {"report": "No quiz data yet. Reports become available once students start taking quizzes."}

    # Build a compact data summary for the LLM
    weak_summary = "\n".join(
        f"- {c['concept']}: {round(c['error_rate']*100)}% wrong ({c['wrong']}/{c['total_attempts']} attempts)"
        for c in analytics["weak_concepts"][:8]
    )
    misc_summary = "\n".join(
        f"- [{m['concept']}] {m['misconception']}"
        for m in analytics["misconceptions"][:10] if m.get("misconception")
    )
    student_summary = "\n".join(
        f"- {s['name']}: {round(s['accuracy']*100)}% accuracy ({s['correct']}/{s['total_answers']})"
        for s in analytics["students"][:15]
    )
    top_summary = "\n".join(
        f"- {s['name']} ({s.get('roll_no','')}): {round(s['accuracy']*100)}%"
        for s in analytics.get("top_performers", [])
    )
    bottom_summary = "\n".join(
        f"- {s['name']} ({s.get('roll_no','')}): {round(s['accuracy']*100)}%"
        for s in analytics.get("bottom_performers", [])
    )

    prompt = f"""You are an educational analyst writing a brief report for a CS professor at VNIT Nagpur
about their Operating Systems class performance on MAROS.

DATA:
Class size: {analytics['total_students']} active students
Total quiz answers: {analytics['total_answers']}
Class accuracy: {round(analytics['class_accuracy']*100)}%

CONCEPTS BY ERROR RATE:
{weak_summary}

DETECTED MISCONCEPTIONS:
{misc_summary or 'None recorded'}

STUDENT PERFORMANCE (weakest first):
{student_summary}

TOP PERFORMERS:
{top_summary or 'None qualified yet'}

STUDENTS NEEDING ATTENTION (below 50%):
{bottom_summary or 'None'}

Write a concise report (150-250 words) with:
1. One-line class health summary
2. The 2-3 concepts needing attention, with the SPECIFIC misconceptions students hold
3. Name the specific top performers and the specific students below 50% who need individual attention — use their names and roll numbers
4. One concrete teaching recommendation for the next lecture

Write in plain professional prose. No markdown headers, no bullet spam — short paragraphs."""

    try:
        report = _call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=500,
        )
        log_interaction(
            student_id=None,
            event_type="class_report",
            payload={"report_length": len(report)},
        )
        return {"report": report, "generated_at": datetime.utcnow().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Report generation failed: {e}")


@app.get("/student/classwork")
async def get_classwork(request: Request):
    """All quiz attempts for the logged-in student, grouped by quiz session."""
    from supabase_layer import get_sb
    user_id = await get_current_user(request)
    if not user_id:
        return {"quizzes": [], "message": "Login to see your classwork"}

    sb = get_sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        answers = (
            sb.table("quiz_answers")
            .select("*")
            .eq("student_id", user_id)
            .order("answered_at", desc=True)
            .limit(500)
            .execute()
        ).data or []

        # Group into quiz sessions: same module_id + answers within 10 min = one quiz
        from collections import defaultdict
        sessions = defaultdict(list)
        for a in answers:
            # Bucket key: module + timestamp rounded to 10-minute window
            ts_bucket = (a.get("answered_at") or "")[:15]   # YYYY-MM-DDTHH:M → ~10min granularity
            sessions[(a.get("module_id"), ts_bucket)].append(a)

        quizzes = []
        for (module_id, _), ans_list in sessions.items():
            total   = len(ans_list)
            correct = sum(1 for a in ans_list if a.get("is_correct"))
            quizzes.append({
                "module_id": module_id,
                "taken_at": ans_list[0].get("answered_at"),
                "total": total,
                "correct": correct,
                "score_pct": round(correct / total * 100) if total else 0,
                "questions": [
                    {
                        "question": a.get("question_text"),
                        "chosen": a.get("chosen_answer"),
                        "correct_answer": a.get("correct_answer"),
                        "is_correct": a.get("is_correct"),
                        "misconception": a.get("misconception"),
                    }
                    for a in ans_list
                ],
            })

        quizzes.sort(key=lambda q: q["taken_at"] or "", reverse=True)
        return {"quizzes": quizzes}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Classwork fetch failed: {e}")


@app.get("/student/mastery")
async def get_mastery(
    request: Request,
    course_id: str = Query(None),
):
    """Get current student's mastery scores."""
    user_id = await get_current_user(request)
    if not user_id:
        return {"mastery": [], "message": "Login to track mastery"}

    weak = get_student_mastery(user_id, course_id, limit=20)
    return {"student_id": user_id, "mastery": weak}


@app.get("/student/next")
async def get_next_concepts(
    request: Request,
    course_id: str = Query(None),
    skill_id:  str = Query(None),
):
    """What should this student learn next?"""
    user_id = await get_current_user(request)
    if not user_id:
        return {"concepts": [], "message": "Login to get recommendations"}

    concepts = get_ready_to_learn(user_id, course_id, skill_id)

    log_interaction(
        student_id = user_id,
        event_type = "path_suggest",
        course_id  = course_id,
        skill_id   = skill_id,
        payload    = {"suggested": concepts},
    )

    return {"student_id": user_id, "next_concepts": concepts}


# ─────────────────────────────────────────────
# RESEARCH PAPERS
# ─────────────────────────────────────────────

def _paper_dir(paper_id: str) -> Path:
    return PAPERS_DIR / paper_id


def _load_paper_meta(paper_id: str) -> dict:
    meta_path = _paper_dir(paper_id) / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found.")
    return json.loads(meta_path.read_text())


def _save_paper_meta(paper_id: str, meta: dict):
    (_paper_dir(paper_id) / "meta.json").write_text(json.dumps(meta, indent=2))


@app.post("/papers")
async def assign_paper(request: Request, file: UploadFile = File(...)):
    # v4: if an authed student uploads (podcast converter), the paper is
    # PRIVATE to them. Prof uploads (no Supabase auth) have owner_id=None
    # and are visible to everyone. This removes student-generated podcasts
    # from the professor's Assigned Papers list.
    owner_id = await get_current_user(request)
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Upload a PDF.")

    paper_id  = str(uuid.uuid4())[:8]
    paper_dir = _paper_dir(paper_id)
    paper_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = paper_dir / "paper.pdf"
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        title, abstract = extract_from_pdf(str(pdf_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to extract PDF: {e}")

    meta = {
        "paper_id"   : paper_id,
        "title"      : title,
        "abstract"   : abstract,
        "domain"     : "general",
        "assigned_at": datetime.utcnow().isoformat(),
        "has_podcast": False,
        "visible"    : True,
        "owner_id"   : owner_id,   # v4: None = professor/global, uuid = that student only
    }
    _save_paper_meta(paper_id, meta)
    print(f"[MAROS] Paper {paper_id} assigned — title: {title[:80]!r}")
    return meta


@app.get("/papers")
async def list_papers(request: Request, visible_only: bool = Query(True)):
    """v4: prof (unauthed) sees only global papers; a student sees global
    papers PLUS their own uploads. Other students' uploads are never shown."""
    user_id = await get_current_user(request)
    if not PAPERS_DIR.exists():
        return []
    out = []
    for d in PAPERS_DIR.iterdir():
        if d.is_dir() and (d / "meta.json").exists():
            m = json.loads((d / "meta.json").read_text())
            owner = m.get("owner_id")
            if owner and owner != user_id:
                continue          # someone else's private paper
            if visible_only and not m.get("visible", True):
                continue
            m["mine"] = bool(owner) and owner == user_id
            out.append(m)
    out.sort(key=lambda m: m.get("assigned_at", ""), reverse=True)
    return out


@app.get("/papers/{paper_id}")
def get_paper(paper_id: str):
    return _load_paper_meta(paper_id)


@app.patch("/papers/{paper_id}/visibility")
def toggle_paper_visibility(paper_id: str, visible: bool = Query(...)):
    meta = _load_paper_meta(paper_id)
    meta["visible"] = visible
    _save_paper_meta(paper_id, meta)
    print(f"[MAROS] Paper {paper_id} visibility → {visible}")
    return meta


@app.delete("/papers/{paper_id}")
async def delete_paper(paper_id: str, request: Request):
    """v4: students can delete their OWN uploads; only the prof (unauthed
    prof console) can delete global assigned papers."""
    user_id = await get_current_user(request)
    meta    = _load_paper_meta(paper_id)
    owner   = meta.get("owner_id")
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Not your paper.")
    if not owner and user_id:
        raise HTTPException(status_code=403, detail="Only the professor can remove assigned papers.")
    shutil.rmtree(_paper_dir(paper_id))
    print(f"[MAROS] Paper {paper_id} deleted")
    return {"deleted": paper_id}


# ─────────────────────────────────────────────
# PODCAST
# ─────────────────────────────────────────────

@app.post("/papers/{paper_id}/podcast")
async def generate_paper_podcast(paper_id: str, background_tasks: BackgroundTasks):
    meta = _load_paper_meta(paper_id)

    podcast_path = _paper_dir(paper_id) / "podcast.json"
    if podcast_path.exists():
        return json.loads(podcast_path.read_text())

    meta["podcast_status"] = "generating"
    _save_paper_meta(paper_id, meta)
    background_tasks.add_task(_run_podcast_job, paper_id, meta)
    return {"paper_id": paper_id, "podcast_status": "generating"}


async def _run_podcast_job(paper_id: str, meta: dict):
    try:
        result = await generate_podcast(
            paper_title = meta["title"],
            abstract    = meta["abstract"],
            domain      = meta.get("domain", "general"),
            job_id      = paper_id,
        )

        podcast_data = {
            "paper_id"    : paper_id,
            "turns"       : result["turns"],
            "turn_count"  : result["turn_count"],
            "audio_path"  : result["audio_path"],
            "generated_at": datetime.utcnow().isoformat(),
        }

        (_paper_dir(paper_id) / "podcast.json").write_text(json.dumps(podcast_data, indent=2))
        meta["has_podcast"]    = True
        meta["podcast_status"] = "done"
        _save_paper_meta(paper_id, meta)
        print(f"[MAROS] Podcast for paper {paper_id} done — {result['turn_count']} turns")

    except Exception as e:
        meta["podcast_status"] = "failed"
        meta["podcast_error"]  = str(e)
        _save_paper_meta(paper_id, meta)
        print(f"[MAROS] Podcast generation FAILED for {paper_id}: {e}")


@app.get("/papers/{paper_id}/podcast")
def get_paper_podcast(paper_id: str):
    meta         = _load_paper_meta(paper_id)
    podcast_path = _paper_dir(paper_id) / "podcast.json"
    if podcast_path.exists():
        return json.loads(podcast_path.read_text())
    return {
        "paper_id"      : paper_id,
        "podcast_status": meta.get("podcast_status", "not_started"),
        "podcast_error" : meta.get("podcast_error"),
    }


@app.get("/papers/{paper_id}/podcast/audio")
def get_paper_podcast_audio(paper_id: str):
    audio_path = Path("output") / f"{paper_id}_podcast.mp3"
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Podcast audio not found.")
    return FileResponse(
        path       = str(audio_path),
        media_type = "audio/mpeg",
        filename   = f"podcast_{paper_id}.mp3"
    )


# ─────────────────────────────────────────────
# ASSIGNMENTS
# ─────────────────────────────────────────────

def _assignment_dir(aid: str) -> Path:
    return ASSIGNMENTS_DIR / aid


def _load_assignment_meta(aid: str) -> dict:
    meta_path = _assignment_dir(aid) / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Assignment {aid} not found.")
    return json.loads(meta_path.read_text())


def _save_assignment_meta(aid: str, meta: dict):
    (_assignment_dir(aid) / "meta.json").write_text(json.dumps(meta, indent=2))


@app.post("/assignments")
async def create_assignment(
    file       : UploadFile = File(...),
    title      : str = Form(""),
    description: str = Form(""),
    due_date   : str = Form("")
):
    aid  = str(uuid.uuid4())[:8]
    adir = _assignment_dir(aid)
    adir.mkdir(parents=True, exist_ok=True)

    file_path = adir / file.filename
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    meta = {
        "assignment_id": aid,
        "title"        : title or file.filename,
        "description"  : description,
        "due_date"     : due_date,
        "filename"     : file.filename,
        "visible"      : True,
        "created_at"   : datetime.utcnow().isoformat(),
        "submissions"  : []
    }
    _save_assignment_meta(aid, meta)
    print(f"[MAROS] Assignment {aid} posted — {meta['title']!r}")
    return meta


@app.get("/assignments")
def list_assignments(visible_only: bool = Query(True)):
    if not ASSIGNMENTS_DIR.exists():
        return []
    out = []
    for d in ASSIGNMENTS_DIR.iterdir():
        if d.is_dir() and (d / "meta.json").exists():
            m = json.loads((d / "meta.json").read_text())
            if visible_only and not m.get("visible", True):
                continue
            out.append(m)
    out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return out


@app.patch("/assignments/{aid}/visibility")
def toggle_assignment_visibility(aid: str, visible: bool = Query(...)):
    meta = _load_assignment_meta(aid)
    meta["visible"] = visible
    _save_assignment_meta(aid, meta)
    print(f"[MAROS] Assignment {aid} visibility → {visible}")
    return meta


@app.get("/assignments/{aid}/file")
def get_assignment_file(aid: str):
    meta      = _load_assignment_meta(aid)
    file_path = _assignment_dir(aid) / meta["filename"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Assignment file not found.")
    return FileResponse(path=str(file_path), filename=meta["filename"])


@app.post("/assignments/{aid}/submit")
async def submit_assignment(
    aid         : str,
    file        : UploadFile = File(...),
    student_name: str = Form("Student"),
    student_roll: str = Form("")
):
    meta    = _load_assignment_meta(aid)
    sub_dir = _assignment_dir(aid) / "submissions"
    sub_dir.mkdir(exist_ok=True)

    sub_id    = str(uuid.uuid4())[:8]
    save_name = f"{sub_id}_{file.filename}"
    with open(sub_dir / save_name, "wb") as f:
        shutil.copyfileobj(file.file, f)

    submission = {
        "submission_id": sub_id,
        "student_name" : student_name,
        "student_roll" : student_roll,
        "filename"     : file.filename,
        "saved_as"     : save_name,
        "submitted_at" : datetime.utcnow().isoformat()
    }
    meta.setdefault("submissions", []).append(submission)
    _save_assignment_meta(aid, meta)
    print(f"[MAROS] Submission {sub_id} received for assignment {aid} from {student_name!r}")
    return submission


@app.get("/assignments/{aid}/submissions")
def get_submissions(aid: str):
    return _load_assignment_meta(aid).get("submissions", [])


@app.get("/assignments/{aid}/submissions/{filename}")
def download_submission(aid: str, filename: str):
    file_path = _assignment_dir(aid) / "submissions" / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Submission not found.")
    return FileResponse(path=str(file_path), filename=filename)


# ─────────────────────────────────────────────
# CHAT — PROF OAK
# ─────────────────────────────────────────────



# ─────────────────────────────────────────────
# OAK PERSONALITY PROMPTS — never exposed to client
# ─────────────────────────────────────────────

OAK_PERSONAS = {
    "videos": {
        "system": """You are Prof Oak — a warm, sharp teaching assistant at VNIT Nagpur.

RULES:
- For casual messages (thanks, hi, ok, bye): reply with ONE short line.
- For quick factual questions: answer in 2-4 sentences, direct and clear.
- If a question is narrowly outside the current module, gently redirect. But if the student asks about the broader course, exams, or multiple topics, answer broadly.
- If web search results are provided, use them only to enrich academic answers.
- If the student uploaded a file, reference its content directly.
- NEVER say "feel free to ask questions", "let me know if you need help", or similar passive filler.

TEACHING MODE — when the student asks you to explain/teach a concept in depth:
Use this structure, with a BLANK LINE between each section:

**What it is:** one clear sentence definition

**How it works:** 2-3 sentences with a simple analogy

**Example:** one concrete, relatable example

*Quick Check:* end with ONE specific question to verify they understood

IMPORTANT: If the student ALSO asks something else (like exam importance, past questions, or a follow-up), answer that too — add a brief **Exam Relevance:** line before the Quick Check. Always address everything the student asked, not just the "teach me" part.

When the student answers your Quick Check: evaluate it. If correct, say so briefly and offer the next related idea. If wrong, correct gently in 1-2 sentences and re-ask differently. Always drive the lesson forward.""",

        "refine": """You are Prof Oak — a warm teaching assistant at VNIT Nagpur.
Another system retrieved accurate information. Rewrite it as a short,
natural explanation a tutor would give sitting next to the student.

RETRIEVED INFORMATION:
{rag_answer}

RULES:
- Keep every fact — change nothing factual
- MAX 3-4 sentences. No essays.
- Talk naturally, no bullet lists
- Do NOT mention "retrieved information" — speak as your own knowledge
- Do NOT invent anything beyond what's given above"""
    },

    "papers": {
        "system": """You are Prof Oak in Research Execution Mode.
You are not here to explain or summarize. You are here to push the student
to IMPLEMENT the paper they're reading.

Your personality: direct, focused, zero tolerance for passive reading.
Every answer you give should end with a concrete next action:
what to code, what to test, what section to re-read with a specific question in mind.

You ARE allowed to brainstorm architecture and implementation approaches with the student —
that is execution-focused thinking. Engage with their ideas, push them further, and always
land on a concrete next step.

Rules:
- Brainstorm freely but always end with one concrete action item
- If they ask "what does X mean?" → ask "how would you implement X?"
- If they're stuck → break it into the smallest possible concrete step
- If they haven't started implementing → call it out directly
- When you write code: before returning it, mentally verify every variable is declared in the scope where it's used (especially variables declared inside loops or blocks) and that every function you call exists in the snippet. Only ship code that runs.
- Keep responses under 4 sentences + 1 action item""",

        "refine": """You are Prof Oak in Research Execution Mode — direct, implementation-obsessed.
Another system retrieved grounded context. Use it to push the student toward execution.

RETRIEVED INFORMATION:
{rag_answer}

Rewrite as a direct, no-fluff push toward implementation:
- Translate theory into "now go build this" language
- End with exactly one concrete action item (what to implement, test, or measure)
- Do NOT mention the retrieval system
- Max 3 sentences + the action item"""
    },

    "assignments": {
        "system": """You are Prof Oak in Coaching Mode — a Socratic tutor.
You NEVER give answers directly. You guide students to find the answer themselves.

Your method:
- When asked a question → respond with a clarifying question back
- When they're stuck → ask what they've already tried
- When they have a partial answer → ask them to extend it
- When they're about to give up → give the smallest possible hint, then ask again
- Celebrate when they figure it out — make it feel earned
- If you ever include code (rare — only tiny illustrative snippets), it must be scope-correct and runnable as written.

Tone: patient, warm, slightly challenging. Like a coach who believes in them.
Max 2-3 sentences per response. Always end with a question.""",

        "refine": """You are Prof Oak in Coaching Mode — Socratic, never giving answers directly.
Use this retrieved context only to inform your guiding questions — never to answer for them.

RETRIEVED INFORMATION:
{rag_answer}

Respond as a Socratic coach:
- Ask a question that leads the student toward the answer in the context
- Do NOT reveal the answer
- Do NOT mention the retrieval system
- End with exactly one question back to the student
- Max 2-3 sentences"""
    }
}

PODCAST_CONTEXT_BLOCK = """
A podcast episode has already been made for this paper, hosted by
Bir (curious learner) and Mia (expert). Here is the full script —
you can naturally reference what was discussed:

{script_text}
"""


CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_MODEL   = os.getenv("CEREBRAS_MODEL", "llama-3.3-70b")

def _call_llm(messages: list, temperature: float = 0.5, max_tokens: int = 400) -> str:
    """Cerebras primary, Groq fallback. Same OpenAI-compatible format."""
    # Try Cerebras first (1M tokens/day free, fast)
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
                timeout=30,
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[MAROS] Cerebras failed ({e}), falling back to Groq")

    # Groq fallback
    res = requests.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers=GROQ_HEADERS,
        json={
            "model"      : GROQ_CHAT_MODEL,
            "messages"   : messages,
            "temperature": temperature,
            "max_tokens" : max_tokens
        },
        timeout=30
    )
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]


def _load_podcast_context(paper_id: str) -> str:
    """Returns a flattened, truncated podcast script for chat context."""
    podcast_path = PAPERS_DIR / paper_id / "podcast.json"
    if not podcast_path.exists():
        return ""
    try:
        data        = json.loads(podcast_path.read_text())
        turns       = data.get("turns", [])
        lines       = [f"{t['speaker']}: {t['text']}" for t in turns]
        script_text = "\n".join(lines)
        if len(script_text) > 6000:
            script_text = script_text[:6000] + "\n...[truncated]"
        return PODCAST_CONTEXT_BLOCK.format(script_text=script_text)
    except Exception as e:
        print(f"[MAROS] Failed to load podcast context for {paper_id}: {e}")
        return ""

# ─────────────────────────────────────────────
# CODE VERIFICATION (v3) — check code in Oak replies before students see it
# ─────────────────────────────────────────────
import re as _re2
import io as _io

CODE_BLOCK_RE = _re2.compile(r"```(\w+)?\n(.*?)```", _re2.DOTALL)

def _check_python_code(code: str) -> list:
    """Deterministic checks for Python blocks: syntax + undefined names."""
    issues = []
    try:
        import ast
        ast.parse(code)
    except SyntaxError as e:
        issues.append(f"Python SyntaxError: {e}")
        return issues
    try:
        from pyflakes.api import check as _pf_check
        from pyflakes.reporter import Reporter as _pf_Reporter
        out, err = _io.StringIO(), _io.StringIO()
        _pf_check(code, "oak_code", _pf_Reporter(out, err))
        for line in (out.getvalue() + err.getvalue()).splitlines():
            if "undefined name" in line or "referenced before assignment" in line:
                issues.append(line.strip())
    except ImportError:
        pass  # pyflakes not installed — syntax check alone still ran
    return issues


def _verify_code_reply(reply: str) -> str:
    """
    If Oak's reply contains code blocks, verify them before returning to student.
    - Python: deterministic (ast + pyflakes)
    - JS/other: one cheap LLM self-review pass at temp 0
    One repair attempt max. Non-fatal on any failure — worst case returns original.
    """
    blocks = CODE_BLOCK_RE.findall(reply)
    if not blocks:
        return reply

    issues = []
    needs_llm_review = False
    for lang, code in blocks:
        lang = (lang or "").lower()
        if lang in ("python", "py"):
            issues += _check_python_code(code)
        else:  # js, jsx, ts, or unlabeled — no good pure-python linter, use LLM pass
            needs_llm_review = True

    if not issues and needs_llm_review:
        try:
            verdict = _call_llm(
                messages=[{
                    "role": "user",
                    "content": f"""Review the code in this reply ONLY for bugs that would throw at runtime:
- variables declared inside a loop/block (const/let) but referenced outside that block
- variables referenced before declaration
- calls to functions that don't exist anywhere in the snippet
- unclosed braces/brackets

If the code is fine, reply with exactly: OK
If there are bugs, list each on one line as: PROBLEM: <one-line description>

{reply[:6000]}"""
                }],
                temperature=0.0,
                max_tokens=250,
            ).strip()
            if not verdict.startswith("OK"):
                issues += [l.strip() for l in verdict.splitlines() if l.strip()][:5]
        except Exception as e:
            print(f"[MAROS] Code self-review skipped (non-fatal): {e}")

    if not issues:
        return reply

    print(f"[MAROS] Code issues in Oak reply → repairing: {issues}")
    try:
        fixed = _call_llm(
            messages=[{
                "role": "user",
                "content": f"""This reply contains code with the following bugs:
{chr(10).join('- ' + i for i in issues)}

Rewrite the reply with the code FIXED. Keep the tone, explanation, and structure identical — change only what's needed to fix the bugs. Output the full corrected reply and nothing else.

{reply}"""
            }],
            temperature=0.1,
            max_tokens=1200,
        )
        return fixed if fixed.strip() else reply
    except Exception as e:
        print(f"[MAROS] Code repair failed (non-fatal): {e}")
        return reply

def _plain_oak_reply(req: ChatRequest, module_context: str) -> str:
    """Oak grounded in module notes / paper abstract — no RAG."""
    if req.role == "professor":
        system_prompt = """You are Prof Oak — a sharp teaching analytics assistant at VNIT Nagpur, talking to the PROFESSOR.
- Answer questions about class performance using the CLASS ANALYTICS data in your context.
- If asked for a report, write a structured 150-250 word report: class health, weak concepts with specific misconceptions, students needing attention, one teaching recommendation.
- For simple questions, answer in 2-4 sentences.
- Never invent data — only use what's in your context. If there's no data, say so."""
        max_tok = 600
    else:
        persona       = OAK_PERSONAS.get(req.mode, OAK_PERSONAS["videos"])
        system_prompt = persona["system"]
        max_tok       = 800

    if module_context:
        system_prompt += f"\n\nCONTEXT:\n{module_context}"

    messages = [{"role": "system", "content": system_prompt}]
    for msg in req.history[-6:]:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.message})

    return _call_llm(messages, temperature=0.5, max_tokens=max_tok)


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """
    Prof Oak chat.
    - Videos tab  → RAG-grounded (OS exam papers)
    - Papers tab  → direct Oak with paper context (no RAG)
    - Assignments → direct Oak in Socratic mode (no RAG)
    """
    start   = time.time()
    user_id = await get_current_user(request)

    module_context = ""
    module_concept = None

    # ── Struggle cue detection (v2, from GraphMASAL Router pattern) ────────
    STRUGGLE_CUES = (
        "wrong", "mistake", "confused", "confusing", "failed",
        "don't understand", "dont understand", "stuck", "misunderstood",
        "i don't get", "i dont get", "makes no sense", "lost",
        "help me learn", "struggling",
    )
    user_text_lower = req.message.strip().lower()
    is_struggling = any(cue in user_text_lower for cue in STRUGGLE_CUES)

    if is_struggling and user_id:
        # Log the struggle event; decay mastery slightly on current concept
        struggle_concept = None
        if req.job_id and req.module_id:
            _mp = OUTPUTS_DIR / req.job_id / "manifest.json"
            if _mp.exists():
                _data = json.loads(_mp.read_text())
                _mod  = next((m for m in _data["modules"] if m["module_id"] == req.module_id), None)
                if _mod:
                    struggle_concept = _mod["concept"]
        if struggle_concept:
            update_mastery(user_id, struggle_concept, -0.05)
        log_interaction(
            student_id = user_id,
            event_type = "struggle_signal",
            concept_id = struggle_concept,
            payload    = {"message": req.message[:200]},
        )
        print(f"[MAROS] Struggle cue detected → logged{' + mastery -0.05 on ' + struggle_concept if struggle_concept else ''}")

    # ── Student mastery context (v2) ───────────────────────────────────────
    mastery_ctx = build_mastery_context(user_id) if user_id else ""
    if mastery_ctx:
        module_context += mastery_ctx

    # ── Student uploaded files (v2) ─────────────────────────────────────────
    upload_key = user_id or "anonymous"
    if upload_key in STUDENT_UPLOADS and STUDENT_UPLOADS[upload_key]:
        for f in STUDENT_UPLOADS[upload_key]:
            module_context += f"\n\nSTUDENT'S UPLOADED FILE ({f['filename']}):\n{f['text'][:3000]}\n"

    # (context cap applied below, after all sources are assembled)

    # ── Web search (v2) — skip for short/casual messages ──
    web_ctx = ""
    if len(req.message.split()) >= 4:
        try:
            web_ctx = web_search(req.message, max_results=2)
            if web_ctx:
                module_context += web_ctx
        except Exception as e:
            print(f"[MAROS] Web search skipped: {e}")


    # ── Professor analytics context (v2) ────────────────────────────────────
    if req.role == "professor":
        try:
            analytics = await professor_analytics()
            if analytics["total_answers"] > 0:
                weak_lines = "\n".join(
                    f"- {c['concept']}: {round(c['error_rate']*100)}% wrong ({c['total_attempts']} attempts)"
                    for c in analytics["weak_concepts"][:6]
                )
                stu_lines = "\n".join(
                    f"- {s['name']}: {round(s['accuracy']*100)}% accuracy"
                    for s in analytics["students"][:10]
                )
                misc_lines = "\n".join(
                    f"- {m['misconception']}"
                    for m in analytics["misconceptions"][:6] if m.get("misconception")
                )
                top_lines = "\n".join(
                    f"- {s['name']} ({s.get('roll_no','')}): {round(s['accuracy']*100)}%"
                    for s in analytics.get("top_performers", [])
                )
                bot_lines = "\n".join(
                    f"- {s['name']} ({s.get('roll_no','')}): {round(s['accuracy']*100)}%"
                    for s in analytics.get("bottom_performers", [])
                )
                module_context += f"""
CLASS ANALYTICS (you are talking to the professor — use this data to answer questions about student performance, reports, or weak areas):
Students active: {analytics['total_students']} | Quiz answers: {analytics['total_answers']} | Class accuracy: {round(analytics['class_accuracy']*100)}%
Concepts by error rate:
{weak_lines}
Students (weakest first):
{stu_lines}
Top performers:
{top_lines or 'None qualified yet'}
Needs attention (below 50%):
{bot_lines or 'None'}
Detected misconceptions:
{misc_lines or 'None yet'}
"""
        except Exception as e:
            print(f"[MAROS] Analytics context skipped: {e}")

    # ── Module context (videos tab) ─────────────────────────────────────────
    # FIX: += (was =, which wiped mastery/upload/web context built above)
    if req.job_id and req.module_id:
        manifest_path = OUTPUTS_DIR / req.job_id / "manifest.json"
        if manifest_path.exists():
            data   = json.loads(manifest_path.read_text())
            module = next((m for m in data["modules"] if m["module_id"] == req.module_id), None)
            if module:
                module_concept = module["concept"]
                module_context += f"""You are currently helping with the module: "{module['concept']}".

MODULE NOTES:
{module['notes']}
"""

    # ── Paper context (papers tab) ──────────────────────────────────────────
    paper_concept = None
    if req.paper_id:
        try:
            meta          = _load_paper_meta(req.paper_id)
            paper_concept = meta["title"]
            module_context += f"""You are helping a student implement this research paper:
Title: {meta['title']}
Abstract: {meta['abstract']}

The student has read the paper and listened to the podcast.
Engage directly with their ideas, help them plan implementation,
brainstorm architecture, and push them toward building it.
"""
        except Exception as e:
            print(f"[MAROS] Could not load paper meta for chat: {e}")

        module_context += _load_podcast_context(req.paper_id)

    # ── RAG for videos tab — direct ChromaDB, no separate server ─────────
    rag_concept = module_concept or paper_concept

    if rag_concept and req.mode == "videos" and req.role != "professor":
        # Use student's message directly — module context is already in the system prompt
        rag_query = req.message
        print(f"[MAROS] RAG query — '{rag_query[:60]}...'")
        try:
            rag_context = build_rag_context(rag_query, n_results=5)
            if rag_context:
                module_context += f"\n\nEXAM CONTENT (from past papers and notes — use to ground your answer):\n{rag_context[:4000]}\n"
                print(f"[MAROS] RAG returned context ({len(rag_context)} chars)")
            else:
                print("[MAROS] RAG found no relevant chunks — proceeding without")
        except Exception as e:
            print(f"[MAROS] RAG query failed (non-fatal): {e}")

    # ── Safety cap: truncate total context to prevent token overflow ───────
    if len(module_context) > 10000:
        module_context = module_context[:10000] + "\n...(context trimmed for length)"

    # ── Direct Oak reply (papers, assignments, or RAG fallback) ────────────
    try:
        reply = _plain_oak_reply(req, module_context)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Groq API error: {e}")
    # ── Verify any code Oak wrote before the student sees it (v3) ─────────
    if req.mode in ("papers", "assignments"):
        reply = _verify_code_reply(reply)

    # ── Quick Check mastery loop (v2, from GraphMASAL Tutor pattern) ──────
    # If Oak's PREVIOUS message asked a Quick Check and the student just
    # answered, detect whether Oak judged it correct — bump mastery.
    try:
        if user_id and module_concept and req.history:
            last_oak = next(
                (m.content for m in reversed(req.history) if m.role == ChatRole.assistant),
                "",
            )
            if "Quick Check" in last_oak or "*Quick Check:*" in last_oak:
                # Oak's new reply contains its verdict on the student's answer
                verdict_markers = ["correct", "exactly", "that's right", "spot on", "well done", "nailed it"]
                if any(v in reply.lower()[:200] for v in verdict_markers):
                    update_mastery(user_id, module_concept, 0.1)
                    log_interaction(
                        student_id=user_id,
                        event_type="mastery_update",
                        concept_id=module_concept,
                        payload={"source": "oak_quick_check", "delta": 0.1},
                    )
                    print(f"[MAROS] Quick Check passed → mastery +0.1 on {module_concept}")
    except Exception as e:
        print(f"[MAROS] Quick Check loop skipped: {e}")

    # ── Persist this chat turn (v3) — threads survive logout ──────────────
    _owner = "professor" if req.role == "professor" else user_id
    _save_chat_turn(_owner, req.mode or "videos", req.message, reply)

    # ── Log Oak chat interaction ──────────────────────────────────────────
    elapsed_ms = int((time.time() - start) * 1000)
    log_interaction(
        student_id      = user_id,
        event_type      = "oak_response",
        module_id       = f"{req.job_id}_mod{req.module_id:02d}" if req.module_id else None,
        payload         = {"mode": req.mode, "query": req.message, "role": req.role, "source": "direct"},
        response_time_ms = elapsed_ms,
    )

    return ChatMessage(
        role      = ChatRole.assistant,
        content   = reply,
        module_id = req.module_id,
        timestamp = datetime.utcnow()
    )


# ─────────────────────────────────────────────
# STATIC FILES — must be last
# ─────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
app.mount("/app", StaticFiles(directory="frontend", html=True), name="frontend")


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)