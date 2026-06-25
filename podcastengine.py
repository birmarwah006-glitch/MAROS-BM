"""
AdaptLearn Podcast Engine
Bir (curious learner) + Mia (expert)
Stack: Groq (script) + Edge TTS (audio) + pydub (stitch)

NOTE: simulation generation has been stripped out for the MAROS integration.
This module only produces the script (turns) + stitched audio file.
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional
import edge_tts
from pydub import AudioSegment
from groq import Groq

# ── Config ──────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "your_groq_key_here")

BIR_VOICE = "en-US-AndrewNeural"
MIA_VOICE = "en-US-JennyNeural"

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SEGMENTS = ["hook", "context", "core", "sowhat", "wrap"]

SEGMENT_INSTRUCTIONS = {
    "hook":    "Generate ONLY the Hook segment (0-2 min, 8-10 turns). Bir asks a relatable real-world question. Keep it engaging and simple.",
    "context": "Generate ONLY the Context segment (2-5 min, 10-12 turns). Mia explains what the paper is about simply. Bir asks clarifying questions.",
    "core":    "Generate ONLY the Core Concept segment (5-12 min, 18-22 turns). Deep dive — analogy first, define every jargon term, complexity ramps up.",
    "sowhat":  "Generate ONLY the So What segment (12-17 min, 12-15 turns). Real-world applications, why this research matters, future impact.",
    "wrap":    "Generate ONLY the Wrap Up segment (17-20 min, 8-10 turns). Bir summarizes the whole paper in his own words, Mia corrects and confirms.",
}

SYSTEM_PROMPT = """You are a podcast script writer for AdaptLearn, an AI-powered learning platform at VNIT Nagpur.

You write scripts for a 20-minute educational podcast with two hosts:
- BIR: curious male learner. Asks questions, pushes back, says "wait, I don't get that", summarizes to check understanding. Never lectures.
- MIA: expert female. Explains clearly. Always gives a real-world analogy BEFORE any technical explanation. Pauses to define every jargon term in one plain sentence immediately when it appears.

STRICT SCRIPT RULES:
1. Bir always speaks first in each segment — asks before Mia explains.
2. Every technical term: Mia defines it immediately in one plain sentence.
3. Every concept: real-world analogy FIRST, then technical explanation.
4. Complexity ramps up across segments — early = undergrad friendly, late = research level.
5. Max 3 sentences per speaking turn.
6. Bir pushes back at least once per segment: "wait, so [restatement]?"
7. Wrap-up: Bir summarizes the whole paper in his own words, Mia corrects/confirms.

OUTPUT: Return ONLY a valid JSON array. No markdown, no preamble, no explanation.
Format:
[
  {"segment": "Hook", "speaker": "Bir", "text": "..."},
  {"segment": "Hook", "speaker": "Mia", "text": "..."},
  ...
]"""


# ── PDF Extractor ────────────────────────────────────────
def extract_from_pdf(pdf_path: str) -> tuple[str, str]:
    import fitz
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc[:3]:
        text += page.get_text()
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # Anchor off "Abstract" — the title sits somewhere in the header chunk
    # before it. Titles are often wrapped across 2+ lines by the PDF layout,
    # so we find the longest RUN of consecutive "title-like" lines and join
    # them, rather than just picking a single longest line.
    text_lower_full = text.lower()
    abstract_pos = text_lower_full.find("abstract")

    header_text = text[:abstract_pos] if abstract_pos != -1 else text[:1500]
    header_lines = [l.strip() for l in header_text.split('\n') if l.strip()]

    def looks_like_author_line(line: str) -> bool:
        # e.g. "Rina Damdoo1 · Praveen Kumar2" — short names joined by a
        # middle-dot, often with trailing digit affiliation markers
        return '·' in line or bool(re.search(r'[a-zA-Z]\d\b', line))

    def looks_like_title_line(line: str) -> bool:
        if len(line) < 8:
            return False
        if re.search(r'\d{4}|https?://|doi\.org|@|\|', line):
            return False
        if re.match(r'^(research|review|article|vol\.?|©|received|accepted|published|open)\b', line.lower()):
            return False
        if looks_like_author_line(line):
            return False
        return True

    best_run, current_run = [], []
    for line in header_lines:
        if looks_like_title_line(line):
            current_run.append(line)
            if len(' '.join(current_run)) > len(' '.join(best_run)):
                best_run = current_run[:]
        else:
            current_run = []

    if best_run:
        title = ' '.join(best_run).replace('\xa0', ' ')
        title = re.sub(r'\s+', ' ', title).strip()
    else:
        title = lines[0] if lines else "Untitled"

    abstract = ""
    text_lower = text.lower()
    start = text_lower.find("abstract")
    end = text_lower.find("introduction", start)
    if start != -1 and end != -1:
        abstract = text[start+8:end].strip()
        # strip a leading "1" or similar section-number artifact some PDFs
        # leave behind right before "Introduction"
        abstract = re.sub(r'\s*\d{1,2}\s*$', '', abstract).strip()
    return title, abstract


# ── Groq Script Generator (per segment) ─────────────────
def generate_segment_script(
    paper_title: str,
    abstract: str,
    domain: str,
    segment: str,
) -> list[dict]:
    client = Groq(api_key=GROQ_API_KEY)
    instruction = SEGMENT_INSTRUCTIONS[segment]
    segment_label = segment.replace("sowhat", "So What").replace("wrap", "Wrap Up").title()

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"""Paper title: {paper_title}
Domain: {domain}
Abstract:
{abstract}

{instruction}
Use "{segment_label}" as the segment name in every turn.
Return ONLY the JSON array. No extra text."""}
        ],
        temperature=0.85,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"```json|```", "", raw).strip()
    return json.loads(raw)


# ── Edge TTS Audio Generator ─────────────────────────────
async def text_to_audio(text: str, voice: str, output_path: Path) -> None:
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output_path))


async def generate_all_audio(turns: list[dict], job_id: str) -> list[Path]:
    tasks, paths = [], []
    for i, turn in enumerate(turns):
        voice = BIR_VOICE if turn["speaker"] == "Bir" else MIA_VOICE
        path = OUTPUT_DIR / f"{job_id}_turn_{i:03d}.mp3"
        paths.append(path)
        tasks.append(text_to_audio(turn["text"], voice, path))
    await asyncio.gather(*tasks)
    return paths


# ── Audio Stitcher ───────────────────────────────────────
PAUSE_BETWEEN_TURNS = 600
PAUSE_BETWEEN_SEGMENTS = 1500

def stitch_audio(turns: list[dict], audio_paths: list[Path], job_id: str) -> Path:
    final = AudioSegment.empty()
    current_segment = None
    for turn, path in zip(turns, audio_paths):
        if not path.exists():
            print(f"  Warning: missing {path}")
            continue
        clip = AudioSegment.from_mp3(str(path))
        if turn["segment"] != current_segment:
            if current_segment is not None:
                final += AudioSegment.silent(duration=PAUSE_BETWEEN_SEGMENTS)
            current_segment = turn["segment"]
        else:
            final += AudioSegment.silent(duration=PAUSE_BETWEEN_TURNS)
        final += clip
    output_path = OUTPUT_DIR / f"{job_id}_podcast.mp3"
    final.export(str(output_path), format="mp3", bitrate="128k")
    for path in audio_paths:
        if path.exists():
            path.unlink()
    return output_path


# ── Main Pipeline (segment by segment) ──────────────────
async def generate_podcast(
    paper_title: str,
    abstract: str,
    domain: str = "general",
    job_id: Optional[str] = None,
) -> dict:
    """
    Generates the full podcast script + audio for a paper.
    `job_id` here is used purely as a filename prefix for the audio/turn
    output — in the MAROS integration this is set to the paper_id so
    outputs land in OUTPUTS_DIR / paper_id / podcast.json.
    """

    import uuid

    if not job_id:
        job_id = str(uuid.uuid4())[:8]

    # ── Generate Podcast Script ─────────────────────────
    all_turns = []

    for seg in SEGMENTS:
        print(f"[{job_id}] Generating segment: {seg}...")

        turns = generate_segment_script(
            paper_title,
            abstract,
            domain,
            seg,
        )

        print(f"[{job_id}]   → {len(turns)} turns")

        all_turns.extend(turns)

    # ── Generate Audio ──────────────────────────────────
    print(
        f"[{job_id}] Total turns: {len(all_turns)} — generating audio..."
    )

    audio_paths = await generate_all_audio(
        all_turns,
        job_id,
    )

    # ── Stitch Audio ────────────────────────────────────
    print(f"[{job_id}] Stitching final podcast...")

    final_path = stitch_audio(
        all_turns,
        audio_paths,
        job_id,
    )

    print(f"[{job_id}] Done → {final_path}")

    return {
        "job_id": job_id,
        "turns": all_turns,
        "audio_path": str(final_path),
        "turn_count": len(all_turns),
    }


# ── CLI ──────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
        domain = sys.argv[2] if len(sys.argv) > 2 else "general"
        print(f"Extracting from PDF: {pdf_path}")
        title, abstract = extract_from_pdf(pdf_path)
        print(f"Title: {title[:80]}...")
    else:
        title = "SignEdgeLVM transformer model for enhanced sign language translation"
        abstract = """Transformer architectures have accelerated research in Continuous Sign Language
        Recognition and Translation (CSLRT). We propose SignEdgeLVM using Global Relative Attention
        Matrix (GRAM) and Dynamic Point Frame Sampling (DPFS), reducing memory by 99.93 percent per
        attention head. Evaluated on PHOENIX14T dataset, achieving BLEU-4 of 22.55."""
        domain = "Deep Learning"

    result = asyncio.run(generate_podcast(
        paper_title=title,
        abstract=abstract,
        domain=domain,
    ))

    print(f"\n── {result['turn_count']} turns → {result['audio_path']} ──")
    print("Run: open", result['audio_path'])