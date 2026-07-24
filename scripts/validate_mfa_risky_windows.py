#!/usr/bin/env python3
"""Cut MFA-refined risky windows and validate each with faster-whisper."""

from __future__ import annotations

import difflib
import json
from collections import defaultdict
from pathlib import Path

from segment_raz_audio import cut_clip, find_ffmpeg, load_whisper_model, transcript_words


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "tmp/mfa-pilot"
INPUT = PILOT / "risky-results.json"
OUTPUT = PILOT / "mfa-candidates"
REPORT = ROOT / "data/raz-audio-mfa-pilot-report.json"
MODEL = ROOT / "models/faster-whisper-base.en"
MINIMUM_SCORE = 0.72


def main() -> None:
    payload = json.loads(INPUT.read_text(encoding="utf-8"))
    rows = payload["results"]
    model = load_whisper_model(MODEL)
    ffmpeg = find_ffmpeg(None)
    source_by_lesson = {}
    for lesson_id in {row["lessonId"] for row in rows}:
        rejected = json.loads(
            (ROOT / "tmp/raz-audio-rejected" / lesson_id / "failure-report.json").read_text(encoding="utf-8")
        )
        source_by_lesson[lesson_id] = ROOT / rejected["sourceAudio"]

    validated = []
    for position, row in enumerate(rows, start=1):
        result = dict(row)
        if row.get("status") != "aligned":
            result["validationPassed"] = False
            validated.append(result)
            continue
        output = OUTPUT / row["lessonId"] / f'{row["sentenceId"].split("-")[-1]}.mp3'
        start = max(0.0, row["alignedStart"] - 0.10)
        end = row["alignedEnd"] + 0.12
        cut_clip(ffmpeg, source_by_lesson[row["lessonId"]], output, start, end)
        print(f"[{position}/{len(rows)}] {row['sentenceId']}", flush=True)
        segments, _ = model.transcribe(
            str(output), language="en", beam_size=5, vad_filter=False,
            condition_on_previous_text=False,
        )
        recognized = " ".join(segment.text.strip() for segment in segments).strip()
        expected_normalized = " ".join(transcript_words(row["english"]))
        recognized_normalized = " ".join(transcript_words(recognized))
        score = difflib.SequenceMatcher(None, expected_normalized, recognized_normalized).ratio()
        exact_normalized = recognized_normalized == expected_normalized
        result.update({
            "candidateStart": round(start, 3),
            "candidateEnd": round(end, 3),
            "recognized": recognized,
            "validationScore": round(score, 4),
            "exactNormalized": exact_normalized,
            # A similar neighbouring sentence is unsafe for dictation. Require
            # the ASR word sequence to match after case/punctuation removal.
            "validationPassed": exact_normalized,
        })
        validated.append(result)

    by_lesson = defaultdict(list)
    for row in validated:
        by_lesson[row["lessonId"]].append(row)
    lessons = [
        {
            "lessonId": lesson_id,
            "riskWindows": len(items),
            "alignedWindows": sum(item.get("status") == "aligned" for item in items),
            "validatedWindows": sum(item.get("validationPassed", False) for item in items),
            "pilotPassed": all(item.get("validationPassed", False) for item in items),
        }
        for lesson_id, items in sorted(by_lesson.items())
    ]
    summary = {
        "riskWindows": len(validated),
        "alignedWindows": sum(row.get("status") == "aligned" for row in validated),
        "validatedWindows": sum(row.get("validationPassed", False) for row in validated),
        "pilotLessons": len(lessons),
        "pilotPassedLessons": sum(row["pilotPassed"] for row in lessons),
        "minimumScore": MINIMUM_SCORE,
        "requiresExactNormalizedText": True,
    }
    REPORT.write_text(json.dumps({
        "format": "wordquest-mfa-validation-pilot",
        "summary": summary,
        "lessons": lessons,
        "results": validated,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
