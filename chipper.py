# chipper.py
import os
import json
import subprocess
import requests
import whisper
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
# STEP 1 — TRANSCRIBE
# ─────────────────────────────────────────────

def transcribe(video_path: Path, job_id: str) -> tuple[str, list]:
    """Transcribe video using local Whisper. Returns (full_transcript, segments)."""
    jobs.update_job(job_id, status=JobStatus.transcribing, progress=10)

    print(f"[MAROS] Loading Whisper ({WHISPER_MODEL})...")
    try:
        model = whisper.load_model(WHISPER_MODEL)
    except Exception as e:
        raise RuntimeError(f"Failed to load Whisper model '{WHISPER_MODEL}': {e}")

    print(f"[MAROS] Transcribing {video_path.name}...")
    try:
        result = model.transcribe(str(video_path))
    except Exception as e:
        raise RuntimeError(f"Whisper transcription failed: {e}")

    raw_segments = result.get("segments")
    if not raw_segments:
        raise RuntimeError("Whisper returned no segments — check that the video has audio.")

    full_transcript = ""
    for seg in raw_segments:
        mm  = int(seg["start"] // 60)
        ss  = int(seg["start"] % 60)
        full_transcript += f"[{mm:02d}:{ss:02d}] {seg['text'].strip()}\n"

    word_count = len(full_transcript.split())
    print(f"[MAROS] Transcription done — {word_count} words.")

    if word_count < 50:
        print("[MAROS] WARNING — transcript is very short. Check video audio.")

    jobs.update_job(job_id, progress=30)
    return full_transcript, raw_segments


# ─────────────────────────────────────────────
# STEP 2 — SEGMENT
# ─────────────────────────────────────────────

def segment(transcript: str, job_id: str) -> list[dict]:
    """Ask LLaMA to identify concept modules. Returns list of clip dicts."""
    jobs.update_job(job_id, status=JobStatus.segmenting, progress=40)

    context = transcript[:TRANSCRIPT_CAP]

    prompt = f"""
You are segmenting a university lecture video into broad concept-based modules for students.

- Return EXACTLY 3 to {MAX_MODULES} clips covering the ENTIRE lecture from start to finish.
- Each clip should be roughly 10-15 minutes long for a 50-minute lecture.
- The last clip's end time must be close to the end of the lecture.

- Each clip must cover ONE major concept or topic.
- Group closely related sub-topics into a single clip.
- Clips must NOT overlap.
- Skip any introduction, greetings, or admin talk at the start.
- Only make a new clip when the professor clearly shifts to a genuinely NEW major topic.

REQUIRED OUTPUT FORMAT — JSON ONLY, no explanation, no markdown:
{{
  "clips": [
    {{"concept": "Descriptive concept name", "start": "MM:SS", "end": "MM:SS"}}
  ]
}}

TRANSCRIPT:
{context}
"""

    payload = {
        "model": GROQ_CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    try:
        res = requests.post(
            f"{GROQ_BASE_URL}/chat/completions",
            headers=GROQ_HEADERS,
            json=payload,
            timeout=60
        )
        res.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Groq API HTTP error during segmentation: {e} — {res.text[:300]}")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network error during segmentation: {e}")

    try:
        raw    = res.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        clips  = parsed["clips"]
    except (KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Failed to parse segmentation response: {e}")

    if not isinstance(clips, list) or len(clips) == 0:
        raise RuntimeError("LLaMA returned no clips in segmentation.")

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

    payload = {
        "model": GROQ_CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }

    try:
        res = requests.post(
            f"{GROQ_BASE_URL}/chat/completions",
            headers=GROQ_HEADERS,
            json=payload,
            timeout=60
        )
        res.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Groq API HTTP error during summarization: {e}")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network error during summarization: {e}")

    return res.json()["choices"][0]["message"]["content"].strip()


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

        # Generate notes
        jobs.update_job(
            job_id,
            status=JobStatus.summarizing,
            progress=60 + int((i / total_clips) * 35)
        )
        print(f"[MAROS] Summarizing module {i+1}: {concept}...")
        try:
            notes = summarize(concept, clip_transcript)
        except Exception as e:
            print(f"[MAROS] Summary failed for module {i+1}: {e}")
            notes = "[Summary generation failed]"

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
    try:
        transcript, segments = transcribe(video_path, job_id)
        clips                = segment(transcript, job_id)
        modules              = cut_clips(video_path, clips, segments, job_id)

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