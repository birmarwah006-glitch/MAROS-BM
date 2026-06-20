# models.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from enum import Enum

# ─────────────────────────────────────────────
# JOB
# ─────────────────────────────────────────────

class JobStatus(str, Enum):
    queued       = "queued"
    transcribing = "transcribing"
    segmenting   = "segmenting"
    cutting      = "cutting"
    summarizing  = "summarizing"
    done         = "done"
    failed       = "failed"

class Job(BaseModel):
    job_id      : str
    status      : JobStatus
    progress    : int = 0          # 0–100
    error       : Optional[str] = None
    created_at  : datetime

# ─────────────────────────────────────────────
# MODULE
# ─────────────────────────────────────────────

class Module(BaseModel):
    module_id    : int
    concept      : str
    start        : str             # "MM:SS"
    end          : str             # "MM:SS"
    duration_sec : float
    video_url    : str             # "/modules/{id}/video"
    notes        : str
    transcript   : str

# ─────────────────────────────────────────────
# MANIFEST
# ─────────────────────────────────────────────

class Manifest(BaseModel):
    job_id        : str
    video_source  : str
    total_modules : int
    modules       : list[Module]
    generated_at  : datetime

# ─────────────────────────────────────────────
# QUIZ
# ─────────────────────────────────────────────

class QuizQuestion(BaseModel):
    question       : str
    options        : dict[str, str]   # {"A": "...", "B": "...", ...}
    correct_answer : str              # "A" | "B" | "C" | "D"
    explanation    : str
    module_id      : int

class Quiz(BaseModel):
    quiz_id      : str
    module_id    : int
    topic        : str
    questions    : list[QuizQuestion]
    generated_at : datetime

# ─────────────────────────────────────────────
# CHAT
# ─────────────────────────────────────────────

class ChatRole(str, Enum):
    user      = "user"
    assistant = "assistant"

class ChatMessage(BaseModel):
    role      : ChatRole
    content   : str
    module_id : Optional[int] = None
    timestamp : datetime



# ─────────────────────────────────────────────
# REQUESTS
# ─────────────────────────────────────────────

class QuizGenerateRequest(BaseModel):
    job_id        : str
    module_id     : int
    num_questions : int = 5


class ChatRequest(BaseModel):
    job_id    : Optional[str] = None
    message   : str
    module_id : Optional[int] = None
    history   : list[ChatMessage] = []