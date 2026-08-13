"""
prep_routes.py — Prep Mode kickoff / resume / navigation endpoints.

Included in main.py exactly like professor_tools:
    from prep_routes import router as prep_router
    app.include_router(prep_router)

The actual *teaching* happens over the existing POST /chat with mode="prep";
these routes only handle setup, resume, and navigation, so the chat pipeline
(struggle detection, mastery, persistence) is reused untouched.

Flow (v2 — free navigation):
  POST /prep/start   {exam_type, target_date}       -> candidate concepts to review
  POST /prep/plan     {exam_type, deselected:[...]}  -> lock plan, get tree + first concept
  GET  /prep/session  ?exam_type=                    -> resume where they left off
  GET  /prep/tree      ?exam_type=                   -> every concept + status, for the tree UI
  POST /prep/jump     {exam_type, concept_id}         -> student picked a concept from the tree

Prep tracks per-student progress, so all routes require login.
"""
from typing import Optional, List

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from supabase_layer import get_current_user, log_interaction
import prep_mode_service as prep

router = APIRouter(prefix="/prep", tags=["prep-mode"])


class PrepStartRequest(BaseModel):
    exam_type: str            # "midsem" | "endsem"
    target_date: str          # ISO date, e.g. "2026-08-20"


class PrepPlanRequest(BaseModel):
    exam_type: str
    deselected: List[str] = []   # concept ids the student already feels solid on


class PrepJumpRequest(BaseModel):
    exam_type: str
    concept_id: str


async def _require_login(request: Request) -> str:
    user_id = await get_current_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required for Prep Mode")
    return user_id


@router.post("/start")
async def prep_start(req: PrepStartRequest, request: Request):
    user_id = await _require_login(request)
    try:
        result = prep.start_prep_session(user_id, req.exam_type, req.target_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=f"Prep rankings not built: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prep start failed: {e}")

    log_interaction(
        student_id=user_id, event_type="prep_start",
        payload={"exam_type": req.exam_type, "days_left": result["days_left"],
                 "n_candidates": len(result["candidates"])},
    )
    return result


@router.post("/plan")
async def prep_plan(req: PrepPlanRequest, request: Request):
    user_id = await _require_login(request)
    try:
        result = prep.finalize_prep_plan(user_id, req.exam_type, req.deselected)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prep plan failed: {e}")

    log_interaction(
        student_id=user_id, event_type="prep_plan",
        payload={"exam_type": req.exam_type, "queue": result["queue"],
                 "deselected": req.deselected},
    )
    return result


@router.get("/session")
async def prep_session(request: Request, exam_type: Optional[str] = None):
    user_id = await _require_login(request)
    return prep.get_prep_session(user_id, exam_type)


@router.get("/tree")
async def prep_tree(request: Request, exam_type: str):
    user_id = await _require_login(request)
    return prep.get_prep_tree(user_id, exam_type)


@router.post("/jump")
async def prep_jump(req: PrepJumpRequest, request: Request):
    user_id = await _require_login(request)
    try:
        result = prep.jump_to_concept(user_id, req.exam_type, req.concept_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prep jump failed: {e}")

    log_interaction(
        student_id=user_id, event_type="prep_jump",
        payload={"exam_type": req.exam_type, "concept_id": req.concept_id},
    )
    return result
