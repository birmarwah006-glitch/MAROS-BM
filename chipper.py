# chipper.py
import os
import json
import math
import re
import time
import threading
import subprocess
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from faster_whisper import WhisperModel
from datetime import datetime
from pathlib import Path

from config import (
    GROQ_HEADERS,
    GROQ_BASE_URL,
    GROQ_CHAT_MODEL,
    WHISPER_MODEL,
    MAX_MODULES,
    MIN_CLIP_DURATION,
    TRANSCRIPT_CAP,
    OUTPUTS_DIR
)
from models import Module, Manifest
import jobs
from models import JobStatus

# ─────────────────────────────────────────────
# HELPERS — timestamp conversion
# ─────────────────────────────────────────────

def t_to_s(t: str) -> float:
    """Parse 'SS', 'MM:SS' (MM may exceed 59, e.g. '75:30'),
    or 'HH:MM:SS' into seconds."""
    try:
        parts = [float(p) for p in str(t).strip().split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0]
    except Exception:
        return 0.0


def s_to_t(sec: float) -> str:
    """Seconds → 'MM:SS' where MM can exceed 59 (matches transcript style)."""
    mm = int(sec // 60)
    ss = int(sec % 60)
    return f"{mm:02d}:{ss:02d}"


# ─────────────────────────────────────────────
# LLM — Cerebras primary, Groq fallback
# ─────────────────────────────────────────────

CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_MODEL   = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")

# ── Concurrency caps ─────────────────────────────────────────────────────────
# The pipeline fires parallel LLM calls from TWO places at once:
#   1. _segment_windowed  → up to MAX_WINDOW_WORKERS windows simultaneously
#   2. cut_clips          → notes + concept map simultaneously, per module
# Both providers appear to cap concurrent connections far tighter than their
# advertised token quotas, so those parallel threads were colliding and 429ing
# each other on nearly every call (Cerebras 429 → Groq fallback → Groq 429).
# These semaphores make the threads QUEUE on the actual HTTP request instead of
# firing simultaneously. Tune the numbers against your real dashboard limits —
# 1 and 2 are conservative starting points, not confirmed ceilings.
CEREBRAS_SEM = threading.Semaphore(1)
GROQ_SEM     = threading.Semaphore(2)


def _chipper_llm(prompt: str, temperature: float = 0.3, json_mode: bool = False) -> str:
    """Cerebras primary (1M TPD free tier), Groq fallback. OpenAI-compatible.

    Each provider gets its own short retry loop for TRANSIENT errors
    (timeouts, connection resets, 429/5xx) before giving up on it — a
    single blip on Cerebras used to fall straight through to Groq, and a
    blip on Groq used to raise immediately and take down whichever call
    was in flight (this is what silently killed a notes call while its
    sibling concept-map call succeeded). Now both providers get 2 tries
    with short backoff before we move on.

    The actual requests.post calls are wrapped in per-provider semaphores so
    concurrent callers queue instead of tripping the concurrent-connection cap.
    Note the semaphore wraps ONLY the request — raise_for_status and JSON
    parsing happen outside it, so a slow parse never holds the slot."""
    messages = [{"role": "user", "content": prompt}]

    def _is_transient(exc: Exception) -> bool:
        if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            return True
        if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
            return exc.response.status_code in (429, 500, 502, 503, 504)
        return False

    if CEREBRAS_API_KEY:
        body = {
            "model": CEREBRAS_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 3000,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        for attempt in range(2):
            try:
                with CEREBRAS_SEM:
                    res = requests.post(
                        "https://api.cerebras.ai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}",
                                 "Content-Type": "application/json"},
                        json=body, timeout=120,
                    )
                res.raise_for_status()
                return res.json()["choices"][0]["message"]["content"]
            except Exception as e:
                if attempt == 0 and _is_transient(e):
                    print(f"[MAROS] Cerebras transient error ({e}) — retrying once before Groq fallback")
                    time.sleep(2)
                    continue
                print(f"[MAROS] Cerebras failed in chipper ({e}), falling back to Groq")
                break

    body = {
        "model": GROQ_CHAT_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for attempt in range(2):
        try:
            with GROQ_SEM:
                res = requests.post(
                    f"{GROQ_BASE_URL}/chat/completions",
                    headers=GROQ_HEADERS, json=body, timeout=120,
                )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == 0 and _is_transient(e):
                print(f"[MAROS] Groq transient error ({e}) — retrying once")
                time.sleep(2)
                continue
            raise


def _strip_json_fences(raw: str) -> str:
    """gpt-oss-120b occasionally wraps JSON in ``` even in json_mode."""
    return raw.replace("```json", "").replace("```", "").strip()


# ─────────────────────────────────────────────
# STEP 1 — TRANSCRIBE
# ─────────────────────────────────────────────

_whisper_model = None

def _get_whisper():
    """Load faster-whisper once and cache it (avoids reloading per video).

    Env vars:
      WHISPER_MODEL_SIZE  base (default, M4 sweet spot) | small | medium | large-v3
      WHISPER_COMPUTE     int8 (default, CPU) | float16 (GPU)
      WHISPER_DEVICE      cpu (default) | cuda   ← set cuda on the VNIT server
    """
    global _whisper_model
    if _whisper_model is None:
        size    = os.getenv("WHISPER_MODEL_SIZE", "tiny")   # tiny on Mac dev, override to small/medium on VNIT
        compute = os.getenv("WHISPER_COMPUTE", "int8")
        device  = os.getenv("WHISPER_DEVICE", "cpu")
        print(f"[MAROS] Loading faster-whisper ({size}, {compute}, {device})...")
        _whisper_model = WhisperModel(size, device=device, compute_type=compute)
    return _whisper_model


def transcribe(video_path: Path, job_id: str) -> tuple[str, list]:
    """Transcribe video using faster-whisper. Returns (full_transcript, segments)."""
    _t0 = time.time()
    jobs.update_job(job_id, status=JobStatus.transcribing, progress=10)

    model = _get_whisper()
    _t_model = time.time()
    print(f"[MAROS] Model ready in {_t_model - _t0:.1f}s")

    # WHISPER_LANG=en pins language and skips the detection pass.
    # Set WHISPER_LANG= (empty) for Hinglish / mixed-language lectures.
    lang = os.getenv("WHISPER_LANG", "en") or None

    print(f"[MAROS] Transcribing {video_path.name}...")
    try:
        seg_iter, info = model.transcribe(
            str(video_path),
            beam_size=1,                       # greedy — 2-3x faster than beam_size=5,
                                               # negligible quality loss for LLM consumption
            language=lang,                     # skip language-detection pass
            condition_on_previous_text=False,  # faster + prevents repetition loops
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
    except Exception as e:
        raise RuntimeError(f"faster-whisper transcription failed: {e}")

    # NOTE: transcription actually happens lazily inside this loop —
    # model.transcribe() returns instantly, the iterator does the work.
    raw_segments = []
    full_transcript = ""
    for seg in seg_iter:
        raw_segments.append({"start": seg.start, "end": seg.end, "text": seg.text})
        full_transcript += f"[{s_to_t(seg.start)}] {seg.text.strip()}\n"

    if not raw_segments:
        raise RuntimeError("faster-whisper returned no segments — check that the video has audio.")

    _t_done = time.time()
    word_count = len(full_transcript.split())
    total_sec  = raw_segments[-1]["end"]
    print(f"[MAROS] ⏱  Transcription: {_t_done - _t_model:.1f}s | {word_count} words | "
          f"lecture length: {s_to_t(total_sec)} | lang: {info.language}")

    if word_count < 50:
        print("[MAROS] WARNING — transcript is very short. Check video audio.")

    jobs.update_job(job_id, progress=30)
    return full_transcript, raw_segments


# ─────────────────────────────────────────────
# STEP 2 — SEGMENT
#   Short lectures: single call, unchanged from before.
#   Long lectures (> WINDOW_THRESHOLD_SEC): split into overlapping ~20-min
#   windows, segmented in parallel, then merged by ownership of each
#   window's non-overlap "core" range so overlap context never produces
#   duplicate modules. This exists because a single call on a 100+ min
#   transcript can blow past provider payload limits (413) even after
#   Cerebras → Groq fallback.
# ─────────────────────────────────────────────

WINDOW_THRESHOLD_SEC   = 40 * 60   # only window lectures longer than this
WINDOW_SIZE_SEC        = 20 * 60   # ~20 min "core" per window
WINDOW_OVERLAP_SEC     = 150       # 2.5 min of shared context on each side, for boundary accuracy
MAX_MODULES_PER_WINDOW = 4         # 20 min window, 5-min-minimum modules → at most ~4
MAX_WINDOW_WORKERS     = 6         # cap concurrent segmentation threads (actual HTTP
                                   # concurrency is governed by CEREBRAS_SEM / GROQ_SEM)


def _fit_transcript(transcript: str) -> str:
    """Return a version of the transcript that fits under TRANSCRIPT_CAP chars
    WITHOUT truncating the tail. If too long, downsample lines (keep every 2nd)
    so timestamps still span the FULL lecture — the LLM needs topic
    transitions, not every sentence."""
    if len(transcript) <= TRANSCRIPT_CAP:
        return transcript

    lines = transcript.split("\n")
    passes = 0
    while len("\n".join(lines)) > TRANSCRIPT_CAP and len(lines) > 50:
        lines = lines[::2]
        passes += 1

    downsampled = "\n".join(lines)
    print(f"[MAROS] Transcript downsampled x{2**passes} "
          f"({len(transcript)} → {len(downsampled)} chars) — full time range preserved.")
    return downsampled


def _build_window_transcript(raw_segments: list, q_start: float, q_end: float) -> str:
    """Slice raw_segments into a [MM:SS] transcript for one query window.
    Timestamps stay GLOBAL (not reset to 0) so merged clips line up with the
    rest of the pipeline (cut_clips, notes, etc.) without any translation."""
    lines = [
        f"[{s_to_t(seg['start'])}] {seg['text'].strip()}\n"
        for seg in raw_segments
        if q_start <= seg["start"] < q_end
    ]
    return "".join(lines)


def _segment_window(q_start: float, q_end: float, raw_segments: list) -> list[dict]:
    """Segment one ~20-25 min window (core + overlap) of a long lecture.
    Returns raw (unvalidated) clip dicts in the same MM:SS global-timestamp
    format the single-call path already produces."""
    window_transcript = _build_window_transcript(raw_segments, q_start, q_end)
    if not window_transcript.strip():
        return []

    window_transcript = _fit_transcript(window_transcript)  # same safety net as the full path

    prompt = f"""
You are an expert academic assistant segmenting ONE SECTION of a longer lecture video into logical, concept-based modules.

This section runs from {s_to_t(q_start)} to {s_to_t(q_end)} (MM:SS) — it is NOT the whole lecture, just this time range. Only identify boundaries for topic changes that happen within this range.

RULES:
- Identify the major concepts taught in THIS SECTION and group it into modules accordingly.
- Place module boundaries exactly where the professor explicitly transitions to a new topic (phrases like "next, let's look at...", "now we'll move on to...", section announcements, etc.).
- Each module must be AT LEAST 5 minutes long. If a concept is shorter, merge it with the closest related concept in this section.
- Never produce more than {MAX_MODULES_PER_WINDOW} modules for this section.
- Modules must cover this section contiguously from {s_to_t(q_start)} to {s_to_t(q_end)} — do not leave gaps, and do not invent content outside this range.
- Give each module a clear, descriptive concept name that reflects ALL sub-topics it covers.

TIMESTAMP FORMAT: "MM:SS" where MM may exceed 59 for content past the hour mark (e.g. "75:30" = 1h15m30s). This matches the [MM:SS] markers in the transcript. Do NOT use HH:MM:SS.

REQUIRED OUTPUT FORMAT — JSON ONLY, no explanation, no markdown:
{{
  "clips": [
    {{"concept": "Descriptive concept name", "start": "MM:SS", "end": "MM:SS"}}
  ]
}}

TRANSCRIPT SECTION:
{window_transcript}
"""

    raw = _chipper_llm(prompt, temperature=0.1, json_mode=True)
    parsed = json.loads(_strip_json_fences(raw))
    clips = parsed["clips"]
    return clips if isinstance(clips, list) else []


def _segment_window_with_retry(q_start: float, q_end: float, raw_segments: list, window_num: int) -> list[dict]:
    """One retry with backoff, same pattern as summarize_with_retry below —
    a single window failing shouldn't take down the whole segmentation pass.
    Degrades to an empty window (its neighbors' overlap context usually
    covers most of the gap) rather than raising."""
    try:
        return _segment_window(q_start, q_end, raw_segments)
    except Exception as e:
        print(f"[MAROS] Window {window_num} segmentation attempt 1 failed ({e}) — retrying in 3s")
        time.sleep(3)
        try:
            return _segment_window(q_start, q_end, raw_segments)
        except Exception as e2:
            print(f"[MAROS] Window {window_num} segmentation failed, skipping this window: {e2}")
            return []


def _segment_windowed(raw_segments: list, total_sec: float, job_id: str) -> list[dict]:
    """Split a long lecture into ~20-min core windows (queried with overlap
    for boundary context), segment each window IN PARALLEL, then merge.

    Merge rule: each window only "owns" clips whose start falls in its own
    non-overlap core range. The overlap exists purely so the LLM sees a bit
    of context before/after its core range — it never contributes duplicate
    modules, because ownership is decided by fixed, non-overlapping core
    boundaries computed up front, not by anything the LLM says.
    """
    num_windows = math.ceil(total_sec / WINDOW_SIZE_SEC)
    print(f"[MAROS] Lecture is {s_to_t(total_sec)} — splitting segmentation into "
          f"{num_windows} windows of ~{WINDOW_SIZE_SEC // 60} min (parallel, "
          f"{WINDOW_OVERLAP_SEC}s overlap for context).")

    windows = []
    for i in range(num_windows):
        core_start = i * WINDOW_SIZE_SEC
        core_end   = min((i + 1) * WINDOW_SIZE_SEC, total_sec)
        q_start    = max(0, core_start - WINDOW_OVERLAP_SEC)
        q_end      = min(total_sec, core_end + WINDOW_OVERLAP_SEC)
        windows.append({
            "i": i, "core_start": core_start, "core_end": core_end,
            "q_start": q_start, "q_end": q_end,
        })

    results = {}
    with ThreadPoolExecutor(max_workers=min(MAX_WINDOW_WORKERS, num_windows)) as pool:
        futures = {
            pool.submit(_segment_window_with_retry, w["q_start"], w["q_end"], raw_segments, w["i"] + 1): w
            for w in windows
        }
        for future in as_completed(futures):
            w = futures[future]
            try:
                results[w["i"]] = future.result()
            except Exception as e:
                print(f"[MAROS] Window {w['i'] + 1} raised unexpectedly: {e}")
                results[w["i"]] = []

    merged = []
    for w in windows:
        is_last = w["i"] == num_windows - 1
        for c in results.get(w["i"], []):
            if not isinstance(c, dict) or not all(k in c for k in ("concept", "start", "end")):
                continue
            start_sec = t_to_s(c["start"])
            owned = w["core_start"] <= start_sec < w["core_end"]
            if owned or (is_last and start_sec >= w["core_start"]):
                merged.append(c)

    merged.sort(key=lambda c: t_to_s(c["start"]))
    print(f"[MAROS] Windowed segmentation merged {len(merged)} raw modules from {num_windows} windows.")
    return merged


def segment(transcript: str, raw_segments: list, job_id: str) -> list[dict]:
    """Ask the LLM to identify concept modules across the ENTIRE lecture.
    Returns list of clip dicts."""
    jobs.update_job(job_id, status=JobStatus.segmenting, progress=40)

    total_sec = raw_segments[-1]["end"]
    total_ts  = s_to_t(total_sec)

    if total_sec > WINDOW_THRESHOLD_SEC:
        try:
            clips = _segment_windowed(raw_segments, total_sec, job_id)
        except Exception as e:
            raise RuntimeError(f"LLM error during windowed segmentation: {e}")
    else:
        context = _fit_transcript(transcript)

        prompt = f"""
You are an expert academic assistant segmenting lecture videos into logical, concept-based modules for university students — the same way the lecture's creator would define chapters.

LECTURE LENGTH: {total_ts} (MM:SS). Your modules MUST cover the lecture all the way to the end. The last module's "end" must be at or very near {total_ts}.

RULES:
- Identify the major concepts taught and group the lecture into modules accordingly.
- Place module boundaries exactly where the professor explicitly transitions to a new topic (phrases like "next, let's look at...", "now we'll move on to...", section announcements, etc.). These natural transitions are the ground truth for boundaries.
- Each module must be AT LEAST 5 minutes long. If a concept is shorter, merge it with the closest related concept — do NOT create a module under 5 minutes.
- Create a new module ONLY when the professor shifts to a genuinely new, distinct concept that cannot be grouped with what came before.
- A typical 30-45 min lecture should produce 3-5 modules. A 60-90 min lecture may produce 5-8. Never produce more than {MAX_MODULES} modules regardless of lecture length.
- Modules must cover the entire lecture from the point the professor begins the main content until the very end at {total_ts}. Do NOT stop early.
- Skip introductions, greetings, admin talk, or off-topic chatter at the very beginning or very end only.
- Ensure modules do not overlap and are contiguous — the end of one module is the start of the next.
- Give each module a clear, descriptive concept name that reflects ALL sub-topics covered in that segment.

TIMESTAMP FORMAT: "MM:SS" where MM may exceed 59 for content past the hour mark (e.g. "75:30" = 1h15m30s). This matches the [MM:SS] markers in the transcript. Do NOT use HH:MM:SS.

REQUIRED OUTPUT FORMAT — JSON ONLY, no explanation, no markdown:
{{
  "clips": [
    {{"concept": "Descriptive concept name", "start": "MM:SS", "end": "MM:SS"}}
  ]
}}

TRANSCRIPT:
{context}
"""

        try:
            raw = _chipper_llm(prompt, temperature=0.1, json_mode=True)
        except Exception as e:
            raise RuntimeError(f"LLM error during segmentation: {e}")

        try:
            parsed = json.loads(_strip_json_fences(raw))
            clips  = parsed["clips"]
        except (KeyError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Failed to parse segmentation response: {e}")

    if not isinstance(clips, list) or len(clips) == 0:
        raise RuntimeError("LLM returned no clips in segmentation.")

    clips = _validate_clips(clips, total_sec)

    print(f"[MAROS] Segmentation done — {len(clips)} modules identified, "
          f"coverage: {clips[0]['start']} → {clips[-1]['end']} (lecture ends {total_ts}).")
    jobs.update_job(job_id, progress=55)
    return clips


def _validate_clips(clips: list[dict], total_sec: float) -> list[dict]:
    """Sanity-pass over LLM output:
    - drop malformed / zero-length clips
    - sort by start time
    - stitch small gaps so clips stay contiguous
    - clamp the final clip to the true lecture end (and extend it there if
      the LLM stopped early, so we never orphan the tail of the lecture)."""
    cleaned = []
    for c in clips:
        if not isinstance(c, dict) or not all(k in c for k in ("concept", "start", "end")):
            print(f"[MAROS] Dropping malformed clip: {c}")
            continue
        s, e = t_to_s(c["start"]), t_to_s(c["end"])
        if e <= s:
            print(f"[MAROS] Dropping clip with end <= start: {c}")
            continue
        cleaned.append({"concept": c["concept"], "_s": s, "_e": e})

    if not cleaned:
        raise RuntimeError("All clips were malformed after validation.")

    cleaned.sort(key=lambda c: c["_s"])

    # Make contiguous: each clip's end snaps to the next clip's start.
    for i in range(len(cleaned) - 1):
        cleaned[i]["_e"] = cleaned[i + 1]["_s"]

    # Never orphan the tail — the last module runs to the true lecture end.
    last = cleaned[-1]
    if abs(last["_e"] - total_sec) > 2:
        print(f"[MAROS] Extending final module end {s_to_t(last['_e'])} → {s_to_t(total_sec)} "
              f"to cover full lecture.")
    last["_e"] = total_sec

    return [
        {"concept": c["concept"], "start": s_to_t(c["_s"]), "end": s_to_t(c["_e"])}
        for c in cleaned
    ]


# ─────────────────────────────────────────────
# STEP 3 — SUMMARIZE
# ─────────────────────────────────────────────

# Literal token the notes prompt emits so the concept map can be spliced
# INLINE (right after the Explanation section) instead of tail-appended.
CONCEPT_MAP_MARKER = "%%CONCEPT_MAP%%"

# Sentinel returned by summarize_with_retry when notes generation raised on
# BOTH attempts (real API/network failure, not a parsing bug). cut_clips
# checks for this to avoid pairing a perfectly-generated diagram with dead
# notes text, which looks like "the diagram replaced the notes" — it didn't;
# the notes call failed independently and the diagram call just kept going.
_NOTES_FAILED_SENTINEL = "[Notes generation failed — this module needs to be regenerated]"


def _generate_detailed_notes(concept: str, transcript_segment: str) -> str:
    """Plain-text detailed notes with GitHub-alert callout syntax.
    Deliberately NOT wrapped in JSON — asking a model to nest full markdown
    (headers, blockquotes, code fences with backticks) inside a JSON string
    is a known failure mode for smaller/faster models: one unescaped quote
    or backtick anywhere in a long document breaks the whole parse. Plain
    text output is the same pattern the original (reliable) summarize()
    and generate_concept_map() already use."""
    prompt = f"""
You are writing study notes for a university course, based on a lecture transcript segment about "{concept}".

Output the notes as plain markdown text — no JSON, no code fences wrapping the whole response, just the markdown document itself.

FORMAT:

## {concept}

**Why this matters**
1-2 sentences on what problem this concept solves or why it comes up.

**Explanation**
Walk through the concept step by step, the way it was actually taught in the transcript. Don't compress multi-step reasoning into one bullet — if the lecture builds an idea up in stages, keep the stages. Include any code, syntax, or examples exactly as discussed.

{CONCEPT_MAP_MARKER}

(The line above is a REQUIRED literal token: output `{CONCEPT_MAP_MARKER}` exactly, alone on its own line, immediately after the Explanation section. It is a placeholder that gets replaced with a concept diagram later in the pipeline. Do not wrap it in a code fence, do not explain it, do not write anything else on that line.)

**Worked example**
If the transcript includes a worked example, walk through it fully (input → process → output). If it doesn't, skip this section rather than inventing one.

CALLOUT SYNTAX — use GitHub-alert blockquote syntax for the semantic blocks below. Each callout is a blockquote where the FIRST line is `> [!TYPE]` and subsequent lines are the body, each prefixed with `> `. Blank line before and after each callout.

Use these callout types and no others:

> [!NOTE]
> Use for a formal definition of a term introduced in this segment.
> One definition per NOTE block. Multiple definitions → multiple blocks.

> [!TIP]
> Use for a worked example, mnemonic, or a helpful concrete pattern that appeared in the transcript.

> [!IMPORTANT]
> Use for a key insight the lecturer emphasised — something they said explicitly matters and students should remember.

> [!WARNING]
> Use for a common mistake or misconception the lecturer corrected or warned about. Only if the transcript actually contains one.

> [!CAUTION]
> Use ONLY for a serious "don't do this" — real correctness or safety pitfalls, not just style advice.

CODE BLOCKS — for code, syntax, or command examples, use standard fenced code blocks with the language tag (```python, ```c, ```bash, etc.).

RULES:
- Ground everything in the transcript. Don't add outside knowledge or invent examples not present in the segment.
- If a callout type has nothing to say, omit it rather than padding it.
- Do not compress technical depth for brevity. Length follows content, not a word-count target.
- Every callout must be a `> [!TYPE]` blockquote — never write "Definition:" as a plain bold label instead.

TRANSCRIPT:
{transcript_segment}
"""
    return _chipper_llm(prompt, temperature=0.3).strip()


def _generate_summary(concept: str, transcript_segment: str) -> str:
    """Short plain-text card blurb — separate, cheap call. No JSON, no
    markdown — just 2-3 sentences, so there's nothing fragile to parse."""
    prompt = f"""
Write a 2-3 sentence plain-text summary of what this lecture segment about "{concept}" covers. No markdown, no bullets, no headers — just plain sentences a student would read on a card before deciding to open the full notes.

TRANSCRIPT:
{transcript_segment}
"""
    raw = _chipper_llm(prompt, temperature=0.3).strip()
    # Strip stray quotes/markdown a model sometimes adds even when told not to.
    raw = raw.strip('"').strip()
    raw = raw.replace("**", "").replace("##", "").strip()
    return raw


def summarize(concept: str, transcript_segment: str) -> dict:
    """Generate two-tier notes via two INDEPENDENT plain-text calls:
      - notes:   full markdown with callouts (behind the Notes button)
      - summary: short card blurb (shown by default)
    Independent calls mean a failure in one doesn't take down the other —
    unlike the earlier single-JSON-call version, which could lose BOTH if
    the JSON parse failed on a long escaped markdown string.

    Returns dict: { 'summary': str, 'notes': str }
    """
    notes_body = _generate_detailed_notes(concept, transcript_segment)
    if not notes_body:
        notes_body = "[Notes generation returned empty]"

    summary_text = ""
    for attempt in range(2):
        try:
            summary_text = _generate_summary(concept, transcript_segment)
            break
        except Exception as e:
            print(f"[MAROS] Summary call attempt {attempt+1} failed ({e}){' — retrying in 3s' if attempt == 0 else ' — using fallback'}")
            if attempt == 0:
                time.sleep(3)

    if not summary_text:
        # Fallback: first non-heading, non-callout line of the notes body.
        plain = [
            ln for ln in notes_body.split("\n")
            if ln.strip() and not ln.startswith(("#", ">", "```", "-", "*"))
        ]
        summary_text = " ".join(plain[:3])[:400]

    return {"summary": summary_text, "notes": notes_body}


# ─────────────────────────────────────────────
# CONCEPT MAPS — multi-shape diagrams with lint + error-feedback retry
# ─────────────────────────────────────────────

_MERMAID_ALLOWED_HEADERS = (
    "flowchart TD", "flowchart LR", "graph TD", "graph LR",
    "mindmap", "timeline", "stateDiagram-v2", "sequenceDiagram", "erDiagram",
)


def _lint_mermaid(src: str) -> str | None:
    """Cheap server-side sanity check on LLM-generated Mermaid — catches the
    common breakers BEFORE the frontend ever sees them, so we can retry with
    the specific error fed back into the prompt. Returns an error message,
    or None if the source looks safe.

    This is intentionally a lint, not a parser — real parsing happens in
    mermaid.js client-side. We only reject things that reliably break it,
    plus a few shapes that PARSE fine but carry no information (a mindmap
    with zero nesting renders as a flat spoke diagram that explains nothing)."""
    if not src or not src.strip():
        return "Output was empty."

    stripped = src.strip()
    first_line = stripped.split("\n", 1)[0].strip()

    if "```" in stripped:
        return "Output contains a ``` code fence — output raw Mermaid only, no fences."

    if not any(first_line.startswith(h) for h in _MERMAID_ALLOWED_HEADERS):
        return (f"First line is '{first_line}' — it must start with one of: "
                f"{', '.join(_MERMAID_ALLOWED_HEADERS)}.")

    is_mindmap  = first_line.startswith("mindmap")
    is_timeline = first_line.startswith("timeline")
    is_state    = first_line.startswith("stateDiagram")
    is_sequence = first_line.startswith("sequenceDiagram")
    is_er       = first_line.startswith("erDiagram")

    if is_mindmap:
        if "-->" in stripped or "---" in stripped:
            return ("mindmap diagrams use INDENTATION for hierarchy, never arrows. "
                    "Remove all --> and --- edges, or switch to a flowchart.")

        body_lines = [l for l in stripped.split("\n")[1:] if l.strip()]
        if len(body_lines) < 3:
            return "mindmap has fewer than 2 nodes under the root — add the sub-ideas."

        # body_lines[0] is the root node; everything after it is branches/detail.
        # Checking indent variety across the ROOT line too would let a purely
        # flat map pass (root indent + one child indent = 2 distinct values),
        # which is exactly the useless 5-spoke shape we're trying to reject.
        branch_indents = [len(l) - len(l.lstrip()) for l in body_lines[1:]]
        if len(set(branch_indents)) < 2:
            return ("mindmap is completely flat — every node is a direct child of the root "
                    "with no sub-detail. Add at least one level of nested detail under each "
                    "branch, or switch to a different shape if this content is sequential, "
                    "chronological, or stateful.")

    elif is_timeline:
        if len([l for l in stripped.split("\n")[1:] if l.strip()]) < 3:
            return "timeline has fewer than 2 events — add more entries."
        if "-->" in stripped or "---" in stripped:
            return ("timeline uses 'period : event' syntax, not --> arrows. "
                    "Remove all arrow syntax.")
        if ":" not in stripped:
            return ("timeline has no 'period : event' entries — each event line must be "
                    "'<period> : <event text>'.")

    elif is_state:
        if "-->" not in stripped:
            return "stateDiagram-v2 has no transitions (-->) — add the state changes."
        if "[*]" not in stripped:
            return ("stateDiagram-v2 should include [*] to mark the start (and ideally the "
                    "end) state — add at least '[*] --> FirstState'.")

    elif is_sequence:
        if not any(tok in stripped for tok in ("->>", "-->>", "->", "-->")):
            return ("sequenceDiagram has no messages between actors — add interactions "
                    "in the form 'A->>B: message'.")
        if ":" not in stripped:
            return "sequenceDiagram messages need a label: use 'A->>B: message text'."

    elif is_er:
        if not any(tok in stripped for tok in ("||", "o{", "}o", "|{", "}|")):
            return ("erDiagram has no relationship notation between entities — use e.g. "
                    "'AUTHOR ||--o{ WORK : writes'.")

    else:
        # flowchart / graph
        # Unquoted parentheses inside [labels] are the #1 flowchart breaker:
        # A[foo (bar)] fails to parse. Require quoted labels instead.
        if re.search(r'\[[^\]"\n]*\(', stripped):
            return ('A node label contains unquoted parentheses, e.g. A[foo (bar)] — '
                    'this breaks Mermaid. Quote the label: A["foo (bar)"].')
        if stripped.count("[") != stripped.count("]"):
            return "Unbalanced square brackets in node definitions."
        # Malformed edge label: -->|label|> is invalid — the model sometimes
        # adds a stray > after the closing pipe, copying arrow syntax where
        # it doesn't belong. Correct form is -->|label| B (no second >).
        if re.search(r'-->\|[^|]*\|>', stripped) or re.search(r'---\|[^|]*\|>', stripped):
            return ('An edge label is malformed: found "-->|label|>" — the second ">" '
                    'is invalid. Correct syntax is A -->|label| B, with nothing after '
                    'the closing pipe except the target node.')
        if "-->" not in stripped and "---" not in stripped and "subgraph" not in stripped:
            return "Flowchart has no edges (-->/---) and no subgraphs — add the relationships."

    return None


def generate_concept_map(concept: str, transcript_segment: str, subject: str = "") -> str:
    """Generate a Mermaid diagram of this concept's internal structure.

    Supports SEVEN shapes (timeline, stateDiagram-v2, sequenceDiagram,
    erDiagram, mindmap, flowchart, subgraph-flowchart) and does one
    lint→feedback→retry pass: if the first output fails _lint_mermaid, the
    specific error is fed back into a second prompt so the retry actually
    corrects the mistake instead of re-rolling the same dice. Output is
    spliced INLINE into the notes at CONCEPT_MAP_MARKER by cut_clips.

    `subject` (e.g. "Biology", "Computer Science") comes from the professor's
    upload-time selection and is passed through ONLY to inform terminology and
    label phrasing. It must never influence shape choice — letting genre drive
    shape reintroduces exactly the stereotyping this prompt is built to avoid
    ("history lecture, therefore timeline"), which is also why the shape
    examples below deliberately pair each shape with an unrelated subject."""

    subject_line = f"\nCOURSE SUBJECT: {subject}\n" if subject else ""

    base_prompt = f"""
You are building a concept diagram for a university lecture segment about "{concept}".
{subject_line}
Analyze the transcript's STRUCTURE — not its subject — and pick the ONE diagram shape that fits. The same subject can require different shapes depending on what is actually said. A history lecture can be a mindmap if it covers unordered parallel themes. A biology lecture can be a timeline if it covers dated discoveries. Judge only by the structure of the content.

- `timeline` — content is CHRONOLOGICAL or DATED, regardless of subject.
  Example (technology):
  timeline
      title Evolution of CPUs
      1971 : First microprocessor released
      2001 : Multi-core CPUs appear

- `stateDiagram-v2` — one entity moves through DISCRETE NAMED STATES with transitions, regardless of subject.
  Example (law):
  stateDiagram-v2
      [*] --> Filed
      Filed --> UnderReview
      UnderReview --> Dismissed
      UnderReview --> Granted

- `sequenceDiagram` — MULTIPLE ACTORS interact with each other in a specific order, regardless of subject.
  Example (economics):
  sequenceDiagram
      Buyer->>Seller: Places order
      Seller->>Bank: Requests payment
      Bank->>Seller: Confirms funds

- `erDiagram` — ENTITIES and the RELATIONSHIPS between them, regardless of subject.
  Example (literature):
  erDiagram
      AUTHOR ||--o{{ WORK : writes
      WORK ||--o{{ EDITION : published_as

- `mindmap` — UNORDERED parallel sub-ideas around one central concept, with no causality or sequence, regardless of subject. Hierarchy is pure indentation; the root goes in double parentheses.
  Example (chemistry):
  mindmap
      root((Acids))
        Strong acids
          Fully dissociate
        Weak acids
          Partially dissociate

- `flowchart TD` (top-down) or `flowchart LR` (left-right) — a SEQUENCE, PROCESS, or CAUSAL CHAIN, regardless of subject. Use LR when the natural reading order is horizontal.
  Example (psychology):
  flowchart TD
      Stimulus --> Perception
      Perception --> Response

- `flowchart TD` with `subgraph` blocks — CONTAINMENT or LAYERS (things inside other things), regardless of subject.
  Example (business):
  flowchart TD
      subgraph Company
        subgraph Engineering
          Frontend
          Backend
        end
      end

CRITICAL: Base your choice ONLY on whether the content is chronological, stateful, interactive, relational, parallel-unordered, sequential-causal, or containment-based. Ignore what subject the lecture is about — the examples above deliberately pair each shape with a different subject so that no subject maps predictably to any shape.

Output ONLY valid Mermaid syntax, nothing else — no markdown fences, no commentary, no prose.

RULES:
- If the transcript centres on explicit dates or years, strongly prefer `timeline`.
- If the transcript describes something passing through named stages or states, prefer `stateDiagram-v2` over a plain flowchart.
- Use the COURSE SUBJECT above (when given) only to shape terminology and label wording — never to decide which diagram shape to use.
- Node and label text must be short (under 6 words) and taken from what was actually discussed, not invented.
- If a flowchart node label needs parentheses, commas, or special characters, put the label in double quotes: A["label (detail)"]. Never leave parentheses unquoted inside [].
- mindmap diagrams: hierarchy is INDENTATION ONLY — never use --> arrows in a mindmap, and never leave every node as a direct child of the root. Each branch needs at least one nested detail beneath it.
- Edge labels use this exact syntax: A -->|label text| B — the closing pipe is followed ONLY by the target node, nothing else. NEVER write A -->|label|> B (that trailing > is invalid and breaks the diagram).
- Only include an edge or relationship if the transcript actually implies it — don't force graph shape onto content that doesn't have it.
- 4-10 nodes / states / entities / events total. If the segment is too simple to map meaningfully, output a short 2-3 node version rather than padding it.
- No styling directives (no classDef, no style), no click handlers — just structure. The frontend styles the rendered diagram.

TRANSCRIPT:
{transcript_segment}
"""

    def _clean(raw: str) -> str:
        raw = raw.strip()
        raw = raw.removeprefix("```mermaid").removeprefix("```").removesuffix("```").strip()
        return raw

    src = _clean(_chipper_llm(base_prompt, temperature=0.2))

    err = _lint_mermaid(src)
    if err is None:
        return src

    print(f"[MAROS] Concept map lint failed ({err}) — retrying with error feedback")
    repair_prompt = f"""{base_prompt}

YOUR PREVIOUS ATTEMPT FAILED VALIDATION.

Previous output:
{src}

Validation error:
{err}

Fix exactly this problem and output the corrected diagram. If the error says the shape is a poor fit for the content, switch to a shape from the list above that actually fits. Output ONLY valid Mermaid syntax — no fences, no commentary.
"""
    src2 = _clean(_chipper_llm(repair_prompt, temperature=0.2))
    err2 = _lint_mermaid(src2)
    if err2 is None:
        return src2

    print(f"[MAROS] Concept map repair also failed lint ({err2}) — dropping diagram for this module")
    return ""


def summarize_with_retry(concept: str, transcript_segment: str, module_num: int) -> dict:
    """Up to 3 attempts with backoff — notes are the core deliverable, worth
    more retry budget than a single blip. Returns the same dict shape as
    summarize(): {'summary': str, 'notes': str}."""
    last_err = None
    for attempt in range(3):
        try:
            return summarize(concept, transcript_segment)
        except Exception as e:
            last_err = e
            wait = 2 * (attempt + 1)
            print(f"[MAROS] Notes attempt {attempt+1}/3 failed for module {module_num}: {e}"
                  f"{f' — retrying in {wait}s' if attempt < 2 else ''}")
            if attempt < 2:
                time.sleep(wait)
    print(f"[MAROS] Notes generation failed for module {module_num} after 3 attempts: {last_err}")
    return {
        "summary": "",
        "notes": _NOTES_FAILED_SENTINEL,
    }


def concept_map_with_retry(concept: str, transcript_segment: str, module_num: int,
                           subject: str = "") -> str:
    """Outer retry for transport-level failures (timeouts, 5xx) — the
    syntax-level lint retry lives INSIDE generate_concept_map. Falls back
    to empty string, never a broken diagram."""
    try:
        return generate_concept_map(concept, transcript_segment, subject)
    except Exception as e:
        print(f"[MAROS] Concept map attempt 1 failed for module {module_num}: {e} — retrying in 3s")
        time.sleep(3)
        try:
            return generate_concept_map(concept, transcript_segment, subject)
        except Exception as e2:
            print(f"[MAROS] Concept map failed for module {module_num}: {e2}")
            return ""


# ─────────────────────────────────────────────
# STEP 4 — CUT CLIPS
# ─────────────────────────────────────────────

def cut_clips(
    video_path : Path,
    clips      : list[dict],
    segments   : list,
    job_id     : str,
    subject    : str = ""
) -> tuple[list[Module], list[int]]:
    """Cut video into concept clips, generate notes, return (modules, module
    numbers whose concept map was dropped).

    The second return value exists because a module whose diagram failed still
    logs "Module N done" and still ships — previously the only trace was a
    single stdout line, so a run could quietly produce 5/12 modules with no
    concept map and nothing downstream would know."""
    jobs.update_job(job_id, status=JobStatus.cutting, progress=60)

    job_output_dir = OUTPUTS_DIR / job_id
    try:
        job_output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise RuntimeError(f"Could not create output directory: {e}")

    modules          = []
    success          = 0
    total_clips      = len(clips)
    missing_diagrams = []   # module numbers where concept map generation was dropped

    for i, clip in enumerate(clips):
        if not isinstance(clip, dict) or not all(k in clip for k in ("concept", "start", "end")):
            print(f"[MAROS] Skipping clip {i+1} — invalid structure: {clip}")
            continue

        concept   = clip["concept"]
        start_sec = t_to_s(clip["start"])
        end_sec   = t_to_s(clip["end"])
        duration  = end_sec - start_sec

        if end_sec <= start_sec:
            print(f"[MAROS] Skipping clip {i+1} — end <= start.")
            continue

        if duration < MIN_CLIP_DURATION:
            print(f"[MAROS] Skipping clip {i+1} — too short ({duration:.1f}s).")
            continue

        # Safe filename
        clean    = "".join(x for x in concept if x.isalnum() or x in " _-")[:40].strip()
        clean    = clean.replace(" ", "_")
        out_file = job_output_dir / f"Module_{i+1:02d}_{clean}.mp4"

        # Cut with FFmpeg
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-i", str(video_path),
            "-t", str(duration),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(out_file)
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)

        if proc.returncode != 0:
            print(f"[MAROS] FFmpeg failed on clip {i+1}:\n{proc.stderr[-300:]}")
            continue

        # Extract transcript for this clip
        clip_transcript = " ".join(
            seg["text"].strip()
            for seg in segments
            if start_sec <= seg["start"] < end_sec
        )

        jobs.update_job(
            job_id,
            status=JobStatus.summarizing,
            progress=60 + int((i / total_clips) * 35)
        )
        print(f"[MAROS] Generating notes + concept map for module {i+1}: {concept}...")

        # Notes and concept map are fully independent of each other — both
        # read the same transcript segment but neither depends on the
        # other's output. Run them concurrently so a slow/failing call on
        # one side never blocks or preempts the other, and both always get
        # their full retry budget regardless of what happens to its sibling.
        # The provider semaphores (CEREBRAS_SEM / GROQ_SEM) keep these two
        # threads from actually hitting the API at the same instant.
        with ThreadPoolExecutor(max_workers=2) as pool:
            notes_future = pool.submit(summarize_with_retry, concept, clip_transcript, i + 1)
            map_future   = pool.submit(concept_map_with_retry, concept, clip_transcript, i + 1, subject)
            result       = notes_future.result()
            diagram_src  = map_future.result()

        summary_text = result["summary"]
        notes_body = result["notes"]
        notes_ok = notes_body != _NOTES_FAILED_SENTINEL

        # Splice the diagram INLINE at the marker the notes prompt emitted
        # (right after Explanation), so it sits next to the concept it maps
        # instead of being buried at the bottom. Diagram generation always
        # runs above regardless of notes outcome — if notes failed, the
        # diagram is still appended on its own so nothing generated gets
        # thrown away, just clearly separated from the failure sentinel.
        if diagram_src:
            diagram_block = f"\n### Concept map\n\n```mermaid\n{diagram_src}\n```\n"
            if notes_ok and CONCEPT_MAP_MARKER in notes_body:
                notes_body = notes_body.replace(CONCEPT_MAP_MARKER, diagram_block, 1)
                notes_body = notes_body.replace(CONCEPT_MAP_MARKER, "")  # strip dupes
            else:
                notes_body = f"{notes_body}\n\n## Concept map\n\n```mermaid\n{diagram_src}\n```\n"
        else:
            notes_body = notes_body.replace(CONCEPT_MAP_MARKER, "")
            missing_diagrams.append(i + 1)

        # Save notes to disk
        notes_file = job_output_dir / f"Module_{i+1:02d}_{clean}_notes.txt"
        notes_file.write_text(f"# {concept}\n\n{notes_body}")

        # Build Module kwargs — include `summary` only if the Module model
        # actually has that field (avoids breaking older Module schemas
        # that pre-date the two-tier notes rollout).
        module_kwargs = dict(
            module_id    = i + 1,
            concept      = concept,
            start        = clip["start"],
            end          = clip["end"],
            duration_sec = round(duration, 1),
            video_url    = f"/modules/{job_id}/{i+1}/video",
            notes        = notes_body,
            transcript   = clip_transcript,
        )
        if "summary" in getattr(Module, "model_fields", getattr(Module, "__fields__", {})):
            module_kwargs["summary"] = summary_text
        modules.append(Module(**module_kwargs))

        success += 1
        print(f"[MAROS] Module {i+1} done: {concept}")

    if missing_diagrams:
        print(f"[MAROS] ⚠  Modules missing concept maps: {missing_diagrams} "
              f"({len(missing_diagrams)}/{success}) — these need regeneration.")
    print(f"[MAROS] Cutting done — {success}/{total_clips} modules created.")
    return modules, missing_diagrams


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────

def run_pipeline(video_path: Path, job_id: str, subject: str = "") -> Manifest:
    """Full Chipper pipeline. Called as a background task by main.py.

    `subject` (e.g. "Biology", "Computer Science") comes from the professor's
    upload-time course selection. It only affects concept-map label phrasing —
    never diagram shape selection. Passing "" is fine and simply omits the hint."""
    _p0 = time.time()
    try:
        transcript, segments = transcribe(video_path, job_id)
        _p1 = time.time()

        clips                = segment(transcript, segments, job_id)
        _p2 = time.time()
        print(f"[MAROS] ⏱  Segmentation: {_p2 - _p1:.1f}s")

        modules, missing_diagrams = cut_clips(video_path, clips, segments, job_id, subject)
        _p3 = time.time()
        print(f"[MAROS] ⏱  Clip cutting: {_p3 - _p2:.1f}s")
        print(f"[MAROS] ⏱  TOTAL PIPELINE: {_p3 - _p0:.1f}s")

        if not modules:
            raise RuntimeError("No modules were successfully created.")

        # Same defensive pattern as Module.summary above: only pass the newer
        # fields if the Manifest model actually declares them, so this file
        # doesn't hard-require a models.py change to run.
        manifest_fields = getattr(Manifest, "model_fields", getattr(Manifest, "__fields__", {}))
        manifest_kwargs = dict(
            job_id        = job_id,
            video_source  = video_path.name,
            total_modules = len(modules),
            modules       = modules,
            generated_at  = datetime.utcnow(),
        )
        if "subject" in manifest_fields:
            manifest_kwargs["subject"] = subject
        if "modules_missing_diagrams" in manifest_fields:
            manifest_kwargs["modules_missing_diagrams"] = missing_diagrams

        manifest = Manifest(**manifest_kwargs)

        # Save manifest to disk
        manifest_path = OUTPUTS_DIR / job_id / "manifest.json"
        try:
            manifest_path.write_text(manifest.model_dump_json(indent=2))
        except AttributeError:
            # Pydantic v1 fallback
            manifest_path.write_text(manifest.json(indent=2))

        jobs.complete_job(job_id)
        print(f"[MAROS] Pipeline complete — {len(modules)} modules ready.")
        return manifest

    except Exception as e:
        jobs.fail_job(job_id, str(e))
        print(f"[MAROS] Pipeline failed: {e}")