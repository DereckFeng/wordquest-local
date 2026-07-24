#!/usr/bin/env python3
"""Test large-v3-turbo against failed lessons without changing production audio.

The pilot is deliberately read-only with respect to public/raz-audio.  It saves
word timestamps and proposed sentence ranges after every lesson so interrupted
runs can resume without repeating expensive transcription.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from segment_raz_audio import (
    align_words,
    detect_silence,
    find_ffmpeg,
    load_whisper_model,
    sentence_ranges,
    transcript_words,
    transcribe_words,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tmp/mfa-pilot/manifest.json"
LIBRARY = ROOT / "data/raz-course-library.json"
MODEL = ROOT / "models/faster-whisper-large-v3-turbo"
WORK = ROOT / "tmp/turbo-anchor-pilot"
REPORT = ROOT / "data/raz-audio-turbo-pilot-report.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--library", type=Path, default=LIBRARY)
    parser.add_argument("--model", type=Path, default=MODEL)
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--lesson-ids", nargs="*", default=[])
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def offsets_for(sentences: list[dict]) -> tuple[list[str], list[tuple[int, int]]]:
    expected: list[str] = []
    offsets: list[tuple[int, int]] = []
    for sentence in sentences:
        start = len(expected)
        expected.extend(transcript_words(sentence["english"]))
        offsets.append((start, len(expected)))
    return expected, offsets


def analyse_lesson(
    row: dict, lesson: dict, model, ffmpeg: str
) -> dict:
    source = ROOT / row["sourceAudio"]
    started = time.monotonic()
    words, duration = transcribe_words(source, model)
    silences = detect_silence(ffmpeg, source, -35, 0.20)
    expected, offsets = offsets_for(lesson["sentences"])
    mapping = align_words(expected, words)

    diagnostics = []
    missing = []
    for index, (start, end) in enumerate(offsets, start=1):
        matched = [mapping[position] for position in range(start, end) if position in mapping]
        total = max(1, end - start)
        if not matched:
            missing.append(index)
        diagnostics.append({
            "sentenceId": lesson["sentences"][index - 1]["id"],
            "english": lesson["sentences"][index - 1]["english"],
            "matchedWords": len(matched),
            "expectedWords": total,
            "matchRatio": round(len(matched) / total, 4),
        })

    ranges = []
    range_error = None
    if not missing:
        try:
            proposed, range_diagnostics = sentence_ranges(
                lesson["sentences"], words, silences, duration, 2.0
            )
            for sentence, bounds, diagnostic in zip(
                lesson["sentences"], proposed, range_diagnostics
            ):
                ranges.append({
                    "sentenceId": sentence["id"],
                    "english": sentence["english"],
                    "start": bounds[0],
                    "end": bounds[1],
                    "duration": round(bounds[1] - bounds[0], 3),
                    **diagnostic,
                })
        except Exception as error:  # keep the rest of the pilot resumable
            range_error = str(error)

    return {
        "lessonId": row["lessonId"],
        "level": row["level"],
        "title": row["title"],
        "sourceAudio": row["sourceAudio"],
        "sentenceCount": len(lesson["sentences"]),
        "expectedWordCount": len(expected),
        "recognizedWordCount": len(words),
        "matchedWordCount": len(mapping),
        "matchedWordRatio": round(len(mapping) / max(1, len(expected)), 4),
        "missingSentenceAnchors": missing,
        "allSentenceAnchorsRecovered": not missing and not range_error,
        "sourceDuration": round(duration, 3),
        "runtimeSeconds": round(time.monotonic() - started, 2),
        "rangeError": range_error,
        "sentences": diagnostics,
        "ranges": ranges,
        "words": [
            {
                "text": word.text,
                "normalized": word.normalized,
                "start": round(word.start, 3),
                "end": round(word.end, 3),
                "probability": round(word.probability, 4),
            }
            for word in words
        ],
    }


def build_report(results: list[dict], model: Path) -> dict:
    completed = len(results)
    recovered = sum(row["allSentenceAnchorsRecovered"] for row in results)
    sentences = sum(row["sentenceCount"] for row in results)
    anchored = sum(
        row["sentenceCount"] - len(row["missingSentenceAnchors"])
        for row in results
    )
    expected_words = sum(row["expectedWordCount"] for row in results)
    matched_words = sum(row["matchedWordCount"] for row in results)
    return {
        "format": "wordquest-turbo-anchor-pilot",
        "version": 1,
        "productionIndexModified": False,
        "model": str(model.relative_to(ROOT) if model.is_relative_to(ROOT) else model),
        "lessonCount": completed,
        "recoveredLessonCount": recovered,
        "recoveredLessonRate": round(recovered / max(1, completed), 4),
        "sentenceCount": sentences,
        "anchoredSentenceCount": anchored,
        "anchoredSentenceRate": round(anchored / max(1, sentences), 4),
        "expectedWordCount": expected_words,
        "matchedWordCount": matched_words,
        "matchedWordRate": round(matched_words / max(1, expected_words), 4),
        "runtimeSeconds": round(sum(row["runtimeSeconds"] for row in results), 2),
        "lessons": [
            {key: value for key, value in row.items() if key not in {"words", "ranges", "sentences"}}
            for row in results
        ],
    }


def main() -> None:
    args = parse_args()
    pilot = json.loads(args.manifest.read_text(encoding="utf-8"))["lessons"]
    selected = [row for row in pilot if row["reason"] == "missing-timestamp-anchor"]
    if args.lesson_ids:
        requested = set(args.lesson_ids)
        selected = [row for row in selected if row["lessonId"] in requested]
    if args.limit:
        selected = selected[: args.limit]
    lessons = json.loads(args.library.read_text(encoding="utf-8"))["lessons"]
    lesson_by_id = {row["id"]: row for row in lessons}
    args.work.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    pending: list[dict] = []
    for row in selected:
        cache = args.work / f'{row["lessonId"]}.json'
        if cache.is_file() and not args.force:
            results.append(json.loads(cache.read_text(encoding="utf-8")))
        else:
            pending.append(row)

    model = load_whisper_model(args.model) if pending else None
    ffmpeg = find_ffmpeg(None)
    for number, row in enumerate(pending, start=1):
        lesson_id = row["lessonId"]
        try:
            result = analyse_lesson(row, lesson_by_id[lesson_id], model, ffmpeg)
        except Exception as error:
            result = {
                "lessonId": lesson_id,
                "level": row["level"],
                "title": row["title"],
                "sourceAudio": row["sourceAudio"],
                "sentenceCount": len(lesson_by_id[lesson_id]["sentences"]),
                "expectedWordCount": sum(
                    len(transcript_words(sentence["english"]))
                    for sentence in lesson_by_id[lesson_id]["sentences"]
                ),
                "recognizedWordCount": 0,
                "matchedWordCount": 0,
                "matchedWordRatio": 0.0,
                "missingSentenceAnchors": list(
                    range(1, len(lesson_by_id[lesson_id]["sentences"]) + 1)
                ),
                "allSentenceAnchorsRecovered": False,
                "sourceDuration": math.nan,
                "runtimeSeconds": 0.0,
                "rangeError": str(error),
                "sentences": [],
                "ranges": [],
                "words": [],
            }
        cache = args.work / f"{lesson_id}.json"
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results.append(result)
        results.sort(key=lambda item: next(
            index for index, selected_row in enumerate(selected)
            if selected_row["lessonId"] == item["lessonId"]
        ))
        report = build_report(results, args.model)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({
            "progress": f"{len(results)}/{len(selected)}",
            "lessonId": lesson_id,
            "recovered": result["allSentenceAnchorsRecovered"],
            "missing": result["missingSentenceAnchors"],
            "matchedWordRatio": result["matchedWordRatio"],
            "runtimeSeconds": result["runtimeSeconds"],
        }, ensure_ascii=False), flush=True)

    report = build_report(results, args.model)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
