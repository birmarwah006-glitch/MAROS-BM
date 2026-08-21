"""
meal_routes.py — Meal catalogue and media.

NOT YET MOUNTED. This file is self-contained and changes no existing module.
To serve Meals from the Python backend, add two lines to main.py alongside the
five routers already there:

    from meal_routes import router as meal_router
    app.include_router(meal_router)

Until then the web app's dev server serves the same shapes from the same files
(see web/vite.config.ts), so the frontend is written against this contract and
not against a mock.

Endpoints:
    GET /meals                     the catalogue: every renderable Meal
    GET /meals/{meal_id}           one Meal document
    GET /meals/{meal_id}/video     the rendered 1080x1920 MP4
    GET /meals/{meal_id}/audio     the narration track
    GET /meals/{meal_id}/timing    captions and beat anchors

Reads straight off disk, like /lectures does, so it survives a restart and
needs no database.

Terminology note: the route prefix is /meals because this is a new surface with
no legacy callers. The older /reels and /clipper/reels routes keep their names
— they have clients — and only what a learner READS says "Meal".
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import BASE_DIR

MEALS_DIR = BASE_DIR / "meals"
CATALOGUE_DIR = MEALS_DIR / "catalogue"
BUILD_DIR = MEALS_DIR / "build"
OUT_DIR = MEALS_DIR / "out"

router = APIRouter(prefix="/meals", tags=["meals"])


def _load(meal_id: str) -> dict:
    path = CATALOGUE_DIR / f"{meal_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Meal {meal_id} not found.")
    return json.loads(path.read_text())["meal"]


def _video_path(meal_id: str) -> Path:
    return OUT_DIR / f"{meal_id}.mp4"


def _summary(meal: dict) -> dict:
    """The shape the feed consumes. Deliberately small — the feed renders many
    of these and does not need the full scene graph."""
    meal_id = meal["id"]
    return {
        "id": meal_id,
        "title": meal["title"],
        "concept": meal["concept"],
        "objective": meal["objective"],
        "difficulty": meal.get("difficulty"),
        "prerequisites": meal.get("prerequisites", []),
        "next_concepts": meal.get("next_concepts", []),
        "practice": meal.get("practice"),
        "video_url": f"/meals/{meal_id}/video",
        "timing_url": f"/meals/{meal_id}/timing",
        "duration_sec": _duration(meal_id),
    }


def _duration(meal_id: str) -> float | None:
    path = BUILD_DIR / f"{meal_id}.timing.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text()).get("duration")
    except Exception:
        return None


@router.get("")
def list_meals():
    """Every Meal that has actually been rendered.

    A Meal with no MP4 on disk is omitted rather than listed as broken: the
    feed's contract is that everything in it is watchable."""
    if not CATALOGUE_DIR.exists():
        return {"meals": []}

    meals = []
    for path in sorted(CATALOGUE_DIR.glob("*.json")):
        try:
            meal = json.loads(path.read_text())["meal"]
        except Exception as e:
            print(f"[MAROS] Skipping bad Meal {path.name}: {e}")
            continue
        if not _video_path(meal["id"]).exists():
            continue
        meals.append(_summary(meal))

    return {"meals": meals}


@router.get("/{meal_id}")
def get_meal(meal_id: str):
    return _load(meal_id)


@router.get("/{meal_id}/timing")
def get_timing(meal_id: str):
    path = BUILD_DIR / f"{meal_id}.timing.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Timing sidecar not found.")
    return json.loads(path.read_text())


@router.get("/{meal_id}/video")
def get_video(meal_id: str):
    path = _video_path(meal_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Meal video not rendered yet.")
    return FileResponse(path=str(path), media_type="video/mp4", filename=f"{meal_id}.mp4")


@router.get("/{meal_id}/audio")
def get_audio(meal_id: str):
    path = BUILD_DIR / f"{meal_id}.mp3"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Meal narration not found.")
    return FileResponse(path=str(path), media_type="audio/mpeg", filename=f"{meal_id}.mp3")
