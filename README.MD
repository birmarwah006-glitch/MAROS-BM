<div align="center">

# MAROS
### Marwah Operating System

*An AI-native adaptive learning platform, built for the VNIT Nagpur Operating Systems course*

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![Supabase](https://img.shields.io/badge/database-Supabase-3ECF8E.svg)](https://supabase.com/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](#license)

</div>

---

MAROS turns a single recorded lecture into a full adaptive learning loop — concept-segmented video modules, an AI tutor that actually diagnoses *why* a student got something wrong, and a real-time analytics dashboard the professor can act on before the next class.

Built solo, shipped fast, running on free-tier infrastructure end to end.

---

## What it does

**For students**
- Drop in a lecture (upload or paste a YouTube link) → it's automatically segmented into concept-level video modules
- Watch a clip, then take an auto-generated quiz on just that concept
- **Prof Oak**, an AI teaching assistant with three distinct personas depending on context — a warm tutor for lecture content, a Socratic coach for assignments (never gives the answer directly), and a direct execution-mode assistant for research papers
- Get real misconception diagnosis on wrong quiz answers, not just "incorrect" — Oak identifies the *root conceptual gap*, not the surface mistake
- Drop a research paper into the built-in converter and get a two-host podcast (Bir + Mia) generated from it, then discuss it with Oak
- Ask Oak anything, with a file/image attach button — it reads PDFs and transcribes images inline

**For professors**
- One-click upload → Chipper pipeline transcribes, segments, and generates notes automatically
- Live class analytics: accuracy trends, weakest concepts by error rate, detected misconceptions aggregated across the whole class, top/bottom performers
- One-click AI-generated class report — names specific students needing attention, specific misconceptions, and one concrete teaching recommendation for the next lecture
- Post assignments and research papers, toggle visibility, track submissions per student

---

## Architecture

```
Lecture (video/audio/YouTube)
        │
        ▼
   ┌─────────┐      faster-whisper transcription (beam_size=1)
   │ Chipper │  →   LLM-based concept segmentation (12-concept OS graph)
   └─────────┘      per-module notes + clip cutting via ffmpeg
        │
        ▼
  Concept Modules ──→ Quiz Generation ──→ Misconception Diagnosis
        │                                         │
        ▼                                         ▼
   Prof Oak Chat  ←──── ChromaDB RAG ────  Mastery Tracking (Supabase)
   (3 personas)         (exam papers,           │
        │                past questions)         ▼
        │                                  Professor Analytics
        └──────────────────────────────────────→ Dashboard
```

**LLM routing:** Cerebras (`gpt-oss-120b`, primary — 1M tokens/day free tier) with automatic Groq fallback on failure.

**Adaptive learning core:** built on ideas extracted from *GraphMASAL* (AAMAS 2026) — specifically its structured teaching-prompt format, struggle-detection pattern, and a 12-concept OS prerequisite graph used to drive mastery-based recommendations.

---

## Tech stack

| Layer | Tech |
|---|---|
| Backend | FastAPI, Python |
| Transcription | faster-whisper |
| Vector search / RAG | ChromaDB, `all-MiniLM-L6-v2` embeddings |
| LLM | Cerebras (primary) → Groq (fallback) |
| Database & Auth | Supabase (Postgres + Auth) |
| Video processing | ffmpeg, yt-dlp |
| Frontend | Vanilla JS, HTML/CSS (no framework — deliberately lightweight) |
| Deploy | Render |

---

## Project structure

```
MAROS-BM/
├── main.py              # FastAPI app — all routes
├── chipper.py            # Video/audio → transcript → concept modules pipeline
├── rag.py                 # ChromaDB RAG context builder
├── supabase_layer.py       # Mastery tracking, interaction logging, auth
├── podcastengine.py         # PDF → two-host podcast generation
├── config.py                # Env config, CORS, provider headers
├── models.py                  # Pydantic schemas
├── jobs.py                     # In-memory job/status tracking
├── seed_bots.py                  # Fake student seeding for demo/testing
└── frontend/
    ├── index.html          # Landing + login (Supabase auth)
    ├── student.html         # Student portal — videos, papers, assignments, chat
    ├── professor.html         # Professor dashboard — upload, analytics, chat
    ├── api.js                   # All backend calls
    └── style.css                  # Shared design system
```

---

## Running locally

```bash
# Backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# .env — see .env.example
CEREBRAS_API_KEY=...
GROQ_API_KEY=...
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...

uvicorn main:app --reload

# Frontend is served at /app once the backend is running:
# http://localhost:8000/app/index.html
```

Requires `ffmpeg` and `yt-dlp` on PATH for lecture ingestion.

---

## Status

Actively developed ahead of a July 2026 deployment for VNIT Nagpur's third-year CS Operating Systems course. Built and maintained solo.

A research paper covering the adaptive learning approach is in progress, targeting EDM/LAK venues.

---

## Author

**Bir Marwah** — VNIT Nagpur, 1517 Fund Fellow