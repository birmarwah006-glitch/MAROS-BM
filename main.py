# main.py
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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

from podcastengine import generate_podcast, extract_from_pdf


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
    manifest_path = OUTPUTS_DIR / req.job_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest not found.")

    data    = json.loads(manifest_path.read_text())
    modules = data["modules"]
    module  = next((m for m in modules if m["module_id"] == req.module_id), None)
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
async def assign_paper(file: UploadFile = File(...)):
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
    }
    _save_paper_meta(paper_id, meta)
    print(f"[MAROS] Paper {paper_id} assigned — title: {title[:80]!r}")
    return meta


@app.get("/papers")
def list_papers(visible_only: bool = Query(True)):
    if not PAPERS_DIR.exists():
        return []
    out = []
    for d in PAPERS_DIR.iterdir():
        if d.is_dir() and (d / "meta.json").exists():
            m = json.loads((d / "meta.json").read_text())
            if visible_only and not m.get("visible", True):
                continue
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
    title      : str = "",
    description: str = "",
    due_date   : str = ""
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
    student_name: str = "Student"
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

RAG_API_URL = "http://127.0.0.1:8010/chat"

# ─────────────────────────────────────────────
# OAK PERSONALITY PROMPTS — never exposed to client
# ─────────────────────────────────────────────

OAK_PERSONAS = {
    "videos": {
        "system": """You are Prof Oak — a warm, patient teaching assistant at VNIT Nagpur.
You help students understand lecture concepts deeply, not just memorize them.
Answer in 2-4 sentences. Use analogies and examples. Be encouraging but precise.
If a question is outside the current module, gently redirect back to it.""",

        "refine": """You are Prof Oak — a warm, encouraging teaching assistant at VNIT Nagpur.
Another system retrieved accurate grounded information. Your job: rewrite it as a
natural, conversational explanation a kind tutor would give sitting next to the student.

RETRIEVED INFORMATION:
{rag_answer}

- Keep every fact and priority level — change nothing factual
- Talk it through naturally, no bullet lists
- Be encouraging but precise
- Do NOT mention "retrieved information" or another system — speak as your own knowledge
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


def _call_groq(messages: list, temperature: float = 0.5, max_tokens: int = 400) -> str:
    """Shared helper for calling Groq chat completions."""
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


def _plain_oak_reply(req: ChatRequest, module_context: str) -> str:
    """Oak grounded in module notes / paper abstract — no RAG."""
    persona       = OAK_PERSONAS.get(req.mode, OAK_PERSONAS["videos"])
    system_prompt = persona["system"]
    if module_context:
        system_prompt += f"\n\nCONTEXT:\n{module_context}"

    messages = [{"role": "system", "content": system_prompt}]
    for msg in req.history[-6:]:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.message})

    return _call_groq(messages, temperature=0.5, max_tokens=400)


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Prof Oak chat.
    - Videos tab  → RAG-grounded (OS exam papers)
    - Papers tab  → direct Oak with paper context (no RAG)
    - Assignments → direct Oak in Socratic mode (no RAG)
    """

    module_context = ""
    module_concept = None

    # ── Module context (videos tab) ─────────────────────────────────────────
    if req.job_id and req.module_id:
        manifest_path = OUTPUTS_DIR / req.job_id / "manifest.json"
        if manifest_path.exists():
            data   = json.loads(manifest_path.read_text())
            module = next((m for m in data["modules"] if m["module_id"] == req.module_id), None)
            if module:
                module_concept = module["concept"]
                module_context = f"""You are currently helping with the module: "{module['concept']}".

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

    # ── RAG only for videos tab ─────────────────────────────────────────────
    rag_concept = module_concept or paper_concept

    if rag_concept and req.mode == "videos":
        print(f"[MAROS] Attempting RAG — concept: {rag_concept!r}")
        try:
            rag_res = requests.post(
                RAG_API_URL,
                json={
                    "query"     : f"{rag_concept}: {req.message}",
                    "session_id": req.paper_id or f"{req.job_id}_{req.module_id}",
                    "n_results" : 5
                },
                timeout=15
            )
            rag_res.raise_for_status()
            rag_data = rag_res.json()

            if rag_data.get("num_chunks_used", 0) == 0:
                raise ValueError("RAG found no relevant chunks")

            persona       = OAK_PERSONAS.get(req.mode, OAK_PERSONAS["videos"])
            refine_prompt = persona["refine"].format(rag_answer=rag_data["answer"])
            reply         = _call_groq(
                messages    = [{"role": "system", "content": refine_prompt}],
                temperature = 0.5,
                max_tokens  = 400
            )

            return ChatMessage(
                role      = ChatRole.assistant,
                content   = reply,
                module_id = req.module_id,
                timestamp = datetime.utcnow()
            )

        except Exception as e:
            print(f"[MAROS] RAG path failed, falling back to plain Oak: {e}")

    else:
        print(f"[MAROS] Skipping RAG — mode: {req.mode!r}, going direct to Oak persona")

    # ── Direct Oak reply (papers, assignments, or RAG fallback) ────────────
    try:
        reply = _plain_oak_reply(req, module_context)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Groq API error: {e}")

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