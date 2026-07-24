#!/usr/bin/env python3
"""Merge extracted RAZ lessons into import packages and a traceable QA report."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-’'][A-Za-z0-9]+)*")
END_RE = re.compile(r"[.!?][\"'’”)]*$")
LEVELS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
ARTIFACTS = ("[non-text]", "ground truth", "quick brown fox", "the image contains", "www.", "http://", "https://", "�", "||")


def title_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def load_lessons(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    lessons = payload if isinstance(payload, list) else payload.get("lessons", [])
    if not isinstance(lessons, list):
        raise ValueError(f"{path} does not contain a lessons array")
    return lessons


def load_order_map(path: Path | None) -> dict[str, int]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("order map must be a JSON object")
    result: dict[str, int] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, int) or value < 1:
            raise ValueError("order map values must be positive integers")
        result[key] = value
    return result


def order_key(lesson: dict[str, Any]) -> str:
    level = str(lesson.get("level", "")).upper()
    title = title_key(str(lesson.get("title", "")))
    return f"{level}:{title}"


def source_order(lesson: dict[str, Any], explicit_order: dict[str, int]) -> tuple[int, int, str, str]:
    configured = explicit_order.get(order_key(lesson), 9999)
    match = re.search(r"(\d+)$", str(lesson.get("id", "")))
    source_number = int(match.group(1)) if match else 9999
    return (configured, source_number, str(lesson.get("sourceName", "")), str(lesson.get("title", "")))


def sentence_issues(sentences: list[dict[str, str]], title: str) -> list[str]:
    issues: list[str] = []
    texts = [str(sentence.get("english", "")).strip() for sentence in sentences]
    if not texts:
        return ["no_sentences"]
    lowered = " ".join(texts).lower()
    if any(artifact in lowered for artifact in ARTIFACTS):
        issues.append("artifact_text")
    if any(not END_RE.search(text) for text in texts):
        issues.append("missing_terminal_punctuation")
    if any(len(WORD_RE.findall(text)) > 70 for text in texts):
        issues.append("very_long_sentence")
    if any("........................" in text for text in texts):
        issues.append("table_of_contents_text")
    return issues


def compact_lesson(lesson: dict[str, Any], lesson_id: str) -> dict[str, Any]:
    sentences = [
        {
            "id": f"{lesson_id}-S{index:03d}",
            "english": str(sentence["english"]).strip(),
            "chinese": str(sentence.get("chinese", "")),
        }
        for index, sentence in enumerate(lesson.get("sentences", []), 1)
        if str(sentence.get("english", "")).strip()
    ]
    return {
        "id": lesson_id,
        "level": str(lesson["level"]),
        "title": str(lesson["title"]),
        "titleZh": str(lesson.get("titleZh", "")),
        "sentences": sentences,
        "sourceName": str(lesson.get("sourceName", "")),
    }


def write_package(path: Path, lessons: list[dict[str, Any]]) -> None:
    payload = {
        "format": "word-game-raz-course-library",
        "version": 1,
        "summary": {
            "lessonCount": len(lessons),
            "sentenceCount": sum(len(lesson["sentences"]) for lesson in lessons),
            "levels": sorted({lesson["level"] for lesson in lessons}, key=LEVELS.index),
        },
        "lessons": lessons,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("data/raz-course-library"))
    parser.add_argument("--combined", type=Path, default=Path("data/raz-course-library.json"))
    parser.add_argument("--passed", type=Path, default=Path("data/raz-course-library-passed-qa.json"))
    parser.add_argument("--qa", type=Path, default=Path("data/raz-course-qa.json"))
    parser.add_argument(
        "--order-map",
        type=Path,
        help='Optional private JSON object mapping normalized "LEVEL:title" keys to positive positions.',
    )
    args = parser.parse_args()
    explicit_order = load_order_map(args.order_map)

    source_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for path in args.inputs:
        for lesson in load_lessons(path):
            key = (str(lesson.get("id", "")), str(lesson.get("sourceName", "")))
            source_by_key[key] = lesson
    source_lessons = list(source_by_key.values())

    grouped: dict[str, list[dict[str, Any]]] = {level: [] for level in LEVELS}
    for lesson in source_lessons:
        level = str(lesson.get("level", "")).upper()
        if level in grouped:
            grouped[level].append(lesson)

    compact: list[dict[str, Any]] = []
    qa_lessons: list[dict[str, Any]] = []
    source_id_counts: Counter[str] = Counter(str(lesson.get("id", "")) for lesson in source_lessons)
    for level in LEVELS:
        level_lessons: list[dict[str, Any]] = []
        for index, lesson in enumerate(
            sorted(grouped[level], key=lambda item: source_order(item, explicit_order)),
            1,
        ):
            lesson_number = explicit_order.get(order_key(lesson), index)
            lesson_id = f"RAZ-{level}-{lesson_number:03d}"
            built = compact_lesson(lesson, lesson_id)
            structural = sentence_issues(built["sentences"], built["title"])
            extraction = dict(lesson.get("extraction", {}))
            source_reasons = [str(reason) for reason in extraction.get("qaReasons", [])]
            review_reasons = list(dict.fromkeys([*structural, *source_reasons]))
            qa_lessons.append(
                {
                    "id": lesson_id,
                    "sourceLessonId": str(lesson.get("id", "")),
                    "sourceIdDuplicated": source_id_counts[str(lesson.get("id", ""))] > 1,
                    "level": level,
                    "title": built["title"],
                    "sourceName": built["sourceName"],
                    "sentenceCount": len(built["sentences"]),
                    "wordCount": sum(len(WORD_RE.findall(sentence["english"])) for sentence in built["sentences"]),
                    "status": "review" if review_reasons else "pass",
                    "reasons": review_reasons,
                    "extraction": extraction,
                }
            )
            level_lessons.append(built)
            compact.append(built)
        if level_lessons:
            write_package(args.output_dir / f"level-{level}.json", level_lessons)

    ids = [lesson["id"] for lesson in compact]
    sentence_ids = [sentence["id"] for lesson in compact for sentence in lesson["sentences"]]
    if len(ids) != len(set(ids)) or len(sentence_ids) != len(set(sentence_ids)):
        raise ValueError("generated IDs are not unique")
    if any(not lesson["sentences"] for lesson in compact):
        raise ValueError("one or more lessons contain no sentences")

    write_package(args.combined, compact)
    status_counts = Counter(item["status"] for item in qa_lessons)
    passed_ids = {item["id"] for item in qa_lessons if item["status"] == "pass"}
    write_package(args.passed, [lesson for lesson in compact if lesson["id"] in passed_ids])
    qa_payload = {
        "format": "word-game-raz-course-qa",
        "version": 1,
        "summary": {
            "lessonCount": len(compact),
            "sentenceCount": len(sentence_ids),
            "passCount": status_counts["pass"],
            "reviewCount": status_counts["review"],
            "duplicateIdCount": len(ids) - len(set(ids)),
            "duplicateSentenceIdCount": len(sentence_ids) - len(set(sentence_ids)),
            "emptyLessonCount": sum(not lesson["sentences"] for lesson in compact),
        },
        "inputs": [str(path) for path in args.inputs],
        "lessons": qa_lessons,
    }
    args.qa.parent.mkdir(parents=True, exist_ok=True)
    args.qa.write_text(json.dumps(qa_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa_payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
