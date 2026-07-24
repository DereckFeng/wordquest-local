#!/usr/bin/env python3
"""Inventory RAZ PDFs by page geometry and embedded-text coverage."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from pypdf import PdfReader


def level_from_path(path: Path) -> str:
    name = path.parent.name.upper()
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if name.startswith(letter):
            return letter
    return "?"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("RAZ Book"))
    parser.add_argument("--output", type=Path, default=Path("tmp/pdfs/inventory.json"))
    args = parser.parse_args()

    pdfs = sorted(args.root.rglob("*.pdf"))
    layouts: Counter[str] = Counter()
    per_level: dict[str, Counter[str]] = defaultdict(Counter)
    records: list[dict[str, object]] = []

    for index, path in enumerate(pdfs, 1):
        try:
            reader = PdfReader(path)
            page_count = len(reader.pages)
            first = reader.pages[0]
            width = round(float(first.mediabox.width))
            height = round(float(first.mediabox.height))
            geometry = f"{width}x{height}"
            sample_indexes = sorted(set([0, min(2, page_count - 1), page_count - 1]))
            sample_lengths = []
            for page_index in sample_indexes:
                sample_lengths.append(len((reader.pages[page_index].extract_text() or "").strip()))
            has_text = max(sample_lengths, default=0) >= 20
            layout = f"{geometry}:{'text' if has_text else 'scan'}"
            level = level_from_path(path)
            layouts[layout] += 1
            per_level[level][layout] += 1
            records.append(
                {
                    "path": str(path),
                    "level": level,
                    "pages": page_count,
                    "geometry": geometry,
                    "sample_text_lengths": sample_lengths,
                    "has_embedded_text": has_text,
                }
            )
        except Exception as exc:  # keep the inventory resumable around a bad PDF
            records.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
        if index % 100 == 0:
            print(f"{index}/{len(pdfs)}", flush=True)

    payload = {
        "pdf_count": len(pdfs),
        "layouts": dict(layouts.most_common()),
        "per_level": {level: dict(counts.most_common()) for level, counts in sorted(per_level.items())},
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pdf_count": len(pdfs), "layouts": payload["layouts"]}, indent=2))


if __name__ == "__main__":
    main()
