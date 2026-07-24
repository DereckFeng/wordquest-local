#!/usr/bin/env python3
"""Build a deterministic 30-lesson MFA pilot corpus without touching production audio."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data/raz-audio-failure-report.json"
CATALOG = ROOT / "data/raz-audio-catalog.json"
LIBRARY = ROOT / "data/raz-course-library.json"
WORK = ROOT / "tmp/mfa-pilot"
CORPUS = WORK / "corpus"
FFMPEG = ROOT / ".audio-venv/bin/ffmpeg"
REASONS = ("missing-timestamp-anchor", "post-cut-validation", "quality-gate")


def evenly_spaced(rows: list[dict], count: int) -> list[dict]:
    if len(rows) <= count:
        return rows
    positions = [round(index * (len(rows) - 1) / (count - 1)) for index in range(count)]
    return [rows[position] for position in positions]


def main() -> None:
    failures = json.loads(REPORT.read_text(encoding="utf-8"))["failures"]
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["mappings"]
    lessons = json.loads(LIBRARY.read_text(encoding="utf-8"))["lessons"]
    mapping_by_id = {row["lessonId"]: row for row in catalog}
    lesson_by_id = {row["id"]: row for row in lessons}
    level_order = {letter: index for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}

    selected: list[dict] = []
    for category in REASONS:
        candidates = [
            row for row in failures
            if row["reason"] == category
            and row["lessonId"] in mapping_by_id
            and row["lessonId"] in lesson_by_id
        ]
        candidates.sort(key=lambda row: (
            level_order[row["level"]],
            len(lesson_by_id[row["lessonId"]]["sentences"]),
            row["lessonId"],
        ))
        selected.extend(evenly_spaced(candidates, 10))

    CORPUS.mkdir(parents=True, exist_ok=True)
    manifest = []
    for failure in selected:
        lesson_id = failure["lessonId"]
        lesson = lesson_by_id[lesson_id]
        mapping = mapping_by_id[lesson_id]
        source = ROOT / mapping["audio"]
        wav = CORPUS / f"{lesson_id}.wav"
        lab = CORPUS / f"{lesson_id}.lab"
        text = " ".join(sentence["english"].strip() for sentence in lesson["sentences"])
        subprocess.run([
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
        ], check=True)
        lab.write_text(text + "\n", encoding="utf-8")
        manifest.append({
            "lessonId": lesson_id,
            "level": lesson["level"],
            "title": lesson["title"],
            "reason": failure["reason"],
            "sentenceCount": len(lesson["sentences"]),
            "sourceAudio": mapping["audio"],
            "wav": str(wav.relative_to(ROOT)),
        })

    payload = {
        "format": "wordquest-mfa-pilot",
        "version": 1,
        "count": len(manifest),
        "lessons": manifest,
    }
    (WORK / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"count": len(manifest), "byReason": {
        reason: sum(row["reason"] == reason for row in manifest) for reason in REASONS
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
