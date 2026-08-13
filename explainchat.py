"""
MAROS Quick Explain — Chat module v2 (audio answers)
File name matches your project exactly: explainchat.py (no underscore).

The "what didn't you get" chatbox. The student types what they're stuck on;
the answer comes back as AUDIO (single voice), grounded in their uploaded notes.

This is the deliberate contrast with the Prof Oak sidebar (which answers in
text). Quick Explain = ask what you didn't get, hear it explained.

Design:
- A 1-2 problem / short-notes upload is small enough to hold entirely in
  context, so NO vector DB / RAG retrieval is needed. The uploaded material IS
  the knowledge base — we reuse classify_blocks() to chunk it and drop it into
  the system context every turn.
- answer_question() produces a SPOKEN-style answer (TTS-friendly: no markdown,
  equations in words, tight). The route then TTS's it via render_chat_audio().
- Reuses _safe_llm() so a bad LLM turn never raises, and _tts_with_retry() /
  OUTPUT_DIR from the podcast engine for the actual voice synthesis.

Exposes (imported by explain_routes.py):
    answer_question(material, history, question, grounding="") -> str
    render_chat_audio(text, paper_id, seq) -> Path            (async)
    ANSWER_WORD_CAP                                            (int)
"""

import asyncio
import re

from explainengine import classify_blocks, _safe_llm, EXPLAINER_VOICE
from podcastengine import _tts_with_retry, OUTPUT_DIR

# How many past messages to replay for continuity. 6 = last 3 exchanges.
HISTORY_WINDOW = 6

# Spoken answers stay tight so each clip is a focused ~30-60s, not a lecture.
ANSWER_WORD_CAP = 150

CHAT_SYSTEM = f"""You are Prof Oak's Quick Explain voice for a MAROS student (VNIT Nagpur).
The student uploaded some notes or a problem set and just told you what they did
NOT understand. Your answer will be READ ALOUD by a text-to-speech voice, so it
must sound natural when spoken.

RULES:
- Ground every answer in the uploaded material below. Do NOT introduce outside
  topics or facts not in the material or the reference context.
- Answer exactly what they asked. If they say "I don't get step 3", explain
  step 3 — do not re-explain the whole thing.
- Teach the reasoning, don't just state the answer: why this step, why this
  approach works. If they clearly just want the final answer, give it, then say
  briefly why.
- If a worked solution is not in the notes, explain WHICH approach to use and
  why. Do not invent a numeric final answer the material doesn't support.
- SPOKEN OUTPUT: plain sentences only. No markdown, no bullet points, no code
  fences, no LaTeX. Say any equation in words ("x squared plus two x").
- Keep it under {ANSWER_WORD_CAP} words — one clear spoken explanation. End with
  a short check like "does that clear it up?" only when it fits naturally.
- Start explaining immediately. No "welcome", no intro, no sign-off."""


def _material_context(material: str, grounding: str = "") -> str:
    """Build the system-side context: the uploaded material (chunked) plus any
    optional course grounding. This is the only thing the chat may explain."""
    blocks = classify_blocks(material)
    if blocks:
        blocks_text = "\n\n".join(
            f"[{b['type']}] {b['title']}\n{b['content']}" for b in blocks
        )
    else:
        blocks_text = material[:4000]  # classify failed — fall back to raw text

    ctx = f"UPLOADED MATERIAL (the only thing you may explain):\n{blocks_text}"
    if grounding:
        ctx += f"\n\nREFERENCE CONTEXT (for factual accuracy, do not add new topics):\n{grounding[:2000]}"
    return ctx


def answer_question(
    material: str,
    history: list,
    question: str,
    grounding: str = "",
) -> str:
    """Generate ONE spoken-style answer to a student question about the material.

    history: list of {"role": "user"|"assistant", "content": str}
    Returns plain text ready for TTS (never raises)."""
    messages = [
        {"role": "system", "content": CHAT_SYSTEM},
        {"role": "user", "content": _material_context(material, grounding)},
        {"role": "assistant", "content": "Got it, I have the material. What didn't you understand?"},
    ]
    # replay recent history (strip any stored audio metadata — keep role/content)
    for m in history[-HISTORY_WINDOW:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})

    answer = _safe_llm(messages, temperature=0.4, max_tokens=600, label="explain-chat")
    return answer.strip() if answer else (
        "Sorry, I couldn't put that into words just now. Try telling me the exact "
        "step or term you're stuck on and I'll take another run at it."
    )


# ── Audio rendering for a single chat answer ─────────────
def _chunk_sentences(text: str, per: int = 2) -> list:
    """Split an answer into small sentence groups so TTS stays reliable and we
    can stitch, mirroring the podcast/segment approach."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks, buf = [], []
    for p in parts:
        if not p:
            continue
        buf.append(p)
        if len(buf) >= per:
            chunks.append(" ".join(buf))
            buf = []
    if buf:
        chunks.append(" ".join(buf))
    return chunks or [text.strip() or "No answer."]


async def render_chat_audio(text: str, paper_id: str, seq: int):
    """TTS one chat answer to a single mp3, return its Path. Raises only if it
    produced no usable audio at all (route catches and still returns text)."""
    from pydub import AudioSegment

    chunks = _chunk_sentences(text)
    sem = asyncio.Semaphore(6)

    async def _bounded(t, path, idx):
        async with sem:
            try:
                return await _tts_with_retry(t, EXPLAINER_VOICE, path, idx)
            except Exception as e:
                print(f"  [explain-chat-tts] chunk {idx} failed: {e}", flush=True)
                return False

    tasks, paths = [], []
    for i, c in enumerate(chunks):
        path = OUTPUT_DIR / f"{paper_id}_ec_{seq:03d}_{i:02d}.mp3"
        paths.append(path)
        tasks.append(_bounded(c, path, i))

    await asyncio.gather(*tasks, return_exceptions=True)

    final = AudioSegment.empty()
    any_ok = False
    for path in paths:
        if not path.exists():
            continue
        try:
            final += AudioSegment.from_mp3(str(path))
            any_ok = True
        except Exception as e:
            print(f"  [explain-chat-stitch] bad chunk {path}, skipping ({e})", flush=True)

    # clean up the per-chunk temp files
    for path in paths:
        if path.exists():
            path.unlink()

    if not any_ok:
        raise RuntimeError("chat answer produced no usable audio")

    out_path = OUTPUT_DIR / f"{paper_id}_explain_chat_{seq:03d}.mp3"
    final.export(str(out_path), format="mp3", bitrate="128k")
    return out_path


if __name__ == "__main__":
    demo = (
        "Paging splits memory into fixed pages and frames. "
        "Solved: 16-bit address, 2KB page -> offset 11 bits, page number 5 bits, 32 entries."
    )
    hist = []
    for q in ["why is the offset 11 bits?", "where does 32 entries come from?"]:
        a = answer_question(demo, hist, q)
        print(f"\nQ: {q}\nA: {a}")
        hist += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    asyncio.run(render_chat_audio(hist[1]["content"], "demo", 0))
    print("\n-- rendered demo audio --")