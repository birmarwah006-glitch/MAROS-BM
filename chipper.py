# chipper.py
import os
import json
import time
import subprocess
import requests
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
# HELPER — MM:SS → seconds
# ─────────────────────────────────────────────

def t_to_s(t: str) -> float:
    try:
        parts = str(t).strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except Exception:
        return 0.0


# ─────────────────────────────────────────────
# LLM — Cerebras primary, Groq fallback
# ─────────────────────────────────────────────

CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_MODEL   = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")

def _chipper_llm(prompt: str, temperature: float = 0.3, json_mode: bool = False) -> str:
    """Cerebras primary (1M TPD free tier), Groq fallback. OpenAI-compatible."""
    messages = [{"role": "user", "content": prompt}]

    if CEREBRAS_API_KEY:
        try:
            body = {
                "model": CEREBRAS_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 3000,
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            res = requests.post(
                "https://api.cerebras.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}",
                         "Content-Type": "application/json"},
                json=body, timeout=60,
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[MAROS] Cerebras failed in chipper ({e}), falling back to Groq")

    body = {
        "model": GROQ_CHAT_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    res = requests.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers=GROQ_HEADERS, json=body, timeout=60,
    )
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]


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
        mm = int(seg.start // 60)
        ss = int(seg.start % 60)
        full_transcript += f"[{mm:02d}:{ss:02d}] {seg.text.strip()}\n"

    if not raw_segments:
        raise RuntimeError("faster-whisper returned no segments — check that the video has audio.")

    _t_done = time.time()
    word_count = len(full_transcript.split())
    print(f"[MAROS] ⏱  Transcription: {_t_done - _t_model:.1f}s | {word_count} words | lang: {info.language}")

    if word_count < 50:
        print("[MAROS] WARNING — transcript is very short. Check video audio.")

    jobs.update_job(job_id, progress=30)
    return full_transcript, raw_segments


# ─────────────────────────────────────────────
# STEP 2 — SEGMENT
# ─────────────────────────────────────────────

def segment(transcript: str, job_id: str) -> list[dict]:
    """Ask the LLM to identify concept modules. Returns list of clip dicts."""
    jobs.update_job(job_id, status=JobStatus.segmenting, progress=40)

    context = transcript[:TRANSCRIPT_CAP]

    prompt = f"""
You are an expert academic assistant segmenting lecture videos into logical, concept-based modules for university students.

RULES:
- Identify the major concepts taught and group the lecture into modules accordingly.
- Each module must be AT LEAST 5 minutes long. If a concept is shorter, merge it with the closest related concept — do NOT create a module under 5 minutes.
- Create a new module ONLY when the professor shifts to a genuinely new, distinct concept that cannot be grouped with what came before.
- A typical 30-45 min lecture should produce 3-5 modules. A 60-90 min lecture may produce 5-8. Never produce more than 8 modules regardless of lecture length.
- Clips must cover the entire lecture from the point the professor begins the main content until the end.
- Skip all introductions, greetings, admin talk, or off-topic chatter at the beginning or end.
- Ensure clips do not overlap and are contiguous — the end of one clip is the start of the next.
- Give each module a clear, descriptive concept name that reflects ALL sub-topics covered in that segment.

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

    print(f"[MAROS] Segmentation done — {len(clips)} modules identified.")
    jobs.update_job(job_id, progress=55)
    return clips


# ─────────────────────────────────────────────
# STEP 3 — SUMMARIZE
# ─────────────────────────────────────────────

def summarize(concept: str, transcript_segment: str) -> str:
    """Generate student notes for a concept clip."""
    prompt = f"""
You are a study notes generator for university students.
Given the transcript of a lecture segment about "{concept}", write clear concise notes.

FORMAT:
- 2-3 sentence overview
- 3-5 bullet points of key takeaways
- Important terms or definitions if present

Keep it student-friendly. No filler, no repetition.

TRANSCRIPT:
{transcript_segment}
"""
    return _chipper_llm(prompt, temperature=0.3).strip()


def summarize_with_retry(concept: str, transcript_segment: str, module_num: int) -> str:
    """One retry with backoff so a single blip doesn't ship '[Summary generation failed]'."""
    try:
        return summarize(concept, transcript_segment)
    except Exception as e:
        print(f"[MAROS] Summary attempt 1 failed for module {module_num}: {e} — retrying in 3s")
        time.sleep(3)
        try:
            return summarize(concept, transcript_segment)
        except Exception as e2:
            print(f"[MAROS] Summary failed for module {module_num}: {e2}")
            return "[Summary generation failed]"


# ─────────────────────────────────────────────
# STEP 4 — CUT CLIPS
# ─────────────────────────────────────────────

def cut_clips(
    video_path : Path,
    clips      : list[dict],
    segments   : list,
    job_id     : str
) -> list[Module]:
    """Cut video into concept clips, generate notes, return Module list."""
    jobs.update_job(job_id, status=JobStatus.cutting, progress=60)

    job_output_dir = OUTPUTS_DIR / job_id
    try:
        job_output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise RuntimeError(f"Could not create output directory: {e}")

    modules     = []
    success     = 0
    total_clips = len(clips)

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
            if start_sec <= seg["start"] <= end_sec
        )

        # Generate notes (with one retry)
        jobs.update_job(
            job_id,
            status=JobStatus.summarizing,
            progress=60 + int((i / total_clips) * 35)
        )
        print(f"[MAROS] Summarizing module {i+1}: {concept}...")
        notes = summarize_with_retry(concept, clip_transcript, i + 1)

        # Save notes to disk
        notes_file = job_output_dir / f"Module_{i+1:02d}_{clean}_notes.txt"
        notes_file.write_text(f"# {concept}\n\n{notes}")

        modules.append(Module(
            module_id    = i + 1,
            concept      = concept,
            start        = clip["start"],
            end          = clip["end"],
            duration_sec = round(duration, 1),
            video_url    = f"/modules/{job_id}/{i+1}/video",
            notes        = notes,
            transcript   = clip_transcript
        ))

        success += 1
        print(f"[MAROS] Module {i+1} done: {concept}")

    print(f"[MAROS] Cutting done — {success}/{total_clips} modules created.")
    return modules


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────

def run_pipeline(video_path: Path, job_id: str) -> Manifest:
    """Full Chipper pipeline. Called as a background task by main.py."""
    _p0 = time.time()
    try:
        transcript, segments = transcribe(video_path, job_id)
        _p1 = time.time()

        clips                = segment(transcript, job_id)
        _p2 = time.time()
        print(f"[MAROS] ⏱  Segmentation: {_p2 - _p1:.1f}s")

        modules              = cut_clips(video_path, clips, segments, job_id)
        _p3 = time.time()
        print(f"[MAROS] ⏱  Clip cutting: {_p3 - _p2:.1f}s")
        print(f"[MAROS] ⏱  TOTAL PIPELINE: {_p3 - _p0:.1f}s")

        if not modules:
            raise RuntimeError("No modules were successfully created.")

        manifest = Manifest(
            job_id        = job_id,
            video_source  = video_path.name,
            total_modules = len(modules),
            modules       = modules,
            generated_at  = datetime.utcnow()
        )

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
        raise