#!/usr/bin/env python3
"""Merge isolated RAZ audio batch shard indexes/states into the official files."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path, fallback):
    if not path.is_file():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path("data/raz-course-library.json"))
    parser.add_argument("--index", type=Path, default=Path("public/raz-audio/index.json"))
    parser.add_argument("--state", type=Path, default=Path("data/raz-audio-batch-state.json"))
    parser.add_argument("--shard-index", type=Path, action="append", default=[])
    parser.add_argument("--shard-state", type=Path, action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library = load_json(args.library, {})
    lessons = {lesson["id"]: lesson for lesson in library.get("lessons", [])}
    official_index = load_json(args.index, {})
    official_state = load_json(
        args.state,
        {
            "format": "wordquest-raz-audio-batch-state",
            "version": 1,
            "startedAt": now(),
            "results": {},
        },
    )

    failed_lessons: set[str] = set()
    merged_results = 0
    for shard_state_path in args.shard_state:
        shard_state = load_json(shard_state_path, {})
        for lesson_id, result in shard_state.get("results", {}).items():
            official_state.setdefault("results", {})[lesson_id] = result
            merged_results += 1
            if result.get("status") == "failed":
                failed_lessons.add(lesson_id)

    removed_entries = 0
    for lesson_id in failed_lessons:
        lesson = lessons.get(lesson_id)
        if not lesson:
            continue
        for sentence in lesson.get("sentences", []):
            if official_index.pop(sentence["id"], None) is not None:
                removed_entries += 1

    merged_index_entries = 0
    for shard_index_path in args.shard_index:
        shard_index = load_json(shard_index_path, {})
        official_index.update(shard_index)
        merged_index_entries += len(shard_index)

    official_state["updatedAt"] = now()
    official_state["mergedShardResults"] = merged_results
    official_state["lastMergedShardIndexes"] = [str(path) for path in args.shard_index]
    official_state["lastMergedShardStates"] = [str(path) for path in args.shard_state]
    atomic_json(args.index, dict(sorted(official_index.items())))
    atomic_json(args.state, official_state)
    print(
        json.dumps(
            {
                "mergedResults": merged_results,
                "failedLessons": len(failed_lessons),
                "removedStaleEntries": removed_entries,
                "mergedIndexEntries": merged_index_entries,
                "officialIndexEntries": len(official_index),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
