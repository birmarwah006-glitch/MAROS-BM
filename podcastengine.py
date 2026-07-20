"""
AdaptLearn / MAROS Podcast Engine v2
Bir (curious learner) + Mia (expert)
Stack: Cerebras primary + Groq fallback (script) + DuckDuckGo (enrichment)
       + Edge TTS (audio) + pydub (stitch)

Changes v2:
- Cerebras (gpt-oss-120b) primary, Groq (llama-3.3-70b) fallback — same as MAROS /chat
- JSON sanitizer (kills trailing-comma parse errors) + 1 auto-retry per segment
- Web enrichment: extracts key concepts/examples from the material,
  DDG-searches them, feeds context into script generation
- Prompt rework: technical for 2nd/3rd-yr CS, every concept grounded
  with a real-world example; specific questions/examples in notes get
  a "why / method" walkthrough

Changes v2.1:
- llm_chat rewritten to use plain `requests` for BOTH Cerebras and Groq,
  matching the pattern already used in chipper.py/main.py. Removes the
  `cerebras.cloud.sdk` and `groq` SDK dependencies entirely — those were
  never installed, so every podcast call was silently eating exceptions
  and falling straight to Groq, draining its small 100K TPD limit.
"""

import asyncio
import json
import os
import re
import sys
import requests
from pathlib import Path
from typing import Optional
import edge_tts
from pydub import AudioSegment


# ── Config ──────────────────────────────────────────────
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

CEREBRAS_MODEL = "gpt-oss-120b"
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

BIR_VOICE = "en-US-AndrewNeural"
MIA_VOICE = "en-US-EmmaMultilingualNeural"


OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SEGMENTS = ["hook", "context", "core", "sowhat", "wrap"]

SEGMENT_INSTRUCTIONS = {
    "hook":    "Generate ONLY the Hook segment (0-2 min, 8-10 turns). Bir opens with a relatable real-world question or scenario that the material actually answers. Engaging, concrete, no jargon yet.",
    "context": "Generate ONLY the Context segment (2-5 min, 10-12 turns). Mia frames what the material is about and why it exists — the problem it solves. Bir asks clarifying questions. Ground the 'why' with one real-world example.",
    "core":    "Generate ONLY the Core Concept segment (5-12 min, 18-22 turns). Deep technical dive. For EACH key concept: precise technical definition -> immediate real-world example -> back to the technical detail. If the material contains a specific example, question, or code/algorithm choice, Bir raises it and Mia walks through WHY it's done that way and the method behind it, step by step.",
    "sowhat":  "Generate ONLY the So What segment (12-17 min, 12-15 turns). Real applications, where a 2nd/3rd-year CS student would actually hit this (projects, interviews, systems they use daily), and future impact. Stay concrete.",
    "wrap":    "Generate ONLY the Wrap Up segment (17-20 min, 8-10 turns). Bir summarizes the whole thing in his own words, Mia corrects and confirms. End with one takeaway the listener can apply.",
}

SYSTEM_PROMPT = """You are a podcast script writer for MAROS (AdaptLearn), an AI-powered learning platform at VNIT Nagpur.

Audience: 2nd and 3rd year CS undergraduates. They are technical — do NOT dumb things down or over-explain basics. But they learn best through concrete examples.

Two hosts:
- BIR: curious male learner (a sharp CS student). Asks pointed questions, pushes back, says "wait, I don't get that", summarizes to check understanding. Never lectures.
- MIA: expert female. Precise and technical, but ALWAYS grounds concepts: technical definition first in one tight sentence, then IMMEDIATELY a real-world example that makes it click, then back to the technical thread.

STRICT SCRIPT RULES:
1. Bir always speaks first in each segment — asks before Mia explains.
2. Every concept pattern: precise definition -> real-world example -> technical depth. The example carries the understanding; the definition carries the rigor.
3. Do NOT dump raw context. Pick what matters, keep it sharp. Depth over breadth.
4. If the source material contains a specific example, question, algorithm step, or design choice (e.g. "why this loop", "why this data structure"), address it directly: Mia explains the WHY and the METHOD, not just the what.
5. Use the web research context ONLY to sharpen explanations and find better examples — the source material is the spine of the episode.
6. Complexity ramps up across segments — hook is accessible, core is genuinely technical.
7. Max 3 sentences per speaking turn.
8. Bir pushes back at least once per segment: "wait, so [restatement]?"
9. Wrap-up: Bir summarizes everything in his own words, Mia corrects/confirms.

OUTPUT: Return ONLY a valid JSON array. No markdown, no code fences, no preamble, no trailing commas.
Format:
[
  {"segment": "Hook", "speaker": "Bir", "text": "..."},
  {"segment": "Hook", "speaker": "Mia", "text": "..."}
]"""


# ── LLM Router: Cerebras primary, Groq fallback ─────────
def llm_chat(messages: list[dict], temperature: float = 0.85, max_tokens: int = 4096) -> str:
    """Cerebras primary, Groq fallback — plain requests, no SDK dependency.
    Matches the working pattern already used in chipper.py and main.py."""

    if CEREBRAS_API_KEY:
        try:
            res = requests.post(
                "https://api.cerebras.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {CEREBRAS_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": CEREBRAS_MODEL,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=120,
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"  [llm] Cerebras failed ({e}) → falling back to Groq")

    res = requests.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]


# ── JSON Sanitizer (fixes trailing-comma failures) ──────
def parse_json_array(raw: str) -> list:
    raw = re.sub(r"```json|```", "", raw).strip()
    # slice from first [ to last ] — drops any preamble/postamble text
    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    # kill trailing commas before ] or }  ← the "Illegal trailing comma" bug
    raw = re.sub(r",\s*([\]}])", r"\1", raw)
    return json.loads(raw)


# ── PDF Extractor (unchanged) ────────────────────────────
def extract_from_pdf(pdf_path: str) -> tuple[str, str]:
    import fitz
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc[:3]:
        text += page.get_text()
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    text_lower_full = text.lower()
    abstract_pos = text_lower_full.find("abstract")

    header_text = text[:abstract_pos] if abstract_pos != -1 else text[:1500]
    header_lines = [l.strip() for l in header_text.split('\n') if l.strip()]

    def looks_like_author_line(line: str) -> bool:
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
        abstract = text[start + 8:end].strip()
        abstract = re.sub(r'\s*\d{1,2}\s*$', '', abstract).strip()
    return title, abstract


# ── Web Enrichment (DuckDuckGo, same as MAROS search) ────
def _ddg_search(query: str, max_results: int = 3) -> list[dict]:
    try:
        try:
            from ddgs import DDGS  # newer package name
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        print(f"  [search] DDG failed for '{query}': {e}")
        return []


def extract_key_points(title: str, material: str) -> dict:
    """One LLM pass: pull out searchable concepts + any specific
    examples/questions embedded in the material (notes often have these)."""
    raw = llm_chat(
        [
            {"role": "system", "content": "You extract key points from study material. Return ONLY valid JSON, no markdown, no trailing commas."},
            {"role": "user", "content": f"""Title: {title}
Material:
{material[:4000]}

Return JSON exactly in this shape:
{{
  "concepts": ["2-4 core technical concepts worth researching, as short search queries"],
  "specifics": ["0-3 specific examples, questions, algorithm steps, or design choices found IN the material that deserve a why/method explanation (empty list if none)"]
}}"""},
        ],
        temperature=0.3,
        max_tokens=600,
    )
    try:
        data = json.loads(re.sub(r",\s*([\]}])", r"\1", re.sub(r"```json|```", "", raw).strip()))
        return {
            "concepts": data.get("concepts", [])[:4],
            "specifics": data.get("specifics", [])[:3],
        }
    except Exception as e:
        print(f"  [enrich] key-point extraction failed ({e}) — skipping enrichment")
        return {"concepts": [], "specifics": []}


def build_web_context(title: str, material: str) -> tuple[str, list[str]]:
    """Search DDG on extracted concepts + specifics, return a compact
    context block (capped) + the list of specifics for the prompt."""
    points = extract_key_points(title, material)
    queries = []
    for c in points["concepts"]:
        queries.append(f"{c} explained real world example")
    for s in points["specifics"]:
        queries.append(f"{s} why how it works")

    chunks = []
    for q in queries[:6]:
        print(f"  [search] {q}")
        for r in _ddg_search(q, max_results=2):
            body = r.get("body", "").strip()
            if body:
                chunks.append(f"- ({q}) {body}")

    context = "\n".join(chunks)[:3500]  # keep it sharp, don't dump
    return context, points["specifics"]


# ── Script Generator (per segment, with retry) ──────────
def generate_segment_script(
    paper_title: str,
    material: str,
    domain: str,
    segment: str,
    web_context: str = "",
    specifics: list[str] | None = None,
) -> list[dict]:
    instruction = SEGMENT_INSTRUCTIONS[segment]
    segment_label = segment.replace("sowhat", "So What").replace("wrap", "Wrap Up").title()

    specifics_block = ""
    if specifics:
        specifics_block = "\nSpecific points from the material that MUST get a why/method walkthrough somewhere in the episode:\n" + "\n".join(f"- {s}" for s in specifics)

    web_block = f"\nWeb research context (use only to sharpen explanations and examples):\n{web_context}" if web_context else ""

    user_prompt = f"""Title: {paper_title}
Domain: {domain}
Source material (the spine of the episode):
{material[:4000]}
{specifics_block}{web_block}

{instruction}
Use "{segment_label}" as the segment name in every turn.
Return ONLY the JSON array. No extra text, no trailing commas."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # attempt 1 + one strict retry on parse failure
    for attempt in range(2):
        raw = llm_chat(messages, temperature=0.85 if attempt == 0 else 0.5)
        try:
            return parse_json_array(raw)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  [{segment}] JSON parse failed (attempt {attempt + 1}): {e}")
            if attempt == 0:
                messages.append({"role": "assistant", "content": raw[:2000]})
                messages.append({"role": "user", "content": "That was not valid JSON. Return ONLY the corrected, strictly valid JSON array. No trailing commas, no markdown fences, no text outside the array."})
    raise RuntimeError(f"Segment '{segment}' failed JSON parsing after retry")


# ── Edge TTS Audio Generator (unchanged) ─────────────────

async def _tts_with_retry(text: str, voice: str, output_path: Path, turn_idx: int, max_attempts: int = 3) -> bool:
    """Render one turn to disk, retrying on Edge TTS transients.
    Returns True on success, False if all attempts failed."""
    for attempt in range(max_attempts):
        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_path))
            # Edge TTS sometimes 'succeeds' but writes 0 bytes — check.
            if output_path.exists() and output_path.stat().st_size > 100:
                return True
            raise RuntimeError("TTS wrote empty/tiny file")
        except Exception as e:
            wait = 2 ** attempt   # 1s, 2s, 4s
            print(f"  [tts] turn {turn_idx} attempt {attempt+1} failed ({e}) — retrying in {wait}s")
            await asyncio.sleep(wait)
    print(f"  [tts] turn {turn_idx} PERMANENTLY failed after {max_attempts} attempts")
    return False


async def generate_all_audio(turns: list[dict], job_id: str) -> list[Path]:
    """Concurrent TTS with per-turn retries and bounded concurrency so
    Edge TTS doesn't rate-limit us into a total failure."""
    # Concurrency cap — Edge TTS gets flaky above ~10 concurrent requests.
    sem = asyncio.Semaphore(8)

    async def _bounded(text, voice, path, idx):
        async with sem:
            return await _tts_with_retry(text, voice, path, idx)

    tasks, paths = [], []
    for i, turn in enumerate(turns):
        voice = BIR_VOICE if turn["speaker"] == "Bir" else MIA_VOICE
        path = OUTPUT_DIR / f"{job_id}_turn_{i:03d}.mp3"
        paths.append(path)
        tasks.append(_bounded(turn["text"], voice, path, i))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    successes = sum(1 for r in results if r is True)
    print(f"  [tts] {successes}/{len(turns)} turns rendered successfully")

    if successes < len(turns) * 0.9:
        # More than 10% missing — the podcast will have noticeable gaps.
        raise RuntimeError(f"TTS failed on {len(turns) - successes}/{len(turns)} turns — likely rate-limited")

    return paths


# ── Audio Stitcher (unchanged) ───────────────────────────
PAUSE_BETWEEN_TURNS = 600
PAUSE_BETWEEN_SEGMENTS = 1500

def stitch_audio(turns: list[dict], audio_paths: list[Path], job_id: str) -> Path:
    final = AudioSegment.empty()
    current_segment = None
    skipped = 0
    for turn, path in zip(turns, audio_paths):
        if not path.exists():
            print(f"  Warning: missing {path}")
            skipped += 1
            continue
        try:
            clip = AudioSegment.from_mp3(str(path))
        except Exception as e:
            print(f"  Warning: corrupt audio at {path}, skipping ({e})")
            skipped += 1
            continue
        if turn["segment"] != current_segment:
            if current_segment is not None:
                final += AudioSegment.silent(duration=PAUSE_BETWEEN_SEGMENTS)
            current_segment = turn["segment"]
        else:
            final += AudioSegment.silent(duration=PAUSE_BETWEEN_TURNS)
        final += clip

    if skipped:
        print(f"  [stitch] {skipped}/{len(turns)} turns skipped (missing/corrupt audio)")
    output_path = OUTPUT_DIR / f"{job_id}_podcast.mp3"
    final.export(str(output_path), format="mp3", bitrate="128k")
    for path in audio_paths:
        if path.exists():
            path.unlink()
    return output_path


# ── Main Pipeline ────────────────────────────────────────
async def generate_podcast(
    paper_title: str,
    abstract: str,
    domain: str = "general",
    job_id: Optional[str] = None,
    enable_web_enrichment: bool = True,
) -> dict:
    """
    `abstract` is the source material — paper abstract OR uploaded notes.
    `job_id` is the filename prefix; in MAROS this is the paper_id.
    """
    import uuid

    if not job_id:
        job_id = str(uuid.uuid4())[:8]

    # ── Web Enrichment ──────────────────────────────────
    web_context, specifics = "", []
    if enable_web_enrichment:
        print(f"[{job_id}] Enriching material via web search...")
        web_context, specifics = build_web_context(paper_title, abstract)
        print(f"[{job_id}]   → {len(web_context)} chars context, {len(specifics)} specifics")

    # ── Generate Podcast Script ─────────────────────────
    all_turns = []
    for seg in SEGMENTS:
        print(f"[{job_id}] Generating segment: {seg}...")
        turns = generate_segment_script(
            paper_title,
            abstract,
            domain,
            seg,
            web_context=web_context,
            specifics=specifics,
        )
        print(f"[{job_id}]   → {len(turns)} turns")
        all_turns.extend(turns)

    # ── Generate Audio ──────────────────────────────────
    print(f"[{job_id}] Total turns: {len(all_turns)} — generating audio...")
    audio_paths = await generate_all_audio(all_turns, job_id)

    # ── Stitch Audio ────────────────────────────────────
    print(f"[{job_id}] Stitching final podcast...")
    final_path = stitch_audio(all_turns, audio_paths, job_id)
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