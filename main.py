# main.py
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
    """Upload a lecture video. Starts the Chipper pipeline in the background."""

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
    """Poll job status."""
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    return job


@app.get("/jobs/{job_id}/manifest", response_model=Manifest)
def get_manifest(job_id: str):
    """Get the full manifest once a job is done."""
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    if job.status != "done":
        raise HTTPException(status_code=202, detail=f"Job is still {job.status}.")

    manifest_path = OUTPUTS_DIR / job_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest file not found on disk.")

    return Manifest.model_validate_json(manifest_path.read_text())


# ─────────────────────────────────────────────
# MODULES
# ─────────────────────────────────────────────

@app.get("/modules/{job_id}")
def get_modules(job_id: str):
    """Return all modules for a completed job."""
    manifest_path = OUTPUTS_DIR / job_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest not found.")

    data = json.loads(manifest_path.read_text())
    return data["modules"]


@app.get("/modules/{job_id}/{module_id}/video")
def get_module_video(job_id: str, module_id: int):
    """Stream the concept clip video."""
    job_dir = OUTPUTS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job output not found.")

    matches = list(job_dir.glob(f"Module_{module_id:02d}_*.mp4"))
    if not matches:
        raise HTTPException(status_code=404, detail=f"Video for module {module_id} not found.")

    return FileResponse(
        path       = str(matches[0]),
        media_type = "video/mp4",
        filename   = matches[0].name
    )


@app.get("/modules/{job_id}/{module_id}/notes")
def get_module_notes(job_id: str, module_id: int):
    """Return the notes for a concept module."""
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
    """Generate a quiz for a specific module."""

    manifest_path = OUTPUTS_DIR / req.job_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest not found.")

    data    = json.loads(manifest_path.read_text())
    modules = data["modules"]

    module = next((m for m in modules if m["module_id"] == req.module_id), None)
    if not module:
        raise HTTPException(status_code=404, detail=f"Module {req.module_id} not found.")

    concept    = module["concept"]
    notes      = module["notes"]
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

    try:
        res = requests.post(
            f"{GROQ_BASE_URL}/chat/completions",
            headers=GROQ_HEADERS,
            json={
                "model"       : GROQ_CHAT_MODEL,
                "messages"    : [{"role": "user", "content": prompt}],
                "temperature" : 0.3,
                "max_tokens"  : 2048
            },
            timeout=60
        )
        res.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Groq API error: {e}")

    text = res.json()["choices"][0]["message"]["content"]
    text = text.replace("```json", "").replace("```", "").strip()

    try:
        parsed    = json.loads(text)
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
    except (KeyError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse quiz response: {e}")

    return Quiz(
        quiz_id      = str(uuid.uuid4()),
        module_id    = req.module_id,
        topic        = concept,
        questions    = questions,
        generated_at = datetime.utcnow()
    )


# ─────────────────────────────────────────────
# CHAT — PROF OAK
# ─────────────────────────────────────────────

@app.post("/chat")
async def chat(req: ChatRequest):
    """Prof Oak chat grounded in module notes."""

    module_context = ""
    if req.job_id and req.module_id:
        manifest_path = OUTPUTS_DIR / req.job_id / "manifest.json"
        if manifest_path.exists():
            data    = json.loads(manifest_path.read_text())
            modules = data["modules"]
            module  = next((m for m in modules if m["module_id"] == req.module_id), None)
            if module:
                module_context = f"""
You are currently helping with the module: "{module['concept']}".

MODULE NOTES:
{module['notes']}
"""

    system_prompt = f"""You are Prof Oak — a warm, knowledgeable teaching assistant at VNIT Nagpur.
{module_context}
Answer questions clearly in 2-4 sentences.
Use examples where helpful. Be encouraging but precise.
If a question is outside the current module, gently redirect."""

    messages = [{"role": "system", "content": system_prompt}]
    for msg in req.history[-6:]:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.message})

    try:
        res = requests.post(
            f"{GROQ_BASE_URL}/chat/completions",
            headers=GROQ_HEADERS,
            json={
                "model"       : GROQ_CHAT_MODEL,
                "messages"    : messages,
                "temperature" : 0.5,
                "max_tokens"  : 400
            },
            timeout=30
        )
        res.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Groq API error: {e}")

    reply = res.json()["choices"][0]["message"]["content"]

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