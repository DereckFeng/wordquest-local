#!/usr/bin/env python3
"""Split one RAZ lesson recording into sentence clips using the exact course text.

This is deliberately a hybrid aligner:
1. faster-whisper supplies rough word timestamps;
2. dynamic programming aligns those words to the immutable course transcript;
3. cut points are snapped to nearby silence;
4. a manifest records confidence so weak lessons can be reviewed.

The script never rewrites course text.  It is intended for local/offline batch use.
"""

from __future__ import annotations

import argparse
import difflib
import glob
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


NUMBER_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
    "11": "eleven",
    "12": "twelve",
    "13": "thirteen",
    "14": "fourteen",
    "15": "fifteen",
    "16": "sixteen",
    "17": "seventeen",
    "18": "eighteen",
    "19": "nineteen",
    "20": "twenty",
}


@dataclass(frozen=True)
class TimedWord:
    text: str
    normalized: str
    start: float
    end: float
    probability: float


@dataclass(frozen=True)
class Silence:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def midpoint(self) -> float:
        return (self.start + self.end) / 2


def normalize_word(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]", "", value.lower())
    return NUMBER_WORDS.get(cleaned, cleaned)


def transcript_words(value: str) -> list[str]:
    return [
        word
        for token in re.findall(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*", value)
        if (word := normalize_word(token))
    ]


def find_ffmpeg(explicit: str | None) -> str:
    if explicit:
        return explicit
    candidates = sorted(
        glob.glob(
            ".audio-venv/lib/python*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*"
        )
        + glob.glob(
            ".tts-venv/lib/python*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*"
        )
    )
    if candidates:
        return candidates[0]
    from shutil import which

    system_ffmpeg = which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    raise RuntimeError("FFmpeg was not found. Run scripts/install_audio_alignment.sh first.")


def detect_silence(
    ffmpeg: str, audio: Path, noise_db: int, minimum_duration: float
) -> list[Silence]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-i",
        str(audio),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={minimum_duration}",
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    starts = [float(value) for value in re.findall(r"silence_start: ([0-9.]+)", completed.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end: ([0-9.]+)", completed.stderr)]
    return [Silence(start, end) for start, end in zip(starts, ends) if end > start]


def load_whisper_model(model_path: Path):
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "faster-whisper is missing. Run .audio-venv/bin/pip install faster-whisper."
        ) from error

    return WhisperModel(str(model_path), device="cpu", compute_type="int8")


def load_stable_alignment_model(model_path: Path):
    try:
        import stable_whisper
    except ImportError as error:
        raise RuntimeError(
            "stable-ts-whisperless is missing. Run scripts/install_audio_alignment.sh first."
        ) from error

    venv_bin = str(Path(sys.prefix) / "bin")
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if venv_bin not in path_parts:
        os.environ["PATH"] = os.pathsep.join([venv_bin, *path_parts])
    return stable_whisper.load_faster_whisper(
        str(model_path), device="cpu", compute_type="int8"
    )


def audio_duration(ffmpeg: str, audio: Path) -> float:
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(audio)],
        capture_output=True,
        text=True,
    )
    match = re.search(r"Duration: (\d+):(\d+):([0-9.]+)", completed.stderr)
    if not match:
        raise RuntimeError(f"Could not determine audio duration: {audio}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def stable_align_words(
    audio: Path,
    sentences: list[dict],
    model,
    ffmpeg: str,
    failure_threshold: float = 0.40,
) -> tuple[list[TimedWord], float]:
    """Force the immutable lesson text onto audio and retain only acoustic anchors."""

    exact_text = "\n".join(sentence["english"] for sentence in sentences)
    result = model.align(
        str(audio),
        exact_text,
        language="en",
        original_split=True,
        verbose=None,
        suppress_silence=True,
        failure_threshold=failure_threshold,
    )
    if result is None:
        raise RuntimeError("Stable-ts could not align the lesson transcript")
    segments = result.to_dict().get("segments", [])
    if len(segments) != len(sentences):
        raise RuntimeError(
            f"Stable-ts returned {len(segments)} sentence ranges; expected {len(sentences)}"
        )

    words: list[TimedWord] = []
    for segment in segments:
        for word in segment.get("words", []):
            normalized_parts = transcript_words(word.get("word", ""))
            start = float(word.get("start", 0.0))
            end = float(word.get("end", 0.0))
            if not normalized_parts or end - start <= 0.04:
                continue
            probability = float(word.get("probability", 0.0) or 0.0)
            step = (end - start) / len(normalized_parts)
            for index, normalized in enumerate(normalized_parts):
                words.append(
                    TimedWord(
                        text=normalized,
                        normalized=normalized,
                        start=start + step * index,
                        end=start + step * (index + 1),
                        probability=probability,
                    )
                )
    if not words:
        raise RuntimeError("Stable-ts produced no usable acoustic word anchors")
    return words, audio_duration(ffmpeg, audio)


def transcribe_words(audio: Path, model) -> tuple[list[TimedWord], float]:
    segments, info = model.transcribe(
        str(audio),
        language="en",
        beam_size=5,
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=True,
    )
    words: list[TimedWord] = []
    for segment in segments:
        for word in segment.words or []:
            normalized = normalize_word(word.word)
            if normalized:
                words.append(
                    TimedWord(
                        text=word.word.strip(),
                        normalized=normalized,
                        start=float(word.start),
                        end=float(word.end),
                        probability=float(word.probability),
                    )
                )
    return words, float(info.duration)


def similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def align_words(expected: list[str], actual: list[TimedWord]) -> dict[int, int]:
    """Semi-global alignment: unrelated title/credit audio is free at either end."""

    rows = len(expected) + 1
    columns = len(actual) + 1
    costs = [[math.inf] * columns for _ in range(rows)]
    moves: list[list[str | None]] = [[None] * columns for _ in range(rows)]
    for column in range(columns):
        costs[0][column] = 0.0
    for row in range(1, rows):
        costs[row][0] = float(row)
        moves[row][0] = "delete"

    for row in range(1, rows):
        for column in range(1, columns):
            score = similarity(expected[row - 1], actual[column - 1].normalized)
            substitution = (1.0 - score) * 1.25
            if score < 0.5:
                substitution = 1.15
            substitution += (1.0 - actual[column - 1].probability) * 0.04
            choices = (
                (costs[row - 1][column - 1] + substitution, "match"),
                (costs[row][column - 1] + 0.82, "insert"),
                (costs[row - 1][column] + 1.0, "delete"),
            )
            costs[row][column], moves[row][column] = min(choices, key=lambda item: item[0])

    row = len(expected)
    column = min(range(columns), key=lambda item: costs[row][item])
    mapping: dict[int, int] = {}
    while row > 0:
        move = moves[row][column]
        if move == "match":
            if similarity(expected[row - 1], actual[column - 1].normalized) >= 0.5:
                mapping[row - 1] = column - 1
            row -= 1
            column -= 1
        elif move == "insert" and column > 0:
            column -= 1
        else:
            row -= 1
    return mapping


def nearest_silence(
    target: float,
    silences: Iterable[Silence],
    minimum_duration: float,
    snap_window: float,
) -> float:
    candidates = [
        silence
        for silence in silences
        if silence.duration >= minimum_duration
        and abs(silence.midpoint - target) <= snap_window
    ]
    if not candidates:
        return target
    return min(candidates, key=lambda silence: abs(silence.midpoint - target)).midpoint


def sentence_ranges(
    sentences: list[dict],
    words: list[TimedWord],
    silences: list[Silence],
    duration: float,
    snap_window: float,
) -> tuple[list[tuple[float, float]], list[dict]]:
    expected: list[str] = []
    sentence_offsets: list[tuple[int, int]] = []
    for sentence in sentences:
        start = len(expected)
        expected.extend(transcript_words(sentence["english"]))
        sentence_offsets.append((start, len(expected)))

    mapping = align_words(expected, words)
    anchors: list[tuple[float, float, float, int, int]] = []
    diagnostics: list[dict] = []
    for start, end in sentence_offsets:
        matched = [mapping[index] for index in range(start, end) if index in mapping]
        if matched:
            first = words[min(matched)].start
            last = max(words[index].end for index in matched)
            probability = sum(words[index].probability for index in matched) / len(matched)
        else:
            first = last = math.nan
            probability = 0.0
        total = max(1, end - start)
        match_ratio = len(matched) / total
        anchors.append((first, last, probability, len(matched), total))
        diagnostics.append(
            {
                "matchedWords": len(matched),
                "expectedWords": total,
                "matchRatio": round(match_ratio, 4),
                "meanWordProbability": round(probability, 4),
                "confidence": round(match_ratio * probability, 4),
            }
        )

    if any(math.isnan(anchor[0]) for anchor in anchors):
        missing = [index + 1 for index, anchor in enumerate(anchors) if math.isnan(anchor[0])]
        raise RuntimeError(f"No timestamp anchor for sentence(s): {missing}")

    # Build each clip independently.  This intentionally permits gaps: title,
    # credits, captions, questions, or edition-only narration between two body
    # sentences must be discarded instead of being forced into either clip.
    ranges_mutable: list[list[float]] = []
    usable_silences = [silence for silence in silences if silence.duration >= 0.20]
    # Never expand a clip far into a pause.  Long expansion can accidentally
    # pull a title, page caption, or the previous sentence into the clip even
    # though the matched word timestamps themselves are correct.
    edge_snap_window = min(snap_window, 0.45)
    for first, last, _probability, _matched, _total in anchors:
        before = [
            silence
            for silence in usable_silences
            if silence.end <= first + 0.08 and first - silence.midpoint <= edge_snap_window
        ]
        after = [
            silence
            for silence in usable_silences
            if silence.start >= last - 0.08 and silence.midpoint - last <= edge_snap_window
        ]
        start = max(before, key=lambda silence: silence.midpoint).midpoint if before else first - 0.18
        end = min(after, key=lambda silence: silence.midpoint).midpoint if after else last + 0.25
        ranges_mutable.append([max(0.0, start), min(duration, end)])

    # Resolve only genuine overlaps.  Ordinary gaps are kept and therefore
    # remove unmatched narration from the student-facing clips.
    for index in range(len(ranges_mutable) - 1):
        if ranges_mutable[index][1] <= ranges_mutable[index + 1][0]:
            continue
        boundary = (anchors[index][1] + anchors[index + 1][0]) / 2
        ranges_mutable[index][1] = boundary
        ranges_mutable[index + 1][0] = boundary
    ranges = [(round(start, 3), round(end, 3)) for start, end in ranges_mutable]
    return ranges, diagnostics


def cut_clip(ffmpeg: str, audio: Path, output: Path, start: float, end: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(audio),
            "-vn",
            "-af",
            "aformat=sample_fmts=s16:channel_layouts=mono",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(output),
        ],
        check=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lesson-id", required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument(
        "--library", type=Path, default=Path("data/raz-course-library.json")
    )
    parser.add_argument(
        "--model", type=Path, default=Path("models/faster-whisper-base.en")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url-prefix", default="/raz-audio")
    parser.add_argument(
        "--index",
        type=Path,
        default=Path("public/raz-audio/index.json"),
        help="Static sentence-to-audio URL index consumed by the website.",
    )
    parser.add_argument("--ffmpeg")
    parser.add_argument("--noise-db", type=int, default=-35)
    parser.add_argument("--minimum-silence", type=float, default=0.20)
    parser.add_argument("--snap-window", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library = json.loads(args.library.read_text(encoding="utf-8"))
    lesson = next(
        (item for item in library["lessons"] if item["id"] == args.lesson_id), None
    )
    if not lesson:
        raise RuntimeError(f"Unknown lesson: {args.lesson_id}")
    if not args.audio.is_file():
        raise RuntimeError(f"Audio file does not exist: {args.audio}")

    ffmpeg = find_ffmpeg(args.ffmpeg)
    silences = detect_silence(
        ffmpeg, args.audio, args.noise_db, args.minimum_silence
    )
    model = load_whisper_model(args.model)
    words, duration = transcribe_words(args.audio, model)
    ranges, diagnostics = sentence_ranges(
        lesson["sentences"], words, silences, duration, args.snap_window
    )

    clips = []
    for index, (sentence, (start, end), diagnostic) in enumerate(
        zip(lesson["sentences"], ranges, diagnostics), start=1
    ):
        filename = f"S{index:03d}.mp3"
        if not args.dry_run:
            cut_clip(ffmpeg, args.audio, args.output / filename, start, end)
        clips.append(
            {
                "sentenceId": sentence["id"],
                "english": sentence["english"],
                "start": start,
                "end": end,
                "duration": round(end - start, 3),
                "audioUrl": f"{args.url_prefix.rstrip('/')}/{filename}",
                **diagnostic,
            }
        )

    manifest = {
        "format": "wordquest-raz-sentence-audio",
        "version": 1,
        "lessonId": lesson["id"],
        "level": lesson["level"],
        "title": lesson["title"],
        "sourceAudio": str(args.audio),
        "sourceDuration": round(duration, 3),
        "alignment": "exact transcript + faster-whisper word timestamps + silence snapping",
        "model": str(args.model),
        "clips": clips,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not args.dry_run:
        audio_index = {}
        if args.index.is_file():
            audio_index = json.loads(args.index.read_text(encoding="utf-8"))
        audio_index.update({clip["sentenceId"]: clip["audioUrl"] for clip in clips})
        args.index.parent.mkdir(parents=True, exist_ok=True)
        args.index.write_text(
            json.dumps(dict(sorted(audio_index.items())), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    weak = [clip for clip in clips if clip["confidence"] < 0.72]
    if weak:
        print(
            f"WARNING: {len(weak)} clip(s) need review (confidence < 0.72).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
