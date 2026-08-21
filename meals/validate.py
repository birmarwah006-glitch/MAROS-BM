"""
Validate Meal JSON against the canonical schema, plus the rules a JSON Schema
cannot express.

    python3 validate.py catalogue/*.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

SCHEMA = json.loads((Path(__file__).parent / "schema" / "meal.schema.json").read_text())


def semantic_checks(meal: dict) -> list[str]:
    """Rules the schema cannot state, but which keep Meals honest."""
    errors: list[str] = []
    script = meal["voice"]["script"]

    def check_anchor(anchor: str, where: str) -> None:
        count = script.count(anchor)
        if count == 0:
            errors.append(f"{where}: narration_anchor not found in voice.script: {anchor!r}")
        elif count > 1:
            errors.append(f"{where}: narration_anchor appears {count} times, must be unique: {anchor!r}")

    for i, scene in enumerate(meal["scenes"]):
        where = f"scene {i} ({scene['beat']})"
        if "narration_anchor" in scene:
            check_anchor(scene["narration_anchor"], where)

        visual = scene["visual"]

        if visual["type"] == "code_editor":
            code = visual["code"]
            line_count = len(code.rstrip("\n").split("\n"))
            for j, action in enumerate(visual.get("actions", [])):
                aw = f"{where} action {j} ({action['action']})"
                if "narration_anchor" in action:
                    check_anchor(action["narration_anchor"], aw)
                # A `type` action must type code that genuinely exists.
                if action["action"] == "type" and action.get("text"):
                    if action["text"] not in code:
                        errors.append(f"{aw}: typed text is not a substring of `code`")
                for ln in action.get("lines", []):
                    if ln > line_count:
                        errors.append(f"{aw}: line {ln} is beyond the {line_count}-line snippet")

        if visual["type"] == "text":
            for em in visual.get("emphasis", []):
                if em not in visual["text"]:
                    errors.append(f"{where}: emphasis {em!r} is not a substring of text")

        if visual["type"] == "flow":
            ids = {n["id"] for n in visual["nodes"]}
            for edge in visual.get("edges", []):
                for end in ("from", "to"):
                    if edge[end] not in ids:
                        errors.append(f"{where}: edge {end}={edge[end]!r} has no matching node")

        if visual["type"] == "sequence":
            n = len(visual["items"])
            for step in visual.get("steps", []):
                if step["index"] >= n:
                    errors.append(f"{where}: step index {step['index']} is out of range for {n} items")

        # The product rule, enforced.
        if visual["type"] == "terminal":
            ex = visual["execution"]
            if not ex["verified"] or ex["source"] == "unverified":
                errors.append(
                    f"{where}: execution is NOT verified — run verify.py. "
                    "A Meal must never claim code executed if it did not."
                )

    beats = [s["beat"] for s in meal["scenes"]]
    if beats and beats[0] != "hook":
        errors.append("every Meal must open on a hook")

    return errors


def main() -> int:
    paths = [Path(a) for a in sys.argv[1:]]
    if not paths:
        print(__doc__.strip())
        return 1

    failed = False
    for path in paths:
        doc = json.loads(path.read_text())
        problems: list[str] = []
        try:
            jsonschema.validate(doc["meal"], SCHEMA)
        except jsonschema.ValidationError as e:
            problems.append(f"schema: {'/'.join(str(p) for p in e.absolute_path)}: {e.message}")

        if not problems:
            problems = semantic_checks(doc["meal"])

        if problems:
            failed = True
            print(f"{path.name}: {len(problems)} problem(s)")
            for p in problems:
                print(f"  {p}")
        else:
            meal = doc["meal"]
            print(f"{path.name}: ok — {len(meal['scenes'])} scenes, "
                  f"{len(meal['voice']['script'].split())} spoken words")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
