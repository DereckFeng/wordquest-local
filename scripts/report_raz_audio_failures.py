#!/usr/bin/env python3
"""Summarize the final RAZ audio batch state for review."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/raz-audio-batch-state.json"
OUTPUT = ROOT / "data/raz-audio-failure-report.json"


def reason(error: str) -> str:
    if "No timestamp anchor" in error:
        return "missing-timestamp-anchor"
    if "post-cut validation failed" in error:
        return "post-cut-validation"
    if "quality gate failed" in error:
        return "quality-gate"
    if "No matching source" in error:
        return "missing-source-audio"
    return "other"


def main() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    rows = state.get("results", {})
    usable = [key for key, row in rows.items() if row.get("status") in {"complete", "skipped-existing"}]
    failures = []
    by_level: dict[str, Counter] = defaultdict(Counter)
    by_reason = Counter()

    for lesson_id, row in sorted(rows.items()):
        level = lesson_id.split("-")[1]
        status = row.get("status", "unknown")
        by_level[level]["usable" if status in {"complete", "skipped-existing"} else status] += 1
        if status == "failed":
            error = row.get("error", "")
            category = reason(error)
            by_reason[category] += 1
            failures.append({"lessonId": lesson_id, "level": level, "reason": category, "error": error})

    report = {
        "format": "wordquest-raz-audio-failure-report",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "usableLessons": len(usable),
            "failedLessons": len(failures),
            "failureReasons": dict(sorted(by_reason.items())),
            "byLevel": {key: dict(value) for key, value in sorted(by_level.items())},
        },
        "failures": failures,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
