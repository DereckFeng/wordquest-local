#!/usr/bin/env python3
"""Promote fully validated turbo pilot lessons into the production audio index."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "data/raz-course-library.json"
INDEX = ROOT / "public/raz-audio/index.json"
STATE = ROOT / "data/raz-audio-batch-state.json"
SOURCES = {
    "RAZ-A-031": (
        ROOT / "tmp/turbo-anchor-pilot",
        ROOT / "data/raz-audio-turbo-validation-report.json",
    ),
    "RAZ-P-053": (
        ROOT / "tmp/turbo-anchor-pilot-p053",
        ROOT / "data/raz-audio-turbo-p053-validation-report.json",
    ),
    "RAZ-U-021": (
        ROOT / "tmp/turbo-anchor-pilot",
        ROOT / "data/raz-audio-turbo-validation-report.json",
    ),
}


def atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> None:
    library = json.loads(LIBRARY.read_text(encoding="utf-8"))["lessons"]
    lesson_by_id = {row["id"]: row for row in library}
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8"))
    promoted = []

    for lesson_id, (work, report_path) in SOURCES.items():
        lesson = lesson_by_id[lesson_id]
        detail = json.loads((work / f"{lesson_id}.json").read_text(encoding="utf-8"))
        validation_report = json.loads(report_path.read_text(encoding="utf-8"))
        validation = next(
            row for row in validation_report["lessons"] if row["lessonId"] == lesson_id
        )
        if not validation["allClipsValidated"]:
            raise RuntimeError(f"candidate has validation failures: {lesson_id}")
        if len(detail["ranges"]) != len(lesson["sentences"]):
            raise RuntimeError(f"candidate/library sentence count differs: {lesson_id}")
        validation_by_id = {row["sentenceId"]: row for row in validation["clips"]}
        clips = []
        destination = ROOT / "public/raz-audio" / lesson["level"] / lesson_id
        if destination.exists():
            raise RuntimeError(f"production destination already exists: {destination}")
        destination.mkdir(parents=True)
        for position, (sentence, aligned) in enumerate(
            zip(lesson["sentences"], detail["ranges"]), start=1
        ):
            if sentence["id"] != aligned["sentenceId"]:
                raise RuntimeError(f"candidate/library sentence ID differs: {sentence['id']}")
            checked = validation_by_id[sentence["id"]]
            filename = f"S{position:03d}.mp3"
            shutil.copy2(work / lesson_id / "clips" / filename, destination / filename)
            audio_url = f"/raz-audio/{lesson['level']}/{lesson_id}/{filename}"
            index[sentence["id"]] = audio_url
            clips.append({
                **aligned,
                "audioUrl": audio_url,
                "validationScore": checked["score"],
                "validationRecognized": checked["recognized"],
            })

        word_coverage = sum(row["matchedWords"] for row in clips) / max(
            1, sum(row["expectedWords"] for row in clips)
        )
        manifest = {
            "format": "wordquest-raz-sentence-audio",
            "version": 1,
            "lessonId": lesson_id,
            "level": lesson["level"],
            "title": lesson["title"],
            "sourceAudio": detail["sourceAudio"],
            "sourceDuration": detail["sourceDuration"],
            "alignment": "immutable transcript + faster-whisper large-v3-turbo timestamps + silence snapping",
            "validationEngine": "independent faster-whisper-base.en; every clip",
            "model": "models/faster-whisper-large-v3-turbo",
            "quality": {
                "passed": True,
                "wordCoverage": round(word_coverage, 4),
                "clipValidation": {
                    "passed": True,
                    "sampledClips": len(clips),
                    "meanScore": validation["meanScore"],
                    "failures": [],
                    "failureRatio": 0.0,
                    "minimumScore": validation_report["minimumScore"],
                    "maximumFailureRatio": 0.0,
                    "engine": "faster-whisper-base.en",
                },
            },
            "clips": clips,
        }
        atomic_json(destination / "manifest.json", manifest)
        state["results"][lesson_id] = {
            "status": "complete",
            "updatedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "clips": len(clips),
            "recoveredBy": "faster-whisper-large-v3-turbo",
            "quality": manifest["quality"],
        }
        promoted.append({"lessonId": lesson_id, "clips": len(clips)})

    state["updatedAt"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_json(INDEX, dict(sorted(index.items())))
    atomic_json(STATE, state)
    print(json.dumps({"promoted": promoted}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
