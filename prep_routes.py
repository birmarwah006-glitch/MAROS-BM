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
  GET  /prep/paper     ?exam_type=                     -> the predicted exam paper + backtest scores
  POST /prep/stage    {exam_type, stage}               -> explicit teach/quiz/practice transition

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

class PrepStageRequest(BaseModel):
    exam_type: str
    stage: str                # "teach" | "quiz" | "practice"


@router.get("/paper")
async def prep_paper(request: Request, exam_type: str):
    """The predicted exam paper (real PYQs assembled per the slot ranking +
    blueprint), with its walk-forward backtest attached so accuracy is shown,
    not implied. Frozen artifact — rebuild with paper_predictor.py --build."""
    await _require_login(request)
    try:
        return prep.get_predicted_paper(exam_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=f"Predicted paper not built: {e}")


@router.post("/stage")
async def prep_stage(req: PrepStageRequest, request: Request):
    """Explicit stage transition for the current concept — e.g. the frontend's
    'Start quiz' / 'Retry quiz' buttons. Marker-driven advancement (quiz pass,
    practice done) happens automatically inside /chat via parse_prep_markers."""
    user_id = await _require_login(request)
    try:
        result = prep.set_prep_stage(user_id, req.exam_type, req.stage)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prep stage failed: {e}")

    log_interaction(
        student_id=user_id, event_type="prep_stage",
        payload={"exam_type": req.exam_type, "stage": req.stage},
    )
    return result

@router.get("/important-questions")
async def prep_important_questions(request: Request, exam_type: str, k: int = 30, concept: str = None):
    """Top-30 most important real past questions ranked by importance,
    with worked answers where available. Frozen artifact — rebuild with
    paper_predictor.py --build."""
    await _require_login(request)
    try:
        items = prep.get_important_questions(exam_type, k=k, concept=concept)
        return {"exam_type": exam_type, "questions": items}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=f"Important questions not built: {e}")
