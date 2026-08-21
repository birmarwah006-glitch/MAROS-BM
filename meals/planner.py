"""
Meal planner — a real lecture in, a connected Meal series out.

    30-minute Python project  ->  12-15 Meals  ->  by the last one you can
                                                    build the project yourself

This is the piece the product rests on, and it is deliberately NOT chipper.
Chipper segments on a 20-minute sliding window with MAX_MODULES=8; that is
duration-driven, and the product rule is that the SOURCE DURATION MUST NOT
DETERMINE THE MEAL COUNT — learning objectives do. Chipper still contributes
what it is good at: the transcript and the timestamps that let a Meal cite its
source.

Three passes, deliberately separable so a bad one can be re-run alone.

  PASS 1  COMPREHEND
      Read the whole lecture at once. What is being built? What concepts does
      it actually teach? What ANALOGIES does the teacher use, verbatim? What
      code gets written? What is filler?

      The analogies matter more than they look. A lecturer who says "a
      dictionary is like a phone book" has already done the hard work of
      making the idea land for this audience. Inventing a fresh analogy throws
      that away and risks one that is subtly wrong. So they are extracted and
      carried through to the Meals that need them.

  PASS 2  CURRICULUM
      Turn comprehension into an ORDERED list of learning objectives, one per
      Meal. Constraints that are checked, not just requested: strictly
      increasing dependencies, no duplicate objectives, coverage of the whole
      build, and a count that follows the material rather than a target.

  PASS 3  AUTHOR
      Write each Meal against the canonical schema, in series order, with the
      preceding objectives in context so Meal 7 can build on Meal 6 instead of
      re-teaching it.

Accuracy is enforced by execution, not by trust: every Meal's code is run for
real by verify.py afterwards, and a Meal whose code does not run is not
publishable.

    python3 planner.py --job <chipper_job_id>
    python3 planner.py --transcript path/to/transcript.txt --title "..."
    python3 planner.py --job <id> --pass1-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
MAROS_ROOT = Path(os.getenv("MAROS_ROOT", HERE.parent))
sys.path.insert(0, str(MAROS_ROOT))

# config must be imported BEFORE the engines: podcastengine reads
# GROQ_API_KEY at import time and never calls load_dotenv() itself, so
# importing it first yields an empty key and a 401. main.py gets away with it
# because it imports config first. This is that same ordering, made explicit.
import config  # noqa: F401
from podcastengine import llm_chat  # Cerebras primary, Groq fallback

import taxonomy

BUILD_DIR = HERE / "build"
CATALOGUE_DIR = HERE / "catalogue"
BUILD_DIR.mkdir(exist_ok=True)
CATALOGUE_DIR.mkdir(exist_ok=True)

# A Meal is 30-90 spoken seconds. At the ~150wpm edge-tts actually delivers,
# that is roughly 75-225 words. The cap is a guard against a runaway script,
# not a target to hit.
SCRIPT_WORD_CAP = 200

# Guard rails on the series length. NOT a target: the count follows the
# material. These only catch a model that has produced something absurd.
MIN_MEALS, MAX_MEALS = 5, 24

# The curriculum reply must fit under the per-minute ceiling alongside its own
# prompt. Roughly 200 tokens per Meal entry, so this comfortably covers a
# 20-Meal series while leaving room for the digest and the catalogue.
CURRICULUM_MAX_TOKENS = 3400

# An authored Meal is ~700 tokens of JSON. Reserving 4000 meant each call
# claimed half the per-minute ceiling and the account rate-limited anyway,
# which cost seven Meals in a single run.
AUTHOR_MAX_TOKENS = 2200


# ─────────────────────────────────────────────────────────────────────────
# LLM plumbing
#
# Two things shape this layer, both learned the hard way against the live API.
#
# 1. explainengine._safe_llm never raises — it logs and returns "". That is
#    right for a chat route, where a dead LLM should degrade to "no answer",
#    but wrong here: a rate-limit rejection came back indistinguishable from a
#    model that produced nothing, and three retries were burned on a request
#    that could never succeed. podcastengine.llm_chat is called directly
#    instead so the real error survives.
#
# 2. Groq's free tier allows 8000 tokens PER MINUTE. A 45-minute lecture is
#    ~13k tokens, so a single whole-lecture call is not merely slow, it is
#    impossible. Everything below is budgeted around that ceiling.
# ─────────────────────────────────────────────────────────────────────────

# Measured against the live endpoint: Groq reported 13,271 tokens for a
# transcript of 8,863 words, i.e. ~1.5 tokens per word for speech transcripts.
TOKENS_PER_WORD = 1.8

# The account ceiling. Override if the key is upgraded.
TPM_LIMIT = int(os.getenv("MAROS_LLM_TPM", "8000"))

# Leave headroom: the reported figure includes the system prompt and the
# reservation for max_tokens, and running right at the edge just trades a
# rejection for a retry.
TPM_SAFETY = 0.75


def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * TOKENS_PER_WORD)


class _RateBudget:
    """Tracks tokens spent in the trailing minute and waits when the next call
    would breach the limit. Cheaper than discovering the limit by 429."""

    def __init__(self, limit: int = TPM_LIMIT):
        self.limit = int(limit * TPM_SAFETY)
        self._spent: list[tuple[float, int]] = []

    def _prune(self, now: float) -> None:
        self._spent = [(t, n) for t, n in self._spent if now - t < 60.0]

    def reserve(self, tokens: int) -> None:
        now = time.time()
        self._prune(now)
        used = sum(n for _, n in self._spent)
        if used + tokens > self.limit and self._spent:
            oldest = min(t for t, _ in self._spent)
            wait = max(60.0 - (now - oldest), 0) + 1.0
            print(f"      rate budget: waiting {wait:.0f}s "
                  f"({used} + {tokens} would exceed {self.limit}/min)")
            time.sleep(wait)
            self._prune(time.time())
        self._spent.append((time.time(), tokens))


BUDGET = _RateBudget()


def _extract_json(raw: str):
    """Pull the first JSON object or array out of a model reply."""
    if not raw:
        raise ValueError("empty model reply")
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            candidate = cleaned[start:end + 1]
            candidate = re.sub(r",\s*([\]}])", r"\1", candidate)  # trailing commas
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no parsable JSON in reply: {cleaned[:200]!r}")


def _llm_raw(system: str, user: str, label: str, max_tokens: int,
             temperature: float) -> str:
    """One call, rate-budgeted, with the real error preserved."""
    BUDGET.reserve(estimate_tokens(system + user) + max_tokens)
    try:
        return llm_chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
        ) or ""
    except Exception as e:
        message = str(e)
        # A request that is too large will never succeed by retrying; say so
        # rather than letting the caller spend its attempts discovering it.
        if "Request too large" in message or "reduce your message size" in message:
            raise RuntimeError(
                f"{label}: the request exceeds the account's per-minute token "
                f"limit ({TPM_LIMIT} TPM). Shrink the window, not the retries. "
                f"Original: {message[:200]}"
            ) from e
        if "rate_limit" in message.lower() or "429" in message:
            print(f"      {label}: rate limited, backing off 20s")
            time.sleep(20)
            return ""
        print(f"      {label}: LLM call failed — {message[:200]}")
        return ""


def _llm_json(system: str, user: str, label: str, max_tokens: int = 3000,
              temperature: float = 0.3, attempts: int = 3):
    """Ask for JSON and keep asking until it parses.

    gpt-oss-120b is a reasoning model and returns empty content often enough
    that one attempt is not a plan. Each retry lowers the temperature, because
    a model that just emitted unparsable output is usually being too creative.
    """
    last_error = None
    for attempt in range(attempts):
        raw = _llm_raw(system, user, f"{label} (attempt {attempt + 1})",
                       max_tokens, max(temperature - attempt * 0.1, 0.0))
        if not raw.strip():
            last_error = "empty reply"
            continue
        try:
            return _extract_json(raw)
        except ValueError as e:
            last_error = str(e)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}")


def window_transcript(text: str, words_per_window: int) -> list[str]:
    """Split on paragraph boundaries so a window never starts mid-sentence."""
    blocks = [b for b in text.split("\n\n") if b.strip()]
    windows, current, count = [], [], 0
    for block in blocks:
        n = len(block.split())
        if current and count + n > words_per_window:
            windows.append("\n\n".join(current))
            current, count = [], 0
        current.append(block)
        count += n
    if current:
        windows.append("\n\n".join(current))
    return windows


# ─────────────────────────────────────────────────────────────────────────
# Source loading
# ─────────────────────────────────────────────────────────────────────────

def load_from_job(job_id: str) -> dict:
    """Read a chipper manifest. Its module boundaries are NOT used as Meal
    boundaries — only the transcript and the timestamps, which let a Meal cite
    where in the source it came from."""
    manifest_path = MAROS_ROOT / "outputs" / job_id / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest for job {job_id}")
    data = json.loads(manifest_path.read_text())

    segments = [
        {
            "start": m["start"],
            "end": m["end"],
            "chipper_concept": m.get("concept", ""),
            "text": m.get("transcript", ""),
        }
        for m in data["modules"]
    ]

    title = data["modules"][0]["concept"] if data.get("modules") else "Lecture"
    yt_path = MAROS_ROOT / "outputs" / job_id / "youtube.json"
    if yt_path.exists():
        try:
            title = json.loads(yt_path.read_text()).get("title") or title
        except Exception:
            pass

    return {"job_id": job_id, "title": title, "segments": segments}


def load_from_transcript(path: Path, title: str) -> dict:
    text = Path(path).read_text()
    return {
        "job_id": None,
        "title": title,
        "segments": [{"start": "00:00", "end": "", "chipper_concept": "", "text": text}],
    }


def full_text(source: dict) -> str:
    """The whole lecture, with timestamps kept inline so the model can cite
    where something was taught."""
    return "\n\n".join(
        f"[{s['start']}-{s['end']}] {s['text']}".strip()
        for s in source["segments"]
        if s["text"].strip()
    )


# ─────────────────────────────────────────────────────────────────────────
# PASS 1 — COMPREHEND
# ─────────────────────────────────────────────────────────────────────────

COMPREHEND_SYSTEM = """You are a curriculum analyst for MAROS, a Python-first
microlearning platform. You are given the transcript of a real programming
lecture or project walkthrough.

Your job is to UNDERSTAND it, not to summarise it. Someone will use your
analysis to rebuild this lecture as a series of short lessons, and they will
not have the transcript.

Return ONLY valid JSON. No markdown, no prose, no commentary.

Pay particular attention to ANALOGIES, and read that word BROADLY. An analogy
is any moment where the teacher explains a new thing by comparing it to
something the learner already knows. Two kinds matter, and the second is the
one most often missed:

  EVERYDAY   "a variable is like a labelled box", "a function is a recipe"
  TECHNICAL  "you access it like a dictionary", "this is basically a list",
             "it is very similar to XML", "think of it as a for loop"

In a programming lecture the TECHNICAL comparisons are usually the more
valuable of the two, because they connect an unfamiliar API to something the
learner has already learned. Capture phrases of the form "like a X", "similar
to X", "basically a X", "the same idea as X", "think of it as X".

Capture them VERBATIM. The teacher has already found a comparison that works
for this audience; that is hard-won and easy to throw away. Do not paraphrase,
and do not invent new ones. If the lecture genuinely uses none, return an
empty list — a fabricated analogy is worse than none.

Be honest about what is NOT taught. If the lecture waves at a topic without
explaining it, that is not a concept it teaches."""

COMPREHEND_USER = """LECTURE TITLE: {title}

TRANSCRIPT (timestamps in square brackets):
{transcript}

Return this exact JSON shape:

{{
  "is_programming": true,
  "language": "python",
  "builds_something": true,
  "artifact": "one sentence: what is built by the end, or null if nothing is built",
  "summary": "3-4 sentences: what this lecture actually teaches, in order",
  "arc": ["the sequence of things the lecture does, 5-12 short phrases"],
  "concepts_taught": [
    {{
      "name": "short name as the lecture treats it",
      "what_is_taught": "one sentence: the specific claim or skill",
      "evidence": "a short VERBATIM quote from the transcript",
      "timestamp": "MM:SS where this happens",
      "depth": "mentioned | explained | worked_through"
    }}
  ],
  "analogies": [
    {{
      "analogy": "VERBATIM quote of the comparison the teacher used",
      "explains": "which concept it is explaining",
      "timestamp": "MM:SS"
    }}
  ],
  "code_written": [
    {{
      "purpose": "what this code does",
      "code": "the code as best you can reconstruct it from the transcript",
      "timestamp": "MM:SS"
    }}
  ],
  "decisions": [
    {{"decision": "a choice the teacher made", "why": "the reason given"}}
  ],
  "filler": ["things a short lesson should drop: admin, tangents, repetition"]
}}

If this is NOT a programming lecture, set "is_programming" to false, fill
"summary", and leave the other lists empty."""


# Sized so one window plus its prompt plus the reply fits inside the
# per-minute ceiling. At ~1.5 tokens/word this is ~2600 tokens of transcript,
# ~700 of prompt and up to 2600 of reply — under 6000, with headroom.
WINDOW_WORDS = int(os.getenv("MAROS_WINDOW_WORDS", "1750"))


SYNTHESIS_SYSTEM = """You are consolidating per-section analyses of one
programming lecture into a single coherent picture.

The sections were analysed independently, so they overlap and repeat. Merge
them: one entry per distinct idea, in the order the lecture teaches it.

Return ONLY valid JSON. No markdown, no prose.

Do NOT invent anything that is not present in the sections. If the sections
disagree, prefer the one with the more specific evidence."""

SYNTHESIS_USER = """LECTURE TITLE: {title}

SECTION ANALYSES:
{sections}

Return:

{{
  "is_programming": true,
  "language": "python",
  "builds_something": true,
  "artifact": "one sentence: what is built by the end, or null",
  "summary": "3-4 sentences: what this lecture actually teaches, in order",
  "arc": ["the sequence of what the lecture does, 5-12 short phrases"],
  "concepts_taught": [
    {{"name": "...", "what_is_taught": "...", "evidence": "...",
      "timestamp": "MM:SS", "depth": "mentioned|explained|worked_through"}}
  ],
  "analogies": [{{"analogy": "VERBATIM", "explains": "...", "timestamp": "MM:SS"}}],
  "code_written": [{{"purpose": "...", "code": "...", "timestamp": "MM:SS"}}],
  "decisions": [{{"decision": "...", "why": "..."}}],
  "filler": ["..."]
}}"""


def analysis_cache_path(source: dict) -> Path:
    return BUILD_DIR / f"analysis_{_slug(source['title'])}.json"


def comprehend(source: dict, use_cache: bool = True) -> dict:
    """Pass 1, windowed.

    A whole-lecture call is not possible on this account: Groq's free tier
    allows 8000 tokens per minute and a 45-minute lecture is ~13k. So the
    lecture is read in windows and the partial analyses are then consolidated.

    This is the same shape as chipper's windowed segmentation, for the same
    underlying reason, and it has a side benefit: a model reading 1,750 words
    notices detail that one reading 9,000 words skims past — which matters
    most for the analogies, the thing most easily lost in a summary.
    """
    # Pass 1 is by far the most expensive stage — six rate-limited calls plus
    # a synthesis — and it depends only on the transcript. Caching it means
    # iterating on Passes 2 and 3 costs seconds instead of eight minutes.
    cache = analysis_cache_path(source)
    if use_cache and cache.exists():
        cached = json.loads(cache.read_text())
        print(f"[planner] Pass 1 — reusing cached analysis ({cache.name}); "
              f"delete it or pass --fresh to re-read the lecture")
        print(f"  concepts: {len(cached.get('concepts_taught', []))}, "
              f"analogies: {len(cached.get('analogies', []))}, "
              f"code: {len(cached.get('code_written', []))}")
        return cached

    transcript = full_text(source)
    windows = window_transcript(transcript, WINDOW_WORDS)
    total_words = len(transcript.split())

    print(f"[planner] Pass 1 — comprehending {total_words} words "
          f"in {len(windows)} window(s)...")

    partials = []
    for i, window in enumerate(windows, 1):
        print(f"    window {i}/{len(windows)} ({len(window.split())} words)")
        try:
            partials.append(_llm_json(
                COMPREHEND_SYSTEM,
                COMPREHEND_USER.format(
                    title=f"{source['title']} (section {i} of {len(windows)})",
                    transcript=window,
                ),
                f"comprehend-{i}",
                max_tokens=2600,
            ))
        except RuntimeError as e:
            # One unreadable window should not lose the rest of the lecture.
            print(f"      skipped: {e}")

    if not partials:
        raise RuntimeError("no window could be analysed — see errors above")

    # Cheap merge first, so the synthesis call sees compact input rather than
    # the raw partials.
    def gather(key: str) -> list:
        out, seen = [], set()
        for part in partials:
            for item in part.get(key) or []:
                fingerprint = json.dumps(item, sort_keys=True)[:180]
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    out.append(item)
        return out

    merged = {
        "concepts_taught": gather("concepts_taught"),
        "analogies": gather("analogies"),
        "code_written": gather("code_written"),
        "decisions": gather("decisions"),
        "filler": gather("filler"),
        "section_summaries": [p.get("summary", "") for p in partials],
        "section_arcs": [a for p in partials for a in (p.get("arc") or [])],
    }

    print(f"    merging {len(merged['concepts_taught'])} concepts, "
          f"{len(merged['analogies'])} analogies, "
          f"{len(merged['code_written'])} code snippets")

    try:
        result = _llm_json(
            SYNTHESIS_SYSTEM,
            SYNTHESIS_USER.format(
                title=source["title"],
                sections=json.dumps(merged, indent=1)[:9000],
            ),
            "synthesis",
            max_tokens=3000,
        )
    except RuntimeError as e:
        # If consolidation fails the raw merge is still usable — it is missing
        # only the narrative fields, which Pass 2 can live without.
        print(f"    synthesis failed ({e}); continuing with the raw merge")
        result = {
            "is_programming": True,
            "language": "python",
            "builds_something": bool(merged["code_written"]),
            "artifact": None,
            "summary": " ".join(merged["section_summaries"])[:800],
            "arc": merged["section_arcs"][:12],
            **merged,
        }

    # Consolidation is asked to merge duplicates, but it has a habit of
    # dropping whole categories instead — an early run turned 18 extracted
    # code snippets into zero. Anything that is VERBATIM EVIDENCE rather than
    # prose is restored from the windows if synthesis returned fewer, because
    # the windows read the actual transcript and the synthesis call did not.
    for key in ("analogies", "code_written", "decisions"):
        kept = len(result.get(key) or [])
        found = len(merged[key])
        if kept < found:
            print(f"    restoring {found} {key} (synthesis kept {kept})")
            result[key] = merged[key]

    print(f"  artifact: {result.get('artifact')}")
    print(f"  concepts taught: {len(result.get('concepts_taught', []))}")
    print(f"  analogies found: {len(result.get('analogies', []))}")
    print(f"  code snippets: {len(result.get('code_written', []))}")

    cache.write_text(json.dumps(result, indent=2) + "\n")
    return result


# ─────────────────────────────────────────────────────────────────────────
# PASS 2 — CURRICULUM
# ─────────────────────────────────────────────────────────────────────────

CURRICULUM_SYSTEM = """You are designing a MAROS Meal series from an analysed
lecture.

A Meal is one short lesson — 30 to 90 seconds — built around exactly ONE
learning objective. A series of Meals is watched in order, and by the last one
the learner should be able to build the thing the lecture builds.

Return ONLY valid JSON. No markdown, no prose.

THE RULES THAT MATTER:

1. ONE MEAL = ONE LEARNING OBJECTIVE. If an objective needs the word "and" to
   state it, it is two Meals.
2. THE COUNT FOLLOWS THE MATERIAL. Do not pad to reach a number and do not
   compress to be brief. A lecture with twelve real ideas gets twelve Meals.
3. STRICT ORDER. Meal N may only rely on what Meals 1..N-1 have taught, plus
   general knowledge. Never forward-reference.
4. SUFFICIENCY. Taken together the series must actually get someone to the
   finished artifact. Do not skip the unglamorous steps that the build needs.
5. NO FILLER. Course admin, greetings, tangents and repetition do not become
   Meals.
6. REUSE THE TEACHER'S ANALOGIES. If the analysis captured an analogy for a
   concept, attach it to the Meal that teaches that concept. Do not invent new
   analogies.
7. BE HONEST ABOUT CODE. Only attach code you can actually justify from the
   lecture. A Meal with no code is fine."""

CURRICULUM_USER = """LECTURE TITLE: {title}

ANALYSIS:
{analysis}

AVAILABLE CONCEPT IDS — use these exact strings, or null if none fits:
{catalogue}

Design the Meal series. Return:

{{
  "series_title": "short title for the whole series",
  "artifact": "what the learner can build after watching all of it",
  "meals": [
    {{
      "order": 1,
      "title": "short, concrete, max 60 chars",
      "objective": "ONE sentence: what the learner can do after this Meal",
      "concept_id": "an id from the list above, or null",
      "why_now": "why this comes at this point and not earlier or later",
      "builds_on": [list of earlier "order" numbers, empty for the first],
      "analogy": "the teacher's VERBATIM analogy if one applies, else null",
      "evidence": "short verbatim quote from the transcript this is grounded in",
      "timestamp": "MM:SS in the source",
      "code": "the code this Meal should show, or null",
      "visual_hint": "one of: input_output, loop, memory, flow, decision, terminal, none"
    }}
  ]
}}"""


def digest_analysis(analysis: dict) -> str:
    """A compact view of Pass 1 for Pass 2.

    The full analysis is far too large to send: with the catalogue, the system
    prompt and the reply reservation it blows straight past the per-minute
    ceiling. Pass 2 does not need the prose fields — it needs to know what was
    taught, in what order, with which analogy and which code attached. So it
    gets exactly that, one line each.
    """
    lines: list[str] = []
    if analysis.get("artifact"):
        lines.append(f"ARTIFACT: {analysis['artifact']}")
    if analysis.get("summary"):
        lines.append(f"SUMMARY: {analysis['summary'][:600]}")

    arc = analysis.get("arc") or []
    if arc:
        lines.append("ARC: " + " -> ".join(str(a)[:60] for a in arc[:12]))

    lines.append("\nCONCEPTS TAUGHT:")
    for c in (analysis.get("concepts_taught") or [])[:32]:
        lines.append(
            f"- [{c.get('timestamp','?')}] ({c.get('depth','?')}) {c.get('name')}: "
            f"{str(c.get('what_is_taught',''))[:120]}"
        )

    analogies = analysis.get("analogies") or []
    if analogies:
        lines.append("\nTEACHER'S ANALOGIES — reuse these verbatim, do not invent:")
        for a in analogies[:12]:
            lines.append(
                f"- explains {a.get('explains','?')}: \"{str(a.get('analogy',''))[:160]}\""
            )

    code = analysis.get("code_written") or []
    if code:
        lines.append("\nCODE WRITTEN IN THE LECTURE:")
        for c in code[:18]:
            snippet = str(c.get("code", "")).replace("\n", " ; ")[:150]
            lines.append(f"- [{c.get('timestamp','?')}] {str(c.get('purpose',''))[:70]}: {snippet}")

    decisions = analysis.get("decisions") or []
    if decisions:
        lines.append("\nDECISIONS:")
        for d in decisions[:8]:
            lines.append(f"- {str(d.get('decision',''))[:90]} — {str(d.get('why',''))[:90]}")

    filler = analysis.get("filler") or []
    if filler:
        lines.append("\nDROP (filler): " + "; ".join(str(f)[:50] for f in filler[:8]))

    return "\n".join(lines)


def curriculum_cache_path(source: dict) -> Path:
    return BUILD_DIR / f"curriculum_{_slug(source['title'])}.json"


def build_curriculum(source: dict, analysis: dict, use_cache: bool = True) -> dict:
    # The curriculum is cached for the same reason the analysis is, plus one
    # more that matters: resume matches Meals on an id derived from their
    # order and title, so a re-planned series with different titles would fail
    # to match and re-author everything. Caching the plan makes a resumed run
    # actually resume, and makes the series reproducible.
    cache = curriculum_cache_path(source)
    if use_cache and cache.exists():
        cached = json.loads(cache.read_text())
        print(f"[planner] Pass 2 — reusing cached plan ({cache.name}), "
              f"{len(cached.get('meals', []))} Meals")
        return cached

    print("[planner] Pass 2 — designing the Meal series...")

    result = _llm_json(
        CURRICULUM_SYSTEM,
        CURRICULUM_USER.format(
            title=source["title"],
            analysis=digest_analysis(analysis),
            catalogue=taxonomy.catalogue_for_prompt(),
        ),
        "curriculum",
        max_tokens=CURRICULUM_MAX_TOKENS,
    )

    meals = result.get("meals", [])
    problems = _check_curriculum(meals)

    # One targeted repair attempt. Re-running the whole pass usually produces a
    # different plan rather than a fixed one, so the faults are named instead.
    if problems:
        print(f"  {len(problems)} problem(s) in the plan — asking for a repair")
        for p in problems[:6]:
            print(f"    {p}")
        result = _llm_json(
            CURRICULUM_SYSTEM,
            CURRICULUM_USER.format(
                title=source["title"],
                analysis=digest_analysis(analysis),
                catalogue=taxonomy.catalogue_for_prompt(),
            ) + "\n\nA previous attempt had these faults. Fix them:\n- "
              + "\n- ".join(problems),
            "curriculum-repair",
            max_tokens=CURRICULUM_MAX_TOKENS,
        )
        meals = result.get("meals", [])
        problems = _check_curriculum(meals)

    # Whatever survives, normalise the concept ids honestly: an id that does
    # not resolve becomes null rather than being forced onto a near-miss.
    for meal in meals:
        resolved = taxonomy.nearest_valid(meal.get("concept_id") or "")
        if meal.get("concept_id") and not resolved:
            print(f"  note: concept id {meal['concept_id']!r} does not map to the "
                  f"Python taxonomy — left unset on Meal {meal.get('order')}")
        meal["concept_id"] = resolved

    result["meals"] = meals
    result["plan_warnings"] = problems

    cache.write_text(json.dumps(result, indent=2) + "\n")

    print(f"  series: {len(meals)} Meals")
    for m in meals:
        tag = m.get("concept_id") or "-"
        mark = " [analogy]" if m.get("analogy") else ""
        print(f"    {m.get('order'):>2}. {m.get('title')}  ({tag}){mark}")
    return result


def _check_curriculum(meals: list) -> list[str]:
    """Checks a prompt cannot enforce. Returns human-readable faults."""
    problems: list[str] = []

    if not meals:
        return ["the plan contains no Meals"]
    if len(meals) < MIN_MEALS:
        problems.append(f"only {len(meals)} Meals — a real lecture teaches more than that")
    if len(meals) > MAX_MEALS:
        problems.append(f"{len(meals)} Meals is too many; merge the ones that share an objective")

    orders = [m.get("order") for m in meals]
    if orders != sorted(orders) or len(set(orders)) != len(orders):
        problems.append("`order` must be unique and ascending, starting at 1")

    seen_objectives = set()
    for m in meals:
        order = m.get("order")

        objective = (m.get("objective") or "").strip().lower()
        if not objective:
            problems.append(f"Meal {order} has no objective")
        elif objective in seen_objectives:
            problems.append(f"Meal {order} repeats an earlier objective")
        seen_objectives.add(objective)

        # Rule 1, mechanically: an objective joining two verbs with "and" is
        # usually two Meals wearing one hat.
        if re.search(r"\band\b", objective) and len(objective.split()) > 12:
            problems.append(
                f"Meal {order} states a compound objective — split it or narrow it")

        # Rule 3: no forward references.
        for dep in m.get("builds_on") or []:
            if not isinstance(dep, int) or dep >= (order or 0):
                problems.append(
                    f"Meal {order} depends on Meal {dep}, which does not come before it")

        if len(m.get("title") or "") > 60:
            problems.append(f"Meal {order} has a title longer than 60 characters")

    return problems


# ─────────────────────────────────────────────────────────────────────────
# PASS 3 — AUTHOR
# ─────────────────────────────────────────────────────────────────────────

AUTHOR_SYSTEM = f"""You write a single MAROS Meal: one short lesson, 30-90
seconds, teaching exactly ONE objective.

Return ONLY valid JSON matching the shape given. No markdown, no prose.

THE SCRIPT IS READ ALOUD by a text-to-speech voice. Write only words a person
would say. No markdown, no arrows, no bare symbols. Write "input" not
"input()", "arrow" not "->". Spell out anything a voice cannot pronounce.
Under {SCRIPT_WORD_CAP} words total — this is a hard cap.

VOICE: a smart person explaining something to another student. Direct,
slightly energetic, never lecturing. Bad: "In today's lesson we will discuss
the concept of loops." Good: "You have ten thousand names. How do you make
Python do the same thing to every one?"

THE HOOK is the first one to three seconds and it decides whether anyone
watches. Make it a real question, a surprising fact, or a problem the learner
recognises. It MUST be technically accurate — never clickbait.

IF AN ANALOGY IS SUPPLIED, USE IT. It is the teacher's own words and it
already works for this audience. Do not replace it with your own. If none is
supplied, only add one if it genuinely helps, and never a strained one.

CODE MUST RUN, AND IT MUST RUN ON ITS OWN. It is executed as a complete
`main.py` in an empty directory, with nothing from any other Meal in scope,
and the Meal is REJECTED if it fails.

This is the one place where continuity does NOT apply. Teaching builds on
earlier Meals; code does not. If an earlier Meal created `soup`, this Meal's
snippet still has to create it again — the imports, the HTML, the parse, all
of it — or it dies with a NameError.

Concretely, every snippet must:
  - import everything it uses
  - define every name it references
  - never reach the network, and never need a package outside the standard
    library — the runner has neither
  - if it reads a file, SHIP THAT FILE in `code.files`. A Meal about reading a
    file should really read one; the file travels with the Meal
  - stay short: the few lines that carry the idea, plus the minimum setup

If teaching the idea honestly needs a page of setup, use a small inline string
instead of a real fetch. A three-line HTML literal is a better teacher than a
network call that cannot run.

If the code needs typed input, supply it in `stdin`. Do NOT write the expected
output — it is captured from a real run.

BE CRISP AND BE ACCURATE. Never invent behaviour Python does not have. If you
are unsure a detail is true, leave it out. A shorter correct Meal beats a
longer one with a wrong claim. Do not repeat yourself and do not pad.

CONTINUITY applies to EXPLANATION, not to code. Earlier Meals are listed
below: do not re-explain what they covered, and never forward-reference a
later one. But every code snippet is still standalone and runnable, as above."""

AUTHOR_USER = """SERIES: {series_title}
FINAL ARTIFACT: {artifact}

ALREADY TAUGHT (do not re-teach):
{prior}

THIS MEAL — number {order} of {total}
  title:      {title}
  objective:  {objective}
  why now:    {why_now}
  concept:    {concept_id}
  analogy:    {analogy}
  evidence:   {evidence}
  code hint:  {code}
  visual:     {visual_hint}

Return:

{{
  "title": "max 70 chars",
  "objective": "one sentence, the same objective as above, tightened",
  "script": "the FULL narration, in order, spoken language only",
  "hook_text": "the on-screen hook, max 90 chars",
  "question_text": "the problem this Meal answers, max 90 chars, or null",
  "concept_flow": {{
    "nodes": [{{"id": "a", "label": "max 30 chars", "kind": "box|actor|value|decision|output"}}],
    "edges": [{{"from": "a", "to": "b", "label": "optional, max 24 chars"}}]
  }},
  "code": {{
    "source": "runnable Python, or null if this Meal needs no code",
    "stdin": ["lines fed to the program, [] if none"],
    "files": [{{"name": "simple.html", "content": "any file the code opens"}}],
    "highlight": "the exact substring to emphasise, or null"
  }},
  "takeaway_text": "the one thing to remember, max 110 chars",
  "practice_prompt": "a task the learner does next, max 140 chars",
  "practice_hint": "one sentence, or null",
  "anchors": {{
    "hook": "VERBATIM phrase from script where the hook lands",
    "question": "VERBATIM phrase, or null",
    "concept": "VERBATIM phrase",
    "code": "VERBATIM phrase where code starts appearing",
    "execution": "VERBATIM phrase where output is discussed, or null",
    "visual": "VERBATIM phrase where the diagram is discussed, or null",
    "takeaway": "VERBATIM phrase",
    "practice": "VERBATIM phrase"
  }}
}}

Every anchor MUST appear in `script` EXACTLY ONCE, character for character.
The anchors are how the visuals are synchronised to the voice; an anchor that
does not appear breaks the Meal."""


def _slug(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return re.sub(r"_+", "_", out)[:48] or "meal"


def _fix_anchors(script: str, anchors: dict) -> tuple[dict, list[str]]:
    """Anchors must appear verbatim exactly once. Repair what can be repaired
    and drop what cannot, rather than emitting a Meal that fails validation."""
    fixed, notes = {}, []
    for beat, phrase in (anchors or {}).items():
        if not phrase or not isinstance(phrase, str):
            continue
        phrase = phrase.strip()
        count = script.count(phrase)
        if count == 1:
            fixed[beat] = phrase
            continue
        if count > 1:
            notes.append(f"anchor for {beat} is not unique — dropped")
            continue
        # Not found verbatim. Try a whitespace-tolerant match against the
        # script, which is where most misses come from.
        loose = re.escape(phrase).replace(r"\ ", r"\s+")
        m = re.search(loose, script)
        if m and script.count(m.group(0)) == 1:
            fixed[beat] = m.group(0)
            notes.append(f"anchor for {beat} recovered by whitespace match")
        else:
            notes.append(f"anchor for {beat} not found in script — dropped")
    return fixed, notes


def author_meal(plan: dict, spec: dict, prior: list[dict], total: int) -> dict:
    order = spec.get("order")
    print(f"  [{order}/{total}] {spec.get('title')}")

    prior_text = "\n".join(
        f"  {p['order']}. {p['title']} — {p['objective']}" for p in prior
    ) or "  (nothing yet — this is the first Meal)"

    written = _llm_json(
        AUTHOR_SYSTEM,
        AUTHOR_USER.format(
            series_title=plan.get("series_title", ""),
            artifact=plan.get("artifact", ""),
            prior=prior_text,
            order=order, total=total,
            title=spec.get("title"),
            objective=spec.get("objective"),
            why_now=spec.get("why_now", ""),
            concept_id=spec.get("concept_id") or "(unmapped)",
            analogy=spec.get("analogy") or "(none supplied — do not invent one)",
            evidence=spec.get("evidence", ""),
            code=spec.get("code") or "(none)",
            visual_hint=spec.get("visual_hint", "none"),
        ),
        f"author-{order}",
        max_tokens=AUTHOR_MAX_TOKENS,
        temperature=0.5,
    )

    script = (written.get("script") or "").strip()
    if not script:
        raise RuntimeError(f"Meal {order}: empty script")

    words = len(script.split())
    if words > SCRIPT_WORD_CAP * 1.25:
        print(f"      script is {words} words, over the {SCRIPT_WORD_CAP} cap")

    anchors, anchor_notes = _fix_anchors(script, written.get("anchors", {}))
    for note in anchor_notes:
        print(f"      {note}")

    return _assemble(plan, spec, written, script, anchors, order)


def _assemble(plan: dict, spec: dict, written: dict, script: str,
              anchors: dict, order: int) -> dict:
    """Turn the authored content into a schema-valid Meal document.

    Scene ASSEMBLY is deterministic and lives here, not in the model. The
    model decides what to teach and say; the shape of a Meal is not its
    decision to make.
    """
    meal_id = f"meal_{order:02d}_{_slug(spec.get('title') or '')}"
    scenes: list[dict] = []

    def add(beat: str, visual: dict, min_duration: float | None = None):
        scene: dict = {"beat": beat, "visual": visual}
        if beat in anchors:
            scene["narration_anchor"] = anchors[beat]
        if min_duration:
            scene["min_duration"] = min_duration
        scenes.append(scene)

    hook = (written.get("hook_text") or spec.get("title") or "").strip()
    add("hook", {"type": "text", "tone": "hook", "text": hook[:160]}, 2.0)

    if written.get("question_text"):
        add("question", {"type": "text", "tone": "question",
                         "text": str(written["question_text"])[:160]})

    flow = written.get("concept_flow") or {}
    nodes = [
        {"id": str(n.get("id") or f"n{i}"),
         "label": str(n.get("label") or "")[:40],
         "kind": n.get("kind") if n.get("kind") in
                 {"box", "actor", "value", "decision", "output"} else "box"}
        for i, n in enumerate(flow.get("nodes") or [])
        if n.get("label")
    ]
    if nodes:
        valid_ids = {n["id"] for n in nodes}
        edges = [
            {k: v for k, v in
             (("from", str(e.get("from"))), ("to", str(e.get("to"))),
              ("label", str(e.get("label"))[:32] if e.get("label") else None))
             if v is not None}
            for e in (flow.get("edges") or [])
            if str(e.get("from")) in valid_ids and str(e.get("to")) in valid_ids
        ]
        add("concept", {"type": "flow", "layout": "vertical",
                        "nodes": nodes, "edges": edges})

    code_block = written.get("code") or {}
    source_code = (code_block.get("source") or "").strip()
    if source_code:
        if not source_code.endswith("\n"):
            source_code += "\n"
        lines = source_code.rstrip("\n").split("\n")
        actions: list[dict] = [{
            "action": "type",
            "text": source_code.rstrip("\n"),
            "lines": list(range(1, len(lines) + 1)),
            "speed_cps": 26,
        }]
        highlight = code_block.get("highlight")
        if highlight and highlight in source_code:
            actions.append({"action": "highlight", "text": highlight})

        add("code", {"type": "code_editor", "language": "python",
                     "filename": "main.py", "code": source_code,
                     "show_line_numbers": True, "actions": actions})

        # Execution is left UNVERIFIED on purpose. verify.py runs the code for
        # real and writes back what it actually printed; validate.py refuses
        # the Meal until then. The model never supplies output.
        add("execution", {
            "type": "terminal",
            "command": "python main.py",
            "stdin": [str(x) for x in (code_block.get("stdin") or [])],
            **({"files": [
                {"name": str(f.get("name")), "content": str(f.get("content", ""))}
                for f in code_block["files"]
                if isinstance(f, dict) and f.get("name")
            ]} if code_block.get("files") else {}),
            "execution": {"verified": False, "source": "unverified",
                          "stdout": "", "stderr": "", "exit_code": -1},
        }, 3.0)

    if written.get("takeaway_text"):
        add("takeaway", {"type": "text", "tone": "takeaway",
                         "text": str(written["takeaway_text"])[:160]}, 2.5)

    practice_prompt = str(written.get("practice_prompt") or "").strip()
    if practice_prompt:
        add("practice", {"type": "practice", "prompt": practice_prompt[:200]}, 2.5)

    concept_id = spec.get("concept_id")
    document = {
        "schema_version": "1.0",
        "id": meal_id,
        "title": str(written.get("title") or spec.get("title") or "")[:80],
        "concept": concept_id or "python.project.overview",
        "objective": str(written.get("objective") or spec.get("objective") or "")[:200],
        "difficulty": "beginner",
        "prerequisites": taxonomy.prerequisites(concept_id) if concept_id else [],
        "next_concepts": [],
        "source": {"kind": "lecture"},
        "voice": {"script": script, "voice_id": "en-US-BrianNeural", "rate": "+6%"},
        "captions": {"enabled": True, "words_per_line": 5,
                     "highlight_active_word": True},
        "scenes": scenes,
        "render": {"width": 1080, "height": 1920, "fps": 30},
    }

    if practice_prompt:
        document["practice"] = {
            "kind": "write_code",
            "prompt": practice_prompt,
            **({"hint": str(written["practice_hint"])}
               if written.get("practice_hint") else {}),
        }

    return document


# ─────────────────────────────────────────────────────────────────────────
# ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────

def link_series(documents: list[dict], plan: dict) -> None:
    """Wire the Meals to each other.

    A Meal series is not a playlist of unrelated clips: `next_concepts` is what
    lets the feed say what comes next, and it is filled from the SERIES order
    rather than from the taxonomy, because the lecture's own ordering is the
    better teacher here.
    """
    for i, doc in enumerate(documents):
        following = documents[i + 1:i + 3]
        doc["next_concepts"] = [d["concept"] for d in following if d.get("concept")]
        doc["series"] = {
            "title": plan.get("series_title"),
            "artifact": plan.get("artifact"),
            "order": i + 1,
            "total": len(documents),
            "previous_id": documents[i - 1]["id"] if i > 0 else None,
            "next_id": documents[i + 1]["id"] if i + 1 < len(documents) else None,
        }


def _existing_document(spec: dict) -> dict | None:
    """A previously authored Meal for this plan entry, if one is on disk.

    Matched on the deterministic id, which is derived from order and title, so
    a re-planned series with different titles correctly does not match."""
    meal_id = f"meal_{spec.get('order'):02d}_{_slug(spec.get('title') or '')}"
    path = CATALOGUE_DIR / f"{meal_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())["meal"]
    except Exception:
        return None


def plan_series(source: dict, write: bool = True, limit: int | None = None,
                fresh: bool = False, resume: bool = True) -> dict:
    analysis = comprehend(source, use_cache=not fresh)

    if not analysis.get("is_programming", True):
        raise SystemExit(
            "[planner] This does not look like a programming lecture.\n"
            f"  {analysis.get('summary', '')[:300]}\n"
            "  MAROS is Python-first; Meals are not generated for other subjects yet."
        )

    plan = build_curriculum(source, analysis, use_cache=not fresh)
    specs = plan.get("meals", [])
    if limit:
        specs = specs[:limit]

    print(f"[planner] Pass 3 — authoring {len(specs)} Meals...")
    documents, prior, failures = [], [], []
    for spec in specs:
        # Resume. Authoring is rate-limited and a long series can lose Meals
        # to the per-minute ceiling; re-running should fill the gaps, not
        # re-pay for the Meals that already succeeded.
        existing = _existing_document(spec)
        if resume and existing is not None:
            print(f"  [{spec.get('order')}/{len(specs)}] {spec.get('title')} "
                  f"— already written, skipping")
            documents.append(existing)
            prior.append({"order": spec.get("order"), "title": spec.get("title"),
                          "objective": spec.get("objective")})
            continue
        try:
            document = author_meal(plan, spec, prior, len(specs))
            # Written immediately, not at the end of the run. Authoring is
            # rate-limited and a long series routinely loses calls to the
            # per-minute ceiling; batching the writes meant a run that died on
            # Meal 15 threw away the fourteen that had succeeded.
            if write:
                (CATALOGUE_DIR / f"{document['id']}.json").write_text(
                    json.dumps({"meal": document}, indent=2) + "\n")
            documents.append(document)
            prior.append({
                "order": spec.get("order"),
                "title": spec.get("title"),
                "objective": spec.get("objective"),
            })
        except Exception as e:
            # One bad Meal must not lose the other fourteen.
            print(f"      FAILED: {e}")
            failures.append({"order": spec.get("order"),
                             "title": spec.get("title"), "error": str(e)})

    link_series(documents, plan)

    if write:
        # Rewrite everything once more: link_series has now filled in each
        # Meal's position and neighbours, which is not known while authoring.
        for doc in documents:
            path = CATALOGUE_DIR / f"{doc['id']}.json"
            path.write_text(json.dumps({"meal": doc}, indent=2) + "\n")
        print(f"[planner] wrote {len(documents)} Meals to {CATALOGUE_DIR}")

    result = {
        "source_title": source["title"],
        "job_id": source.get("job_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "analysis": analysis,
        "plan": plan,
        "meal_ids": [d["id"] for d in documents],
        "failures": failures,
    }
    if write:
        sidecar = BUILD_DIR / f"series_{_slug(source['title'])}.json"
        sidecar.write_text(json.dumps(result, indent=2) + "\n")
        print(f"[planner] plan sidecar -> {sidecar.name}")

    print()
    print(f"[planner] {len(documents)} Meals authored"
          + (f", {len(failures)} failed" if failures else ""))
    print("[planner] NEXT — nothing is publishable until the code actually runs:")
    print("    python3 verify.py catalogue/meal_*.json")
    print("    python3 validate.py catalogue/meal_*.json")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Turn a lecture into a Meal series.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--job", help="chipper job id from outputs/")
    src.add_argument("--transcript", help="path to a plain-text transcript")
    ap.add_argument("--title", default="Lecture", help="title, with --transcript")
    ap.add_argument("--pass1-only", action="store_true",
                    help="comprehend and print the analysis, author nothing")
    ap.add_argument("--plan-only", action="store_true",
                    help="comprehend and design the series, author nothing")
    ap.add_argument("--limit", type=int, help="author only the first N Meals")
    ap.add_argument("--no-resume", action="store_true",
                    help="re-author Meals that already exist on disk")
    ap.add_argument("--fresh", action="store_true",
                    help="re-read the lecture instead of reusing the cached analysis")
    args = ap.parse_args()

    source = (load_from_job(args.job) if args.job
              else load_from_transcript(Path(args.transcript), args.title))

    print(f"[planner] source: {source['title']}")
    print(f"[planner] {len(source['segments'])} segment(s), "
          f"{len(full_text(source).split())} words")

    if args.pass1_only:
        print(json.dumps(comprehend(source, use_cache=not args.fresh), indent=2))
        return 0

    if args.plan_only:
        analysis = comprehend(source, use_cache=not args.fresh)
        print(json.dumps(build_curriculum(source, analysis,
                                        use_cache=not args.fresh), indent=2))
        return 0

    plan_series(source, limit=args.limit, fresh=args.fresh,
                resume=not args.no_resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
