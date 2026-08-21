"""
Meal narration and forced alignment.

Turns a Meal's `voice.script` into an audio track, then works out WHEN each
`narration_anchor` is actually spoken. That timing sidecar is what locks the
visuals to the voice — no scene timing is ever hand-authored.

Reuses MAROS infrastructure rather than reimplementing it:
    podcastengine._tts_with_retry   edge-tts with backoff and an empty-file guard
    chipper._get_whisper            the cached faster-whisper model

Alignment method, borrowed from reel_planner._time_from_whisper: we are
transcribing audio we generated ourselves from a known script, so alignment is
a positional walk — the Nth spoken word is the Nth script word. If Whisper's
word count drifts too far from the script's, the walk would smear, so we fall
back to proportional estimation rather than shipping visuals that lag the
voice.

    python3 narrate.py catalogue/meal_input_output.json
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

# MAROS is the source of truth and is imported read-only. Nothing here writes
# into its tree; output lands in meals/build/.
MAROS_ROOT = Path(os.getenv("MAROS_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(MAROS_ROOT))

BUILD_DIR = Path(__file__).parent / "build"
BUILD_DIR.mkdir(exist_ok=True)

# Drift beyond this and the positional walk is untrustworthy.
MAX_DRIFT = 0.20
# Spoken words per second, used only when Whisper is unavailable.
FALLBACK_WPS = 2.6


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


async def synthesize(script: str, voice: str, rate: str, pitch: str, out: Path) -> Path:
    """Render the whole script to one mp3 through MAROS's hardened TTS path."""
    import edge_tts
    from podcastengine import _tts_with_retry

    # _tts_with_retry takes a bare voice name; prosody needs Communicate
    # directly. Use the helper when there is no prosody to apply, so the
    # retry/empty-file protection is not lost for the common case.
    if rate in ("", "+0%") and pitch in ("", "+0Hz"):
        ok = await _tts_with_retry(script, voice, out, turn_idx=0)
        if not ok:
            raise RuntimeError("TTS failed after retries")
        return out

    for attempt in range(3):
        try:
            comm = edge_tts.Communicate(script, voice, rate=rate, pitch=pitch)
            await comm.save(str(out))
            if out.exists() and out.stat().st_size > 100:
                return out
            raise RuntimeError("TTS wrote an empty file")
        except Exception as e:
            print(f"  [tts] attempt {attempt + 1} failed ({e})")
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("TTS failed after retries")


def audio_duration(path: Path) -> float:
    from pydub import AudioSegment
    return len(AudioSegment.from_file(str(path))) / 1000.0


def word_times(audio: Path, script_words: list[str]) -> list[dict] | None:
    """Per-script-word timings via Whisper, or None if untrustworthy."""
    try:
        from chipper import _get_whisper
        model = _get_whisper()
        segments, _info = model.transcribe(
            str(audio),
            beam_size=1,
            language=os.getenv("WHISPER_LANG", "en") or None,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        heard = [
            {"w": w.word.strip(), "start": float(w.start), "end": float(w.end)}
            for seg in segments
            for w in (seg.words or [])
            if _norm(w.word)
        ]
    except Exception as e:
        print(f"  [align] Whisper unavailable ({e}) — estimating instead")
        return None

    if not heard:
        return None

    drift = abs(len(heard) - len(script_words)) / max(len(script_words), 1)
    if drift > MAX_DRIFT:
        print(f"  [align] heard {len(heard)} words vs {len(script_words)} scripted "
              f"({drift:.0%} drift) — estimating instead")
        return None

    # Positional walk: the Nth script word takes the Nth heard timing.
    out = []
    for i, sw in enumerate(script_words):
        h = heard[min(i, len(heard) - 1)]
        out.append({"w": sw, "start": h["start"], "end": h["end"]})
    return out


def estimate_times(script_words: list[str], duration: float) -> list[dict]:
    """Proportional fallback — every word gets an equal slice."""
    n = max(len(script_words), 1)
    per = duration / n
    return [
        {"w": w, "start": round(i * per, 3), "end": round((i + 1) * per, 3)}
        for i, w in enumerate(script_words)
    ]


def collect_anchors(meal: dict) -> list[str]:
    """Every narration_anchor in the Meal, scenes and code actions alike."""
    found = []
    for scene in meal["scenes"]:
        if "narration_anchor" in scene:
            found.append(scene["narration_anchor"])
        visual = scene["visual"]
        if visual["type"] == "code_editor":
            for action in visual.get("actions", []):
                if "narration_anchor" in action:
                    found.append(action["narration_anchor"])
        for key in ("nodes", "edges", "steps"):
            for item in visual.get(key, []) if isinstance(visual.get(key), list) else []:
                if isinstance(item, dict) and "narration_anchor" in item:
                    found.append(item["narration_anchor"])
    return found


def anchor_time(script: str, anchor: str, times: list[dict]) -> float:
    """Start time of an anchor phrase, by word offset into the script."""
    prefix = script.split(anchor)[0]
    word_index = len(prefix.split())
    if word_index >= len(times):
        return times[-1]["start"] if times else 0.0
    return round(times[word_index]["start"], 3)


def build_captions(times: list[dict], per_line: int) -> list[dict]:
    lines = []
    for i in range(0, len(times), per_line):
        chunk = times[i:i + per_line]
        lines.append({
            "text": " ".join(w["w"] for w in chunk),
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
            "words": chunk,
        })
    return lines


def narrate(meal_path: Path) -> Path:
    meal = json.loads(meal_path.read_text())["meal"]
    voice = meal["voice"]
    script = voice["script"]
    script_words = script.split()

    audio_path = BUILD_DIR / f"{meal['id']}.mp3"
    print(f"{meal['id']}:")
    print(f"  synthesising {len(script_words)} words as {voice.get('voice_id')}")
    asyncio.run(synthesize(
        script,
        voice.get("voice_id", "en-US-BrianNeural"),
        voice.get("rate", "+0%"),
        voice.get("pitch", "+0Hz"),
        audio_path,
    ))

    duration = audio_duration(audio_path)
    print(f"  audio: {duration:.1f}s -> {audio_path.name}")

    times = word_times(audio_path, script_words)
    method = "whisper" if times else "estimated"
    if times is None:
        times = estimate_times(script_words, duration)
    print(f"  alignment: {method}")

    anchors = {a: anchor_time(script, a, times) for a in collect_anchors(meal)}
    per_line = meal.get("captions", {}).get("words_per_line", 5)

    sidecar = {
        "meal_id": meal["id"],
        "audio": audio_path.name,
        "duration": round(duration, 3),
        "alignment": method,
        "anchors": anchors,
        "captions": build_captions(times, per_line),
    }

    out = BUILD_DIR / f"{meal['id']}.timing.json"
    out.write_text(json.dumps(sidecar, indent=2) + "\n")

    print(f"  anchors resolved: {len(anchors)}")
    for text, t in sorted(anchors.items(), key=lambda kv: kv[1]):
        print(f"    {t:6.2f}s  {text[:60]}")
    print(f"  -> {out.name}")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__.strip())
        raise SystemExit(1)
    for arg in sys.argv[1:]:
        narrate(Path(arg))
