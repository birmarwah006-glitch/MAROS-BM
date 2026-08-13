"""
MAROS Quick Explain routes v3 (podcast + audio chat)
Same plug-in pattern — already wired in main.py:

    from explain_routes import router as explain_router
    app.include_router(explain_router)

This is the file main.py is importing FROM — it must live at explain_routes.py
in your project root next to main.py, explainchat.py, and explainengine.py.

Endpoints:
    POST   /papers/{paper_id}/explain/chat              -> ask a question,
                                                             get {answer, audio_url, seq}
    GET    /papers/{paper_id}/explain/chat               -> {history: [...]}
    DELETE /papers/{paper_id}/explain/chat               -> reset the chat
    GET    /papers/{paper_id}/explain/chat/audio/{seq}   -> that turn's mp3

Old one-shot whole-notes audio routes are REMOVED (superseded by the chat
above — Quick Explain is now the chat, not a single pre-baked mp3). If your
frontend still calls POST /papers/{paper_id}/explain (no /chat) anywhere,
that call will now 404 on purpose — it should be routing to the chat instead.

Storage:
    outputs/_papers/{paper_id}/explain_chat.json   (chat history)
    output/{paper_id}_explain_chat_{seq:03d}.mp3   (per-turn answer audio)
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import OUTPUTS_DIR
from explainchat import answer_question, render_chat_audio

router = APIRouter()

PAPERS_DIR = OUTPUTS_DIR / "_papers"


# ── local meta helpers (duplicated from main.py to avoid circular import) ──
def _paper_dir(paper_id: str) -> Path:
    return PAPERS_DIR / paper_id


def _load_meta(paper_id: str) -> dict:
    meta_path = _paper_dir(paper_id) / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found.")
    return json.loads(meta_path.read_text())


def _chat_path(paper_id: str) -> Path:
    return _paper_dir(paper_id) / "explain_chat.json"


def _load_history(paper_id: str) -> list:
    p = _chat_path(paper_id)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _save_history(paper_id: str, history: list):
    _chat_path(paper_id).write_text(json.dumps(history, indent=2))


# ── optional RAG grounding — accuracy over speed for institutional sales ──
def _grounding_for(title: str, material: str) -> str:
    """Pull course RAG context so explanations stay syllabus-accurate.
    Non-fatal: no RAG hit -> empty string, pipeline continues."""
    try:
        from rag import build_rag_context
        query = f"{title} {material[:300]}"
        ctx = build_rag_context(query, n_results=4)
        return ctx or ""
    except Exception as e:
        print(f"[MAROS] Explain RAG grounding skipped (non-fatal): {e}")
        return ""


# ── chat routes (the "what didn't you get" audio chatbox) ──────
@router.post("/papers/{paper_id}/explain/chat")
async def explain_chat(paper_id: str, body: dict):
    """Ask one question about the uploaded material. Body: {"question": "..."}
    Returns the answer AS TEXT plus a URL to its rendered audio."""
    meta = _load_meta(paper_id)

    question = (body or {}).get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Empty question.")

    material = meta.get("abstract") or meta.get("title") or ""
    if not material:
        raise HTTPException(status_code=400, detail="This paper has no material to explain.")

    history = _load_history(paper_id)
    grounding = _grounding_for(meta.get("title", ""), material)

    # 1) generate the spoken-style text answer
    answer = answer_question(material, history, question, grounding=grounding)

    # 2) figure out this turn's sequence number (0-indexed by prior assistant turns)
    seq = sum(1 for m in history if m.get("role") == "assistant")

    # 3) render it to audio — non-fatal: chat still works with text-only if TTS fails
    has_audio = False
    try:
        await render_chat_audio(answer, paper_id, seq)
        has_audio = True
    except Exception as e:
        print(f"[MAROS] Explain chat TTS failed for {paper_id} seq {seq} (non-fatal): {e}")

    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer, "seq": seq, "has_audio": has_audio})
    _save_history(paper_id, history)

    return {
        "paper_id": paper_id,
        "answer": answer,
        "seq": seq,
        "has_audio": has_audio,
        "audio_url": f"/papers/{paper_id}/explain/chat/audio/{seq}" if has_audio else None,
        "history": history,
    }


@router.get("/papers/{paper_id}/explain/chat")
def get_explain_chat(paper_id: str):
    _load_meta(paper_id)  # 404s if the paper doesn't exist
    return {"paper_id": paper_id, "history": _load_history(paper_id)}


@router.delete("/papers/{paper_id}/explain/chat")
def reset_explain_chat(paper_id: str):
    _load_meta(paper_id)
    p = _chat_path(paper_id)
    if p.exists():
        p.unlink()
    # also clean up any rendered audio files for this paper's chat
    for f in Path("output").glob(f"{paper_id}_explain_chat_*.mp3"):
        try:
            f.unlink()
        except Exception:
            pass
    return {"paper_id": paper_id, "history": []}


@router.get("/papers/{paper_id}/explain/chat/audio/{seq}")
def get_explain_chat_audio(paper_id: str, seq: int):
    audio_path = Path("output") / f"{paper_id}_explain_chat_{seq:03d}.mp3"
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail=f"Audio for turn {seq} not found.")
    return FileResponse(
        path=str(audio_path),
        media_type="audio/mpeg",
        filename=f"explain_{paper_id}_{seq}.mp3",
    )