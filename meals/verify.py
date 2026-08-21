"""
Meal execution verifier.

The MAROS product rule is absolute: NEVER CLAIM THAT CODE EXECUTED IF IT DID
NOT ACTUALLY EXECUTE. This script is what makes that enforceable rather than
aspirational.

For every terminal scene in a Meal it takes the code from the nearest
preceding code_editor scene, runs it for real with the scene's stdin, and
writes the captured stdout/stderr/exit_code back into the Meal's `execution`
block with `verified: true` and a timestamp.

If execution fails or is unavailable, `verified` stays false and the Meal is
NOT publishable. Nothing is ever filled in by hand.

    python3 verify.py catalogue/meal_input_output.json
    python3 verify.py catalogue/*.json --check    # verify only, no writes

Executor: local CPython in a temp dir, with a timeout and no network use by
the snippet itself. This is deliberately NOT glot.io — that service is a
third-party dependency which is currently unreachable, and putting it on the
critical path of every render is a risk (see docs/meal-architecture.md).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TIMEOUT_SEC = 10


def run_python(code: str, stdin_lines: list[str],
               files: list[dict] | None = None) -> dict:
    """Execute one snippet and capture exactly what it printed.

    Any fixture files the Meal declares are written alongside the script, so a
    Meal that teaches reading a file can actually read one. Names are filenames
    only; anything with a path separator is refused rather than allowed to
    write outside the sandbox.
    """
    with tempfile.TemporaryDirectory() as tmp:
        for fixture in files or []:
            name = fixture.get("name", "")
            if not name or "/" in name or "\\" in name or name.startswith("."):
                return {
                    "verified": False, "source": "unverified", "stdout": "",
                    "stderr": f"refused unsafe fixture filename: {name!r}",
                    "exit_code": -1,
                }
            (Path(tmp) / name).write_text(fixture.get("content", ""))

        script = Path(tmp) / "main.py"
        script.write_text(code)
        stdin_data = "".join(line + "\n" for line in stdin_lines)
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SEC,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return {
                "verified": False,
                "source": "unverified",
                "stdout": "",
                "stderr": f"Timed out after {TIMEOUT_SEC}s",
                "exit_code": -1,
            }

    return {
        "verified": True,
        "source": "local_sandbox",
        "executed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
    }


def verify_meal(path: Path, write: bool = True) -> bool:
    meal = json.loads(path.read_text())["meal"]
    scenes = meal["scenes"]

    ok = True
    latest_code: str | None = None

    for i, scene in enumerate(scenes):
        visual = scene["visual"]

        if visual["type"] == "code_editor":
            latest_code = visual["code"]
            continue

        if visual["type"] != "terminal":
            continue

        if latest_code is None:
            print(f"  scene {i}: terminal with no preceding code_editor — invalid")
            ok = False
            continue

        stdin = visual.get("stdin", [])
        result = run_python(latest_code, stdin, visual.get("files"))
        visual["execution"] = result

        if result["verified"] and result["exit_code"] == 0:
            shown = result["stdout"].replace("\n", "\\n")
            print(f"  scene {i}: ran ok, exit 0 -> {shown!r}")
        else:
            print(f"  scene {i}: FAILED — {result['stderr'].strip() or 'non-zero exit'}")
            ok = False

    if write and ok:
        doc = json.loads(path.read_text())
        doc["meal"]["scenes"] = scenes
        path.write_text(json.dumps(doc, indent=2) + "\n")

    return ok


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    write = "--check" not in sys.argv[1:]

    if not args:
        print(__doc__.strip())
        return 1

    all_ok = True
    for arg in args:
        path = Path(arg)
        print(f"{path.name}:")
        if not verify_meal(path, write=write):
            all_ok = False

    print("\nall meals verified" if all_ok else "\nSOME MEALS ARE NOT PUBLISHABLE")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
