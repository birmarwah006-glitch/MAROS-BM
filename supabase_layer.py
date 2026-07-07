"""
supabase_layer.py — MAROS v2 Supabase integration
Auth, mastery tracking, misconception diagnosis, interaction logging.
Drop this next to main.py.
"""

import os
import json
import uuid
from datetime import datetime
from typing import Optional
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# SUPABASE CLIENT
# ─────────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # service_role key (bypasses RLS)

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("[MAROS] ⚠ SUPABASE_URL or SUPABASE_SERVICE_KEY not set — mastery/logging disabled")
    _sb = None
else:
    _sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    print(f"[MAROS] ✓ Supabase connected: {SUPABASE_URL}")


def get_sb() -> Optional[Client]:
    return _sb


# ─────────────────────────────────────────────
# AUTH — extract student_id from Supabase JWT
# ─────────────────────────────────────────────

async def get_current_user(request: Request) -> Optional[str]:
    """
    FastAPI dependency. Extracts user_id from Supabase JWT.
    Returns None if no auth header (allows gradual migration).
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.replace("Bearer ", "")
    sb = get_sb()
    if not sb:
        return None

    try:
        user_response = sb.auth.get_user(token)
        return user_response.user.id
    except Exception as e:
        print(f"[MAROS] Auth failed: {e}")
        return None


async def require_user(request: Request) -> str:
    """Dependency that REQUIRES auth. Use for protected endpoints."""
    user_id = await get_current_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required")
    return user_id


# ─────────────────────────────────────────────
# INTERACTION LOGGING — the paper dataset
# ─────────────────────────────────────────────

def log_interaction(
    student_id: Optional[str],
    event_type: str,
    course_id: str = None,
    module_id: str = None,
    concept_id: str = None,
    skill_id: str = None,
    payload: dict = None,
    response_time_ms: int = None,
    session_id: str = None,
):
    """Fire-and-forget logging. Never throws — data loss is better than broken UX."""
    sb = get_sb()
    if not sb:
        return

    try:
        sb.table("interaction_log").insert({
            "student_id": student_id,
            "session_id": session_id,
            "event_type": event_type,
            "course_id": course_id,
            "module_id": module_id,
            "concept_id": concept_id,
            "skill_id": skill_id,
            "payload": payload,
            "response_time_ms": response_time_ms,
        }).execute()
    except Exception as e:
        print(f"[MAROS] Log failed (non-fatal): {e}")


# ─────────────────────────────────────────────
# MASTERY TRACKING
# ─────────────────────────────────────────────

def get_student_mastery(student_id: str, course_id: str = None, limit: int = 5) -> list:
    """Get student's weakest concepts for Oak's system prompt."""
    sb = get_sb()
    if not sb or not student_id:
        return []

    try:
        result = sb.rpc("student_mastery_summary", {
            "p_student": student_id,
            "p_course": course_id,
            "p_limit": limit,
        }).execute()
        return result.data or []
    except Exception as e:
        print(f"[MAROS] Mastery fetch failed: {e}")
        return []


def update_mastery(student_id: str, concept_id: str, delta: float):
    """Update mastery score. Negative delta = wrong answer, positive = correct."""
    sb = get_sb()
    if not sb or not student_id:
        return

    try:
        sb.rpc("update_mastery", {
            "p_student": student_id,
            "p_concept": concept_id,
            "p_delta": delta,
        }).execute()
    except Exception as e:
        print(f"[MAROS] Mastery update failed: {e}")


def get_ready_to_learn(student_id: str, course_id: str = None, skill_id: str = None) -> list:
    """What concepts should this student tackle next?"""
    sb = get_sb()
    if not sb or not student_id:
        return []

    try:
        result = sb.rpc("ready_to_learn", {
            "p_student": student_id,
            "p_course": course_id,
            "p_skill": skill_id,
            "p_threshold": 0.6,
        }).execute()
        return result.data or []
    except Exception as e:
        print(f"[MAROS] Ready-to-learn query failed: {e}")
        return []


# ─────────────────────────────────────────────
# MISCONCEPTION DIAGNOSIS
# ─────────────────────────────────────────────

def build_diagnosis_prompt(question: str, chosen: str, correct: str, concept: str) -> str:
    """Build the LLM prompt for misconception diagnosis."""
    return f"""You are an expert CS tutor at VNIT Nagpur.
A student answered a quiz question incorrectly. Explain the correct concept in ONE sentence.

QUESTION: {question}
STUDENT'S ANSWER: {chosen}
CORRECT ANSWER: {correct}
TAGGED CONCEPT: {concept}

Return ONLY valid JSON, no markdown, no backticks:
{{
    "root_concept": "{concept}",
    "misconception": "one sentence: what the student wrongly believes",
    "confidence": 0.8,
    "reasoning": "ONE short sentence explaining the correct concept directly to the student"
}}"""


def save_quiz_answer(
    student_id: Optional[str],
    module_id: str,
    question_text: str,
    options: dict,
    chosen_answer: str,
    correct_answer: str,
    is_correct: bool,
    concept_id: str = None,
    root_concept_id: str = None,
    misconception: str = None,
    diagnosis_confidence: float = None,
):
    """Save a quiz answer to Supabase."""
    sb = get_sb()
    if not sb:
        return

    try:
        sb.table("quiz_answers").insert({
            "student_id": student_id,
            "module_id": module_id,
            "question_text": question_text,
            "options": options,
            "chosen_answer": chosen_answer,
            "correct_answer": correct_answer,
            "is_correct": is_correct,
            "concept_id": concept_id,
            "root_concept_id": root_concept_id,
            "misconception": misconception,
            "diagnosis_confidence": diagnosis_confidence,
        }).execute()
    except Exception as e:
        print(f"[MAROS] Quiz answer save failed: {e}")


# ─────────────────────────────────────────────
# OAK MASTERY CONTEXT BUILDER
# ─────────────────────────────────────────────

def build_mastery_context(student_id: str, course_id: str = None) -> str:
    """
    Returns a string to prepend to Oak's system prompt.
    Empty string if no mastery data or no auth.
    """
    if not student_id:
        return ""

    weak = get_student_mastery(student_id, course_id, limit=5)
    if not weak:
        return ""

    lines = [f"- {w['concept_name']}: {w['score']:.0%} mastery" for w in weak]
    return f"""
STUDENT MASTERY CONTEXT (use naturally, don't list these):
This student is weakest on:
{chr(10).join(lines)}
When relevant, gently steer toward these weak areas. Acknowledge progress on strong areas.
"""


# ─────────────────────────────────────────────
# WEB SEARCH — DuckDuckGo (free, no API key)
# ─────────────────────────────────────────────

def web_search(query: str, max_results: int = 3) -> str:
    """
    Search the web via DuckDuckGo.
    Returns formatted string to inject into Oak's context.
    Returns empty string if search fails or finds nothing.
    """
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return ""

        snippets = []
        for r in results:
            title = r.get("title", "")
            body  = r.get("body", "")
            href  = r.get("href", "")
            snippets.append(f"- {title}: {body} ({href})")

        return "\n\nWEB SEARCH RESULTS (if the student's question matches these results, answer from them — override any topic restrictions):\n" + "\n".join(snippets) + "\n"

    except Exception as e:
        print(f"[MAROS] Web search failed (non-fatal): {e}")
        return ""