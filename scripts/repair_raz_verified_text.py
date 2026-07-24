#!/usr/bin/env python3
"""Apply externally supplied, visually verified body-text corrections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

def lessons_from(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("lessons"), list):
        return payload["lessons"]
    raise ValueError("input does not contain a lessons array")


def load_corrections(path: Path) -> dict[tuple[str, str], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("corrections") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("corrections file must be a list or contain a corrections list")
    corrections: dict[tuple[str, str], dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each correction must be an object")
        level = str(row.get("level", "")).strip()
        title = str(row.get("title", "")).strip()
        replace = row.get("replace")
        replacement = row.get("with")
        if (
            not level
            or not title
            or not isinstance(replace, str)
            or not replace
            or not isinstance(replacement, list)
            or not replacement
            or not all(isinstance(item, str) and item for item in replacement)
        ):
            raise ValueError("each correction needs level, title, replace, and a non-empty with list")
        key = (level, title)
        if key in corrections:
            raise ValueError(f"duplicate correction key: {key}")
        corrections[key] = {
            "replace": replace,
            "with": replacement,
            "evidence": str(row.get("evidence", "")),
        }
    return corrections


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corrections",
        required=True,
        type=Path,
        help="Private JSON correction list; keep this file outside source control.",
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    corrections = load_corrections(args.corrections)
    applied = []
    for path in args.inputs:
        payload = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for lesson in lessons_from(payload):
            key = (str(lesson.get("level", "")), str(lesson.get("title", "")))
            correction = corrections.get(key)
            if not correction:
                continue
            sentences = lesson.get("sentences", [])
            matches = [
                index for index, sentence in enumerate(sentences)
                if sentence.get("english") == correction["replace"]
            ]
            if not matches:
                # Idempotent reruns are allowed, but partial/ambiguous states are not.
                texts = [sentence.get("english") for sentence in sentences]
                if all(text in texts for text in correction["with"]):
                    continue
                raise RuntimeError(f"verified source text not found exactly once in {path}: {key}")
            if len(matches) != 1:
                raise RuntimeError(f"verified source text is ambiguous in {path}: {key}")
            index = matches[0]
            original = sentences[index]
            replacement = [
                {**original, "english": text}
                for text in correction["with"]
            ]
            lesson["sentences"] = [*sentences[:index], *replacement, *sentences[index + 1:]]
            changed = True
            applied.append({
                "path": str(path), "level": key[0], "title": key[1],
                "evidence": correction["evidence"],
            })
        if changed:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
    print(json.dumps({"applied": applied}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
