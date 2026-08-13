"""
MAROS Quick Explain Engine v1.1 (hardened)
Single-voice, no-fluff audio explanation of a student's uploaded notes.

v1.1 changes:
- Never crashes on an undefined `raw` (was the "cannot access local variable
  'raw'" error). Every LLM call is wrapped; `raw` is always defined.
- Prints the raw LLM response so failures are diagnosable.
- Robust JSON extraction: strips prose/fences, finds the [...] array itself.
- If classification fails entirely, falls back to a single whole-notes block
  so the student still gets audio instead of a hard failure.
"""

import asyncio
import json
from pathlib import Path

from pydub import AudioSegment

# Reused, unmodified, from the podcast engine
from podcastengine import llm_chat, _tts_with_retry, OUTPUT_DIR


# ── Config ──────────────────────────────────────────────
EXPLAINER_VOICE = "en-US-BrianNeural"
MAX_BLOCKS = 6
PAUSE_BETWEEN_BLOCKS_MS = 700


# ── Robust JSON array extraction ─────────────────────────
def _extract_json_array(raw: str):
    """Pull a JSON array out of an LLM reply, tolerating fences/prose.
    Returns [] on total failure instead of raising."""
    if not raw or not raw.strip():
        return []
    txt = raw.replace("```json", "").replace("```", "").strip()
    start, end = txt.find("["), txt.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    chunk = txt[start:end + 1]
    # kill trailing commas before ] or }
    import re
    chunk = re.sub(r",\s*([\]}])", r"\1", chunk)
    try:
        data = json.loads(chunk)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _safe_llm(messages, temperature, max_tokens, label):
    """Call the shared LLM; always return a string, never raise. Logs raw."""
    raw = ""
    try:
        raw = llm_chat(messages, temperature=temperature, max_tokens=max_tokens) or ""
    except Exception as e:
        print(f"  [explain] LLM call FAILED during {label}: {e}", flush=True)
        return ""
    print(f"  [explain] {label} raw ({len(raw)} chars): {raw[:200]!r}", flush=True)
    return raw


# ── Step 1: classify note-blocks ────────────────────────
CLASSIFY_SYSTEM = """You segment study notes into blocks for an audio explainer.
Return ONLY a valid JSON array, no markdown, no trailing commas, no prose."""

def classify_blocks(material: str) -> list[dict]:
    raw = _safe_llm(
        [
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content": f"""Study notes:
{material[:5000]}

Split into at most {MAX_BLOCKS} blocks. Tag each:
- "concept": a definition/theory/idea with no worked problem
- "solved_example": a specific question, worked problem, solved example, or design choice

Return ONLY this JSON array:
[
  {{"type": "concept", "title": "short name", "content": "the relevant text"}},
  {{"type": "solved_example", "title": "short name", "content": "the question + solution steps"}}
]"""},
        ],
        temperature=0.2, max_tokens=1800, label="classify",
    )
    blocks = _extract_json_array(raw)
    clean = []
    for b in blocks:
        if isinstance(b, dict) and b.get("content") and b.get("type") in ("concept", "solved_example"):
            clean.append({
                "type": b["type"],
                "title": str(b.get("title") or "Untitled")[:80],
                "content": str(b["content"])[:2000],
            })
    return clean[:MAX_BLOCKS]


# ── Step 2: single-voice script ─────────────────────────
SCRIPT_SYSTEM = """You write a single-voice audio explanation script for MAROS (VNIT Nagpur).
Audience: college students who want it QUICK and CLEAR. One calm expert voice.

ABSOLUTE RULES:
- NO intro, NO "welcome", NO outro, NO filler. Start explaining immediately.
- Plain spoken language for TTS. No markdown, no bullets, no LaTeX. Say equations in words.
- Ground every fact in the source block. Never invent facts.

TEMPLATES — follow exactly:

For a "concept" block (MAX 90 words):
1. What it is — one tight sentence.
2. How it works — 2-3 sentences.
3. ONE relatable real-life analogy ("it's like...").

For a "solved_example" block (MAX 140 words):
1. Name the approach/method used, explicitly.
2. Why THIS approach fits better than the obvious alternative.
3. Walk the key steps briefly, in words.
4. ONE real-life analogy that mirrors the REASONING, not just the topic.

If a solved_example has a question but NO worked solution: explain WHICH approach
to use and why — do NOT invent a numeric final answer.

OUTPUT: ONLY a valid JSON array, no fences, no trailing commas:
[{"title": "...", "text": "..."}]"""


def generate_explain_script(blocks: list[dict], grounding_context: str = "") -> list[dict]:
    blocks_text = "\n\n".join(
        f"[BLOCK {i+1}] type={b['type']} title={b['title']}\n{b['content']}"
        for i, b in enumerate(blocks)
    )
    grounding_block = (
        f"\n\nREFERENCE CONTEXT (keep facts accurate, never add topics):\n{grounding_context[:3000]}"
        if grounding_context else ""
    )
    messages = [
        {"role": "system", "content": SCRIPT_SYSTEM},
        {"role": "user", "content": f"""Blocks to explain, in order:

{blocks_text}{grounding_block}

One script segment per block, same order. Respect the word caps.
Return ONLY the JSON array."""},
    ]

    for attempt in range(2):
        raw = _safe_llm(messages, 0.6 if attempt == 0 else 0.3, 2500, f"script(try{attempt+1})")
        segs = _extract_json_array(raw)
        out = []
        for i, s in enumerate(segs):
            if isinstance(s, dict) and s.get("text"):
                out.append({
                    "title": str(s.get("title") or (blocks[i]["title"] if i < len(blocks) else f"Part {i+1}")),
                    "text": str(s["text"]).strip(),
                })
        if out:
            return out
        if attempt == 0:
            messages.append({"role": "assistant", "content": raw[:1500]})
            messages.append({"role": "user", "content": "That was not valid JSON. Return ONLY the JSON array — no fences, no prose, no trailing commas."})

    # Last-resort fallback: read each block's content aloud so audio still ships.
    print("  [explain] script gen failed twice — using raw-notes fallback", flush=True)
    return [{"title": b["title"], "text": b["content"]} for b in blocks]


# ── Step 3: TTS + stitch ─────────────────────────────────
async def _render_audio(segments: list[dict], job_id: str) -> Path:
    sem = asyncio.Semaphore(8)

    async def _bounded(text, path, idx):
        async with sem:
            return await _tts_with_retry(text, EXPLAINER_VOICE, path, idx)

    tasks, paths = [], []
    for i, seg in enumerate(segments):
        path = OUTPUT_DIR / f"{job_id}_explain_seg_{i:03d}.mp3"
        paths.append(path)
        tasks.append(_bounded(seg["text"], path, i))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    successes = sum(1 for r in results if r is True)
    print(f"  [explain-tts] {successes}/{len(segments)} segments rendered", flush=True)
    if successes < max(1, int(len(segments) * 0.8)):
        raise RuntimeError(f"TTS failed on {len(segments) - successes}/{len(segments)} segments")

    final = AudioSegment.empty()
    first = True
    for path in paths:
        if not path.exists():
            continue
        try:
            clip = AudioSegment.from_mp3(str(path))
        except Exception as e:
            print(f"  [explain-stitch] corrupt segment {path}, skipping ({e})", flush=True)
            continue
        if not first:
            final += AudioSegment.silent(duration=PAUSE_BETWEEN_BLOCKS_MS)
        final += clip
        first = False

    out_path = OUTPUT_DIR / f"{job_id}_explain.mp3"
    final.export(str(out_path), format="mp3", bitrate="128k")
    for path in paths:
        if path.exists():
            path.unlink()
    return out_path


# ── Main pipeline ────────────────────────────────────────
async def generate_explanation(title: str, material: str, job_id: str, grounding_context: str = "") -> dict:
    print(f"[{job_id}] Quick Explain: classifying blocks...", flush=True)
    blocks = classify_blocks(material)
    if not blocks:
        print(f"[{job_id}] classify produced 0 blocks — falling back to whole-notes block", flush=True)
        blocks = [{"type": "concept", "title": title or "Notes", "content": material[:2000]}]
    n_concept = sum(1 for b in blocks if b["type"] == "concept")
    print(f"[{job_id}]   -> {len(blocks)} blocks ({n_concept} concept / {len(blocks)-n_concept} solved)", flush=True)

    print(f"[{job_id}] Generating single-voice script...", flush=True)
    segments = generate_explain_script(blocks, grounding_context=grounding_context)
    total_words = sum(len(s["text"].split()) for s in segments)
    print(f"[{job_id}]   -> {len(segments)} segments, ~{total_words} words (~{round(total_words/150, 1)} min)", flush=True)

    print(f"[{job_id}] Rendering audio ({EXPLAINER_VOICE})...", flush=True)
    audio_path = await _render_audio(segments, job_id)
    print(f"[{job_id}] Done -> {audio_path}", flush=True)

    return {
        "job_id": job_id,
        "segments": segments,
        "audio_path": str(audio_path),
        "segment_count": len(segments),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        from podcastengine import extract_from_pdf
        title, material = extract_from_pdf(sys.argv[1])
    else:
        title = "Paging"
        material = "Paging splits memory into fixed pages and frames. Solved: 16-bit address, 2KB page -> offset 11 bits, page number 5 bits, 32 entries."
    result = asyncio.run(generate_explanation(title, material, job_id="test"))
    print(f"\n-- {result['segment_count']} segments -> {result['audio_path']} --")