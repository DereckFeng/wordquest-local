#!/usr/bin/env python3
"""Run MFA align_one on the risky windows from the 30-lesson pilot."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "tmp/mfa-pilot"
REJECTED = ROOT / "tmp/raz-audio-rejected"
CORPUS = PILOT / "sentence-corpus"
OUTPUT = PILOT / "risky-aligned"
RESULTS = PILOT / "risky-results.json"
MFA = ROOT / ".mfa-env312/bin/mfa"
DICTIONARY = ROOT / "tmp/mfa/pretrained_models/dictionary/english_us_arpa.dict"
ACOUSTIC = ROOT / "tmp/mfa/pretrained_models/acoustic/english_us_arpa.zip"


def risky_ids(lesson_id: str) -> set[str]:
    report = json.loads((REJECTED / lesson_id / "failure-report.json").read_text(encoding="utf-8"))
    quality = report.get("quality", {})
    result = {row["sentenceId"] for row in quality.get("clipValidation", {}).get("failures", [])}
    result.update(quality.get("implausibleClips", []))
    result.update(
        row["sentenceId"] for row in report.get("clips", []) if row.get("confidence", 1.0) < 0.55
    )
    return result


def word_bounds(path: Path) -> tuple[float, float, int] | None:
    text = path.read_text(encoding="utf-8")
    words_section = text.split('name = "words"', 1)[1].split('name = "phones"', 1)[0]
    intervals = re.findall(
        r'xmin = ([0-9.]+)\s+xmax = ([0-9.]+)\s+text = "([^"]*)"', words_section
    )
    words = [(float(start), float(end), label) for start, end, label in intervals if label != "<eps>"]
    if not words:
        return None
    return words[0][0], words[-1][1], len(words)


def main() -> None:
    manifest = json.loads((PILOT / "sentence-manifest.json").read_text(encoding="utf-8"))
    selected = [row for row in manifest["windows"] if row["sentenceId"] in risky_ids(row["lessonId"])]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    previous = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {"results": []}
    done = {row["key"] for row in previous["results"] if row.get("status") == "aligned"}
    rows = previous["results"]
    env = os.environ.copy()
    # MFA's third-party probe mishandles quoted absolute executable paths when
    # the project directory contains spaces. Relative PATH entries avoid that.
    env["MFA_ROOT_DIR"] = "tmp/mfa-align-one"
    env["PATH"] = ".mfa-env312/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

    for position, window in enumerate(selected, start=1):
        key = window["key"]
        if key in done:
            continue
        output = OUTPUT / f"{key}.TextGrid"
        command = [
            str(MFA), "align_one", str(CORPUS / f"{key}.wav"), str(CORPUS / f"{key}.lab"),
            str(DICTIONARY), str(ACOUSTIC), str(output), "--overwrite",
        ]
        print(f"[{position}/{len(selected)}] {key}", flush=True)
        completed = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
        bounds = word_bounds(output) if completed.returncode == 0 and output.exists() else None
        row = dict(window)
        if bounds:
            local_start, local_end, word_count = bounds
            row.update({
                "status": "aligned",
                "alignedStart": round(window["sourceStart"] + local_start, 3),
                "alignedEnd": round(window["sourceStart"] + local_end, 3),
                "startShift": round(window["sourceStart"] + local_start - window["coarseStart"], 3),
                "endShift": round(window["sourceStart"] + local_end - window["coarseEnd"], 3),
                "alignedWords": word_count,
            })
        else:
            row.update({"status": "failed", "exitCode": completed.returncode, "error": completed.stderr[-2000:]})
        rows = [old for old in rows if old["key"] != key] + [row]
        RESULTS.write_text(json.dumps({"format": "wordquest-mfa-risky-pilot", "results": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    aligned = [row for row in rows if row.get("status") == "aligned"]
    summary = {
        "selectedWindows": len(selected),
        "alignedWindows": len(aligned),
        "failedWindows": len(selected) - len(aligned),
        "lessonsWithAlignment": len({row["lessonId"] for row in aligned}),
    }
    RESULTS.write_text(json.dumps({"format": "wordquest-mfa-risky-pilot", "summary": summary, "results": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
