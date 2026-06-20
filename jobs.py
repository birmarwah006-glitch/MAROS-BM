# jobs.py
import uuid
from datetime import datetime
from typing import Optional
from models import Job, JobStatus

# ─────────────────────────────────────────────
# IN-MEMORY JOB STORE
# (single server process — fine for now)
# ─────────────────────────────────────────────

_jobs: dict[str, Job] = {}


# ─────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────

def create_job() -> Job:
    job = Job(
        job_id     = str(uuid.uuid4()),
        status     = JobStatus.queued,
        progress   = 0,
        error      = None,
        created_at = datetime.utcnow()
    )
    _jobs[job.job_id] = job
    return job


# ─────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────

def get_job(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def all_jobs() -> list[Job]:
    return list(_jobs.values())


# ─────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────

def update_job(
    job_id   : str,
    status   : Optional[JobStatus] = None,
    progress : Optional[int]       = None,
    error    : Optional[str]       = None
) -> Optional[Job]:
    job = _jobs.get(job_id)
    if not job:
        return None

    if status   is not None: job.status   = status
    if progress is not None: job.progress = progress
    if error    is not None: job.error    = error

    _jobs[job_id] = job
    return job


# ─────────────────────────────────────────────
# SHORTCUTS
# ─────────────────────────────────────────────

def fail_job(job_id: str, error: str) -> None:
    update_job(job_id, status=JobStatus.failed, progress=0, error=error)

def complete_job(job_id: str) -> None:
    update_job(job_id, status=JobStatus.done, progress=100)