#!/usr/bin/env python3
"""Resume-safe local batch segmentation for conservatively matched RAZ audio."""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import shutil
import sys
import time
from pathlib import Path

from qwen_asr_client import QwenAsrClient, QwenAsrError

from segment_raz_audio import (
    cut_clip,
    detect_silence,
    find_ffmpeg,
    load_stable_alignment_model,
    load_whisper_model,
    sentence_ranges,
    stable_align_words,
    transcript_words,
    transcribe_words,
)


class RejectedLessonError(RuntimeError):
    def __init__(self, message: str, report: dict):
        super().__init__(message)
        self.report = report


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path, fallback):
    if not path.is_file():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def valid_existing_lesson(output: Path, sentence_count: int) -> bool:
    manifest_path = output / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = load_json(manifest_path, {})
        clips = manifest.get("clips", [])
        return len(clips) == sentence_count and all(
            (output / f"S{index:03d}.mp3").is_file()
            for index in range(1, sentence_count + 1)
        )
    except (OSError, ValueError):
        return False


def quality_check(clips: list[dict], minimum_confidence: float, maximum_weak_ratio: float) -> dict:
    expected_words = sum(clip["expectedWords"] for clip in clips)
    matched_words = sum(clip["matchedWords"] for clip in clips)
    coverage = matched_words / max(1, expected_words)
    weak = [clip for clip in clips if clip["confidence"] < minimum_confidence]
    weak_ratio = len(weak) / max(1, len(clips))
    implausible = [clip for clip in clips if clip["duration"] < 0.45 or clip["duration"] > 90]
    passed = coverage >= 0.90 and weak_ratio <= maximum_weak_ratio and not implausible
    return {
        "passed": passed,
        "wordCoverage": round(coverage, 4),
        "weakClips": len(weak),
        "weakClipRatio": round(weak_ratio, 4),
        "implausibleClips": [clip["sentenceId"] for clip in implausible],
        "minimumConfidence": minimum_confidence,
        "maximumWeakRatio": maximum_weak_ratio,
    }


def validate_cut_samples(
    clips: list[dict],
    staging: Path,
    model,
    sample_count: int,
    minimum_score: float,
    maximum_failure_ratio: float,
    qwen_client: QwenAsrClient | None = None,
) -> dict:
    count = min(sample_count, len(clips))
    if count <= 0:
        return {"passed": True, "sampledClips": 0, "failures": []}
    if count == 1:
        selected = {0}
    else:
        selected = {round(index * (len(clips) - 1) / (count - 1)) for index in range(count)}
    selected.update(
        sorted(range(len(clips)), key=lambda index: clips[index]["confidence"])[:2]
    )
    results = []
    for index in sorted(selected):
        clip_path = staging / f"S{index + 1:03d}.mp3"
        if qwen_client:
            recognized = qwen_client.transcribe(clip_path)
        else:
            segments, _info = model.transcribe(
                str(clip_path),
                language="en",
                beam_size=5,
                vad_filter=False,
                condition_on_previous_text=False,
            )
            recognized = " ".join(segment.text.strip() for segment in segments).strip()
        expected_normalized = " ".join(transcript_words(clips[index]["english"]))
        recognized_normalized = " ".join(transcript_words(recognized))
        score = difflib.SequenceMatcher(
            None, expected_normalized, recognized_normalized
        ).ratio()
        results.append(
            {
                "sentenceId": clips[index]["sentenceId"],
                "score": round(score, 4),
                "recognized": recognized,
            }
        )
    failures = [result for result in results if result["score"] < minimum_score]
    failure_ratio = len(failures) / len(results)
    mean_score = sum(result["score"] for result in results) / len(results)
    return {
        "passed": failure_ratio <= maximum_failure_ratio and mean_score >= 0.80,
        "sampledClips": len(results),
        "meanScore": round(mean_score, 4),
        "failures": failures,
        "failureRatio": round(failure_ratio, 4),
        "minimumScore": minimum_score,
        "maximumFailureRatio": maximum_failure_ratio,
        "engine": "qwen3-asr" if qwen_client else "faster-whisper",
    }


def segment_one(
    lesson: dict,
    mapping: dict,
    model,
    ffmpeg: str,
    args: argparse.Namespace,
) -> tuple[dict, Path]:
    lesson_id = lesson["id"]
    level = lesson["level"]
    audio = Path(mapping["audio"])
    output = args.output_root / level / lesson_id
    staging = args.staging_root / lesson_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    silences = detect_silence(ffmpeg, audio, args.noise_db, args.minimum_silence)
    if args.alignment_engine == "stable-ts":
        words, duration = stable_align_words(
            audio,
            lesson["sentences"],
            model,
            ffmpeg,
            args.stable_failure_threshold,
        )
    else:
        words, duration = transcribe_words(audio, model)
    ranges, diagnostics = sentence_ranges(
        lesson["sentences"], words, silences, duration, args.snap_window
    )
    if args.alignment_engine == "stable-ts":
        for diagnostic in diagnostics:
            diagnostic["acousticConfidence"] = diagnostic["confidence"]
            diagnostic["confidence"] = diagnostic["matchRatio"]

    clips = []
    url_prefix = f"/raz-audio/{level}/{lesson_id}"
    for index, (sentence, (start, end), diagnostic) in enumerate(
        zip(lesson["sentences"], ranges, diagnostics), start=1
    ):
        filename = f"S{index:03d}.mp3"
        clip = {
            "sentenceId": sentence["id"],
            "english": sentence["english"],
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
            "audioUrl": f"{url_prefix}/{filename}",
            **diagnostic,
        }
        clips.append(clip)

    quality = quality_check(clips, args.minimum_confidence, args.maximum_weak_ratio)
    if not quality["passed"]:
        report = {
            "status": "rejected",
            "reason": "quality gate failed",
            "lessonId": lesson_id,
            "level": level,
            "title": lesson["title"],
            "sourceAudio": str(audio),
            "catalogMatch": {
                "score": mapping["score"],
                "margin": mapping.get("margin"),
                "source": mapping["source"],
            },
            "quality": quality,
            "clips": clips,
        }
        atomic_json(staging / "failure-report.json", report)
        raise RejectedLessonError(
            f"quality gate failed: {json.dumps(quality, ensure_ascii=False)}",
            report,
        )

    for index, ((start, end), _sentence) in enumerate(zip(ranges, lesson["sentences"]), start=1):
        cut_clip(ffmpeg, audio, staging / f"S{index:03d}.mp3", start, end)

    clip_validation = validate_cut_samples(
        clips,
        staging,
        args.validation_model,
        args.validation_samples,
        args.minimum_validation_score,
        args.maximum_validation_failure_ratio,
        args.qwen_client,
    )
    quality["clipValidation"] = clip_validation
    if not clip_validation["passed"]:
        report = {
            "status": "rejected",
            "reason": "post-cut validation failed",
            "lessonId": lesson_id,
            "level": level,
            "title": lesson["title"],
            "sourceAudio": str(audio),
            "catalogMatch": {
                "score": mapping["score"],
                "margin": mapping.get("margin"),
                "source": mapping["source"],
            },
            "quality": quality,
            "clips": clips,
        }
        atomic_json(staging / "failure-report.json", report)
        raise RejectedLessonError(
            f"post-cut validation failed: {json.dumps(clip_validation, ensure_ascii=False)}",
            report,
        )

    manifest = {
        "format": "wordquest-raz-sentence-audio",
        "version": 1,
        "lessonId": lesson_id,
        "level": level,
        "title": lesson["title"],
        "sourceAudio": str(audio),
        "sourceDuration": round(duration, 3),
        "catalogMatch": {
            "score": mapping["score"],
            "margin": mapping.get("margin"),
            "source": mapping["source"],
        },
        "alignment": (
            "exact transcript + stable-ts forced alignment + silence snapping"
            if args.alignment_engine == "stable-ts"
            else "exact transcript + faster-whisper word timestamps + silence snapping"
        ),
        "validationEngine": (
            f"qwen3-asr:{args.qwen_model}" if args.qwen_client else "faster-whisper"
        ),
        "model": str(args.model),
        "quality": quality,
        "clips": clips,
    }
    atomic_json(staging / "manifest.json", manifest)
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(output)
    return manifest, output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path("data/raz-course-library.json"))
    parser.add_argument("--catalog", type=Path, default=Path("data/raz-audio-catalog.json"))
    parser.add_argument("--model", type=Path, default=Path("models/faster-whisper-base.en"))
    parser.add_argument(
        "--alignment-engine",
        choices=("faster-whisper", "stable-ts"),
        default="faster-whisper",
    )
    parser.add_argument("--stable-failure-threshold", type=float, default=0.40)
    parser.add_argument("--output-root", type=Path, default=Path("public/raz-audio"))
    parser.add_argument("--staging-root", type=Path, default=Path("tmp/raz-audio-staging"))
    parser.add_argument("--rejected-root", type=Path, default=Path("tmp/raz-audio-rejected"))
    parser.add_argument("--index", type=Path, default=Path("public/raz-audio/index.json"))
    parser.add_argument("--state", type=Path, default=Path("data/raz-audio-batch-state.json"))
    parser.add_argument("--levels", nargs="*", default=[])
    parser.add_argument("--lesson-ids", nargs="*", default=[])
    parser.add_argument("--lesson-ids-file", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--minimum-title-score", type=float, default=0.84)
    parser.add_argument("--minimum-confidence", type=float, default=0.55)
    parser.add_argument("--maximum-weak-ratio", type=float, default=0.15)
    parser.add_argument("--validation-samples", type=int, default=8)
    parser.add_argument("--minimum-validation-score", type=float, default=0.72)
    parser.add_argument("--maximum-validation-failure-ratio", type=float, default=0.0)
    parser.add_argument("--noise-db", type=int, default=-35)
    parser.add_argument("--minimum-silence", type=float, default=0.20)
    parser.add_argument("--snap-window", type=float, default=2.0)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-failed", action="store_true")
    parser.add_argument(
        "--validation-engine",
        choices=("faster-whisper", "qwen3-asr"),
        default="faster-whisper",
    )
    parser.add_argument("--qwen-server-url", default="http://192.168.18.12:1234")
    parser.add_argument("--qwen-model", default="qwen3-asr-1.7b")
    parser.add_argument("--qwen-timeout", type=float, default=300.0)
    parser.add_argument("--qwen-probe-audio", type=Path, default=Path("tmp/qwen-asr-probe-1s.wav"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library = load_json(args.library, {})
    catalog = load_json(args.catalog, {})
    lessons = {lesson["id"]: lesson for lesson in library["lessons"]}
    selected_levels = {level.upper() for level in args.levels}
    selected_ids = set(args.lesson_ids)
    if args.lesson_ids_file:
        selected_ids.update(
            line.strip()
            for line in args.lesson_ids_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    mappings = [
        mapping
        for mapping in catalog["mappings"]
        if mapping["score"] >= args.minimum_title_score
        and (not selected_levels or mapping["level"] in selected_levels)
        and (not selected_ids or mapping["lessonId"] in selected_ids)
    ]
    if args.limit:
        mappings = mappings[: args.limit]
    if not mappings:
        print("No catalog mappings selected.", file=sys.stderr)
        return 2

    args.qwen_client = None
    if args.validation_engine == "qwen3-asr":
        args.qwen_client = QwenAsrClient(
            args.qwen_server_url, args.qwen_model, args.qwen_timeout
        )
        probe_audio = args.qwen_probe_audio
        if not probe_audio.is_file():
            probe_audio = Path(mappings[0]["audio"])
        print(
            f"Checking Qwen3-ASR audio input at {args.qwen_client.endpoint} with {probe_audio} ...",
            flush=True,
        )
        try:
            probe_text = args.qwen_client.transcribe(probe_audio)
        except QwenAsrError as error:
            print(f"Qwen3-ASR preflight failed: {error}", file=sys.stderr)
            print(
                "Start an ASR server that accepts OpenAI audio_url input, then retry; no audio index was changed.",
                file=sys.stderr,
            )
            return 2
        print(f"Qwen3-ASR preflight passed: {probe_text!r}", flush=True)

    state = load_json(
        args.state,
        {
            "format": "wordquest-raz-audio-batch-state",
            "version": 1,
            "startedAt": now(),
            "results": {},
        },
    )
    audio_index = load_json(args.index, {})
    ffmpeg = find_ffmpeg(args.ffmpeg)
    print(
        f"Loading local model {args.model} with {args.alignment_engine} alignment ...",
        flush=True,
    )
    model = (
        load_stable_alignment_model(args.model)
        if args.alignment_engine == "stable-ts"
        else load_whisper_model(args.model)
    )
    args.validation_model = (
        load_whisper_model(args.model)
        if args.alignment_engine == "stable-ts"
        else model
    )
    run_started = time.monotonic()

    for position, mapping in enumerate(mappings, start=1):
        lesson_id = mapping["lessonId"]
        lesson = lessons[lesson_id]
        output = args.output_root / lesson["level"] / lesson_id
        if not args.force and valid_existing_lesson(output, len(lesson["sentences"])):
            manifest = load_json(output / "manifest.json", {})
            for clip in manifest.get("clips", []):
                audio_index[clip["sentenceId"]] = clip["audioUrl"]
            atomic_json(args.index, dict(sorted(audio_index.items())))
            state["results"][lesson_id] = {
                "status": "skipped-existing", "updatedAt": now(), "clips": len(lesson["sentences"])
            }
            atomic_json(args.state, state)
            print(f"[{position}/{len(mappings)}] {lesson_id} already complete", flush=True)
            continue

        if (
            args.skip_failed
            and not args.force
            and state.get("results", {}).get(lesson_id, {}).get("status") == "failed"
        ):
            print(f"[{position}/{len(mappings)}] {lesson_id} already failed; skipped", flush=True)
            continue

        started = time.monotonic()
        print(
            f"[{position}/{len(mappings)}] {lesson_id} {lesson['title']} <- {Path(mapping['audio']).name}",
            flush=True,
        )
        try:
            manifest, _output = segment_one(lesson, mapping, model, ffmpeg, args)
            for clip in manifest["clips"]:
                audio_index[clip["sentenceId"]] = clip["audioUrl"]
            atomic_json(args.index, dict(sorted(audio_index.items())))
            state["results"][lesson_id] = {
                "status": "complete",
                "updatedAt": now(),
                "seconds": round(time.monotonic() - started, 2),
                "clips": len(manifest["clips"]),
                "quality": manifest["quality"],
            }
            print(
                f"  complete: {len(manifest['clips'])} clips, coverage {manifest['quality']['wordCoverage']:.1%}",
                flush=True,
            )
        except Exception as error:
            for sentence in lesson["sentences"]:
                audio_index.pop(sentence["id"], None)
            atomic_json(args.index, dict(sorted(audio_index.items())))
            rejected = args.rejected_root / lesson_id
            if rejected.exists():
                shutil.rmtree(rejected)
            rejected.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                output.replace(rejected)
            elif (args.staging_root / lesson_id).exists():
                (args.staging_root / lesson_id).replace(rejected)
            else:
                rejected.mkdir(parents=True, exist_ok=True)
            failure_report = {
                "status": "failed",
                "updatedAt": now(),
                "lessonId": lesson_id,
                "level": lesson["level"],
                "title": lesson["title"],
                "sourceAudio": mapping["audio"],
                "error": str(error),
            }
            if isinstance(error, RejectedLessonError):
                failure_report.update(error.report)
                failure_report["updatedAt"] = now()
                failure_report["error"] = str(error)
            atomic_json(rejected / "failure-report.json", failure_report)
            state["results"][lesson_id] = {
                "status": "failed", "updatedAt": now(),
                "seconds": round(time.monotonic() - started, 2), "error": str(error),
            }
            print(f"  FAILED: {error}", file=sys.stderr, flush=True)
        state["updatedAt"] = now()
        atomic_json(args.state, state)

    complete = sum(1 for row in state["results"].values() if row["status"] in {"complete", "skipped-existing"})
    failed = sum(1 for row in state["results"].values() if row["status"] == "failed")
    print(
        f"Run finished in {(time.monotonic() - run_started) / 60:.1f} min; complete={complete}, failed={failed}",
        flush=True,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
