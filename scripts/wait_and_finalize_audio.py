#!/usr/bin/env python3
"""Wait for the final audio shard, then report and verify the project."""

from __future__ import annotations

import json
import argparse
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/raz-audio-batch-state.json"
RESULT = ROOT / "data/raz-audio-finalization.json"
TARGET_LEVELS = set("UVWXYZ")


def target_ids() -> list[str]:
    ids = (ROOT / "tmp/qwen-retry-lesson-ids.txt").read_text(encoding="utf-8").splitlines()
    return sorted(item for item in ids if item and item.split("-")[1] in TARGET_LEVELS)


def run(name: str, command: list[str], env: dict[str, str] | None = None) -> dict:
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    return {
        "name": name,
        "passed": result.returncode == 0,
        "exitCode": result.returncode,
        "seconds": round(time.monotonic() - started, 2),
        "output": (result.stdout + result.stderr)[-4000:],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-wait", action="store_true",
        help="Run final checks immediately after a manual recovery/promotion.",
    )
    parser.add_argument(
        "--skip-build", action="store_true",
        help="Record the production build as incomplete after a separately observed timeout.",
    )
    args = parser.parse_args()
    started_at = datetime.now().astimezone()
    last_id = target_ids()[-1]
    if not args.no_wait:
        while True:
            state = json.loads(STATE.read_text(encoding="utf-8"))
            updated = state.get("results", {}).get(last_id, {}).get("updatedAt")
            if updated and datetime.fromisoformat(updated) >= started_at:
                break
            time.sleep(30)

    python = str(ROOT / ".audio-venv/bin/python")
    node = "/Users/eva00/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    env = os.environ.copy()
    env["PATH"] = str(Path(node).parent) + ":" + env.get("PATH", "")
    build_check = (
        {
            "name": "production-build",
            "passed": False,
            "exitCode": None,
            "seconds": 0,
            "output": (
                "Skipped after a direct vinext build remained in client asset "
                "generation for more than 15 minutes while copying the 4 GB "
                "public/raz-audio tree. Code compilation stages 1-3 passed."
            ),
        }
        if args.skip_build
        else run("production-build", ["node_modules/.bin/vinext", "build"], env)
    )
    checks = [
        run("failure-report", [python, "scripts/report_raz_audio_failures.py"]),
        run("python-compile", [
            python, "-m", "py_compile",
            "scripts/segment_raz_audio.py", "scripts/batch_segment_raz_audio.py",
            "scripts/report_raz_audio_failures.py", "scripts/run_turbo_anchor_pilot.py",
            "scripts/validate_turbo_anchor_pilot.py",
            "scripts/promote_turbo_audio_candidates.py",
            "scripts/repair_raz_verified_text.py",
        ]),
        run("rendered-html-tests", [node, "--test", "tests/rendered-html.test.mjs"]),
        build_check,
        run("diff-check", ["git", "diff", "--check"]),
    ]
    payload = {
        "format": "wordquest-raz-audio-finalization",
        "finishedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "lastLesson": last_id,
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(0 if payload["passed"] else 1)


if __name__ == "__main__":
    main()
