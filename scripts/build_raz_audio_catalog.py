#!/usr/bin/env python3
"""Build a conservative RAZ lesson-to-recording catalog.

Only unique, high-confidence title matches are accepted automatically.  The
result is an input to audio segmentation, never a source of course text.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import json
import re
import unicodedata
from pathlib import Path


AUDIO_SUFFIXES = {".mp3", ".mp4"}


def level_from_path(path: Path) -> str:
    match = re.search(r"/([A-Z])级", path.as_posix(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Cannot determine RAZ level from {path}")
    return match.group(1).upper()


def audio_title(path: Path) -> str:
    value = unicodedata.normalize("NFKC", path.stem).strip()
    value = re.sub(r"^[A-Z]\s*[-_ ]\s*\d{1,4}\s*[-_. ]*", "", value, flags=re.I)
    value = re.sub(r"^\d{1,4}\s*[-_. ]*", "", value)
    return value.strip(" -_.")


def normalized_tokens(value: str) -> list[str]:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    tokens = re.findall(r"[a-z0-9]+", value.lower().replace("&", " and "))
    if len(tokens) % 2 == 0 and tokens[: len(tokens) // 2] == tokens[len(tokens) // 2 :]:
        tokens = tokens[: len(tokens) // 2]
    return tokens


def title_score(left: str, right: str) -> float:
    left_tokens = normalized_tokens(left)
    right_tokens = normalized_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    left_joined = "".join(left_tokens)
    right_joined = "".join(right_tokens)
    character_score = difflib.SequenceMatcher(None, left_joined, right_joined).ratio()
    left_count = collections.Counter(left_tokens)
    right_count = collections.Counter(right_tokens)
    common = sum((left_count & right_count).values())
    token_score = 2 * common / (len(left_tokens) + len(right_tokens))
    containment = common / min(len(left_tokens), len(right_tokens))
    if containment == 1 and min(len(left_tokens), len(right_tokens)) >= 2:
        containment_score = 0.9 + 0.1 * min(len(left_tokens), len(right_tokens)) / max(
            len(left_tokens), len(right_tokens)
        )
    else:
        containment_score = containment * 0.88
    return max(character_score, token_score, containment_score)


def load_overrides(path: Path) -> dict[str, str | None]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Audio overrides must be a JSON object keyed by lesson ID")
    return value


def build_catalog(args: argparse.Namespace) -> dict:
    library = json.loads(args.library.read_text(encoding="utf-8"))
    lessons = library["lessons"]
    audio_files = sorted(
        path
        for path in args.audio_root.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
    )
    by_level_lessons: dict[str, list[dict]] = collections.defaultdict(list)
    by_level_audio: dict[str, list[Path]] = collections.defaultdict(list)
    for lesson in lessons:
        by_level_lessons[lesson["level"]].append(lesson)
    for audio in audio_files:
        by_level_audio[level_from_path(audio)].append(audio)

    overrides = load_overrides(args.overrides)
    mappings: list[dict] = []
    rejected: list[dict] = []
    used_audio: set[str] = set()
    mapped_lessons: set[str] = set()

    for level in sorted(by_level_lessons):
        level_lessons = by_level_lessons[level]
        level_audio = by_level_audio[level]
        scores: dict[tuple[str, str], float] = {}
        for lesson in level_lessons:
            for audio in level_audio:
                scores[(lesson["id"], str(audio))] = title_score(
                    lesson["title"], audio_title(audio)
                )

        for lesson in level_lessons:
            lesson_id = lesson["id"]
            if lesson_id not in overrides:
                continue
            relative = overrides[lesson_id]
            mapped_lessons.add(lesson_id)
            if relative is None:
                rejected.append(
                    {"lessonId": lesson_id, "level": level, "title": lesson["title"], "reason": "disabled-by-override"}
                )
                continue
            audio = args.audio_root.parent / relative
            if not audio.is_file():
                raise FileNotFoundError(f"Override audio does not exist: {audio}")
            used_audio.add(str(audio))
            mappings.append(
                {
                    "lessonId": lesson_id,
                    "level": level,
                    "title": lesson["title"],
                    "audio": str(audio),
                    "audioTitle": audio_title(audio),
                    "score": 1.0,
                    "source": "override",
                }
            )

        candidate_rows = []
        for lesson in level_lessons:
            if lesson["id"] in mapped_lessons:
                continue
            ranked = sorted(
                ((scores[(lesson["id"], str(audio))], audio) for audio in level_audio),
                reverse=True,
                key=lambda item: item[0],
            )
            if not ranked:
                rejected.append(
                    {"lessonId": lesson["id"], "level": level, "title": lesson["title"], "reason": "no-audio-in-level"}
                )
                continue
            best_score, best_audio = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else 0.0
            reverse_ranked = sorted(
                (
                    (scores[(other["id"], str(best_audio))], other["id"])
                    for other in level_lessons
                    if other["id"] not in mapped_lessons
                ),
                reverse=True,
            )
            reverse_second = reverse_ranked[1][0] if len(reverse_ranked) > 1 else 0.0
            margin = min(best_score - second_score, best_score - reverse_second)
            candidate_rows.append((best_score, margin, lesson, best_audio, second_score, reverse_second))

        for best_score, margin, lesson, audio, second_score, reverse_second in sorted(
            candidate_rows, reverse=True, key=lambda item: (item[0], item[1])
        ):
            if str(audio) in used_audio:
                rejected.append(
                    {
                        "lessonId": lesson["id"], "level": level, "title": lesson["title"],
                        "candidate": str(audio), "score": round(best_score, 4), "reason": "audio-already-used",
                    }
                )
                continue
            exact = best_score >= 0.9999
            accepted = exact or (best_score >= args.minimum_score and margin >= args.minimum_margin)
            if not accepted:
                rejected.append(
                    {
                        "lessonId": lesson["id"], "level": level, "title": lesson["title"],
                        "candidate": str(audio), "audioTitle": audio_title(audio),
                        "score": round(best_score, 4), "margin": round(margin, 4),
                        "secondLessonScore": round(reverse_second, 4),
                        "secondAudioScore": round(second_score, 4), "reason": "ambiguous-or-low-score",
                    }
                )
                continue
            used_audio.add(str(audio))
            mapped_lessons.add(lesson["id"])
            mappings.append(
                {
                    "lessonId": lesson["id"], "level": level, "title": lesson["title"],
                    "audio": str(audio), "audioTitle": audio_title(audio),
                    "score": round(best_score, 4), "margin": round(margin, 4), "source": "automatic",
                }
            )

    extras = [str(path) for path in audio_files if str(path) not in used_audio]
    level_summary = {}
    for level in sorted(by_level_lessons):
        level_summary[level] = {
            "lessons": len(by_level_lessons[level]),
            "audioFiles": len(by_level_audio[level]),
            "mapped": sum(1 for row in mappings if row["level"] == level),
            "unmappedLessons": sum(1 for row in rejected if row["level"] == level),
            "unusedAudio": sum(1 for path in extras if level_from_path(Path(path)) == level),
        }
    return {
        "format": "wordquest-raz-audio-catalog",
        "version": 1,
        "settings": {"minimumScore": args.minimum_score, "minimumMargin": args.minimum_margin},
        "summary": {
            "lessons": len(lessons), "audioFiles": len(audio_files), "mapped": len(mappings),
            "unmappedLessons": len(rejected), "unusedAudio": len(extras),
        },
        "levels": level_summary,
        "mappings": sorted(mappings, key=lambda row: row["lessonId"]),
        "unmapped": sorted(rejected, key=lambda row: row["lessonId"]),
        "unusedAudio": extras,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path("data/raz-course-library.json"))
    parser.add_argument("--audio-root", type=Path, default=Path("RAZ Audio"))
    parser.add_argument("--overrides", type=Path, default=Path("data/raz-audio-overrides.json"))
    parser.add_argument("--output", type=Path, default=Path("data/raz-audio-catalog.json"))
    parser.add_argument("--minimum-score", type=float, default=0.84)
    parser.add_argument("--minimum-margin", type=float, default=0.035)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = build_catalog(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(catalog["summary"], ensure_ascii=False))
    for level, summary in catalog["levels"].items():
        print(level, json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
