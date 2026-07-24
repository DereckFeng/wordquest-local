#!/usr/bin/env python3
"""Cut turbo pilot candidates and independently validate every sentence clip.

Outputs stay under tmp/turbo-anchor-pilot and never modify the production index.
The alignment model is large-v3-turbo; validation deliberately uses base.en.
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path

from segment_raz_audio import cut_clip, find_ffmpeg, load_whisper_model, transcript_words


ROOT = Path(__file__).resolve().parents[1]
PILOT_REPORT = ROOT / "data/raz-audio-turbo-pilot-report.json"
WORK = ROOT / "tmp/turbo-anchor-pilot"
MODEL = ROOT / "models/faster-whisper-base.en"
REPORT = ROOT / "data/raz-audio-turbo-validation-report.json"
MINIMUM_SCORE = 0.72


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-report", type=Path, default=PILOT_REPORT)
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--model", type=Path, default=MODEL)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--lesson-ids", nargs="*", default=[])
    return parser.parse_args()


def normalized(text: str) -> str:
    return " ".join(transcript_words(text))


def main() -> None:
    args = parse_args()
    pilot = json.loads(args.pilot_report.read_text(encoding="utf-8"))
    selected = [row for row in pilot["lessons"] if row["allSentenceAnchorsRecovered"]]
    if args.lesson_ids:
        requested = set(args.lesson_ids)
        selected = [row for row in selected if row["lessonId"] in requested]
    ffmpeg = find_ffmpeg(None)
    pending = []
    results = []
    for row in selected:
        cache = args.work / row["lessonId"] / "validation.json"
        if cache.is_file() and not args.force:
            results.append(json.loads(cache.read_text(encoding="utf-8")))
        else:
            pending.append(row)

    model = load_whisper_model(args.model) if pending else None
    for lesson_position, row in enumerate(pending, start=1):
        detail = json.loads((args.work / f'{row["lessonId"]}.json').read_text(encoding="utf-8"))
        output = args.work / row["lessonId"] / "clips"
        output.mkdir(parents=True, exist_ok=True)
        source = ROOT / detail["sourceAudio"]
        validations = []
        sentences = detail["ranges"]
        expected_values = [normalized(item["english"]) for item in sentences]
        for index, item in enumerate(sentences):
            clip = output / f"S{index + 1:03d}.mp3"
            if args.force or not clip.is_file():
                cut_clip(ffmpeg, source, clip, item["start"], item["end"])
            segments, _ = model.transcribe(
                str(clip), language="en", beam_size=5, vad_filter=False,
                condition_on_previous_text=False,
            )
            recognized = " ".join(segment.text.strip() for segment in segments).strip()
            actual = normalized(recognized)
            expected = expected_values[index]
            score = difflib.SequenceMatcher(None, expected, actual).ratio()
            neighbor_scores = []
            for neighbor in (index - 1, index + 1):
                if 0 <= neighbor < len(expected_values):
                    neighbor_scores.append(
                        difflib.SequenceMatcher(None, expected_values[neighbor], actual).ratio()
                    )
            likely_neighbor = bool(neighbor_scores and max(neighbor_scores) > score + 0.08)
            validations.append({
                "sentenceId": item["sentenceId"],
                "english": item["english"],
                "recognized": recognized,
                "expectedNormalized": expected,
                "recognizedNormalized": actual,
                "score": round(score, 4),
                "exactNormalized": expected == actual,
                "likelyNeighborContamination": likely_neighbor,
                "passed": score >= MINIMUM_SCORE and not likely_neighbor,
                "start": item["start"],
                "end": item["end"],
            })
            print(
                f'[{lesson_position}/{len(pending)} {index + 1}/{len(sentences)}] '
                f'{item["sentenceId"]} score={score:.3f}', flush=True
            )

        failed = [item for item in validations if not item["passed"]]
        exact = sum(item["exactNormalized"] for item in validations)
        result = {
            "lessonId": row["lessonId"],
            "level": row["level"],
            "title": row["title"],
            "clipCount": len(validations),
            "passedClipCount": len(validations) - len(failed),
            "failedClipCount": len(failed),
            "exactNormalizedCount": exact,
            "meanScore": round(
                sum(item["score"] for item in validations) / max(1, len(validations)), 4
            ),
            "allClipsValidated": not failed,
            "clips": validations,
        }
        cache = args.work / row["lessonId"] / "validation.json"
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results.append(result)

    order = {row["lessonId"]: index for index, row in enumerate(selected)}
    results.sort(key=lambda row: order[row["lessonId"]])
    clips = sum(row["clipCount"] for row in results)
    passed = sum(row["passedClipCount"] for row in results)
    exact = sum(row["exactNormalizedCount"] for row in results)
    report = {
        "format": "wordquest-turbo-independent-validation",
        "version": 1,
        "productionIndexModified": False,
        "alignmentModel": "faster-whisper-large-v3-turbo",
        "validationModel": "faster-whisper-base.en",
        "minimumScore": MINIMUM_SCORE,
        "candidateLessonCount": len(results),
        "fullyValidatedLessonCount": sum(row["allClipsValidated"] for row in results),
        "clipCount": clips,
        "passedClipCount": passed,
        "passedClipRate": round(passed / max(1, clips), 4),
        "exactNormalizedCount": exact,
        "exactNormalizedRate": round(exact / max(1, clips), 4),
        "lessons": results,
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in report.items() if key != "lessons"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
