#!/usr/bin/env python3
"""Extract Level D lessons from the two scan-only compilation PDFs.

The script pulls the original JPEG from every PDF page, runs macOS Vision OCR
with both upright and 180-degree orientations, and groups story pages by the
repeated ``<book title> • Level D`` footer.  Covers, title pages, author lines,
footers, and page numbers are excluded from the dictation text.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from extract_raz_library import END_RE, WORD_RE, normalize_line, split_sentences


@dataclass
class Page:
    source: str
    number: int
    image: Path
    orientation: str
    score: float
    lines: list[dict[str, object]]


def ensure_binary(binary: Path, source: Path) -> None:
    if binary.exists() and binary.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return
    binary.parent.mkdir(parents=True, exist_ok=True)
    module_cache = binary.parent.parent / "clang-cache"
    module_cache.mkdir(parents=True, exist_ok=True)
    command = [
        "clang",
        f"-fmodules-cache-path={module_cache}",
        "-fobjc-arc",
        str(source),
        "-o",
        str(binary),
        "-framework",
        "Foundation",
        "-framework",
        "Vision",
        "-framework",
        "ImageIO",
    ]
    subprocess.run(command, check=True)


def extract_images(pdf: Path, output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(pdf)
    paths: list[Path] = []
    for index, page in enumerate(reader.pages, 1):
        destination = output / f"page-{index:04d}.jpg"
        paths.append(destination)
        if destination.exists():
            continue
        images = list(page.images)
        if not images:
            width = max(100, round(float(page.mediabox.width)))
            height = max(100, round(float(page.mediabox.height)))
            Image.new("RGB", (width, height), "white").save(destination, quality=90)
        else:
            destination.write_bytes(images[0].data)
    return paths


def load_vision_cache(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "error" not in record:
            records[str(record["path"])] = record
    return records


def run_vision(binary: Path, images: list[Path], cache_path: Path) -> dict[str, dict[str, object]]:
    cached = load_vision_cache(cache_path)
    missing = [image for image in images if str(image) not in cached]
    if not missing:
        return cached
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("a", encoding="utf-8") as cache_file:
        process = subprocess.Popen(
            [str(binary), *map(str, missing)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for index, line in enumerate(process.stdout, 1):
            record = json.loads(line)
            if "error" in record:
                raise RuntimeError(f"Vision failed for {record.get('path')}: {record['error']}")
            cache_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            cache_file.flush()
            cached[str(record["path"])] = record
            if index % 25 == 0 or index == len(missing):
                print(f"Vision OCR {index}/{len(missing)} new pages", flush=True)
        stderr = process.stderr.read() if process.stderr else ""
        status = process.wait()
        if status:
            raise RuntimeError(f"Vision OCR exited {status}: {stderr}")
    return cached


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())


def similar_title(left: str, right: str) -> bool:
    return difflib.SequenceMatcher(None, title_key(left), title_key(right)).ratio() >= 0.86


def footer_title(lines: list[dict[str, object]]) -> str | None:
    for line in lines:
        text = normalize_line(str(line.get("text", "")))
        match = re.match(r"(.+?)\s*[•·]\s*Level\s+D\b", text, flags=re.I)
        if match:
            candidate = normalize_line(match.group(1))
            if candidate and "leveled book" not in candidate.lower():
                return candidate
    return None


def page_number(lines: list[dict[str, object]]) -> int | None:
    candidates: list[tuple[float, int]] = []
    for line in lines:
        text = normalize_line(str(line.get("text", "")))
        if re.fullmatch(r"\d{1,2}", text):
            y = float(line.get("y", 1))
            candidates.append((y, int(text)))
    return min(candidates)[1] if candidates else None


def body_lines(lines: list[dict[str, object]]) -> list[str]:
    ignored = (
        "level d",
        "leveled book",
        "readinga-z.com",
        "reading a-z",
        "written by",
        "illustrated by",
        "word count",
        "all rights reserved",
        "learning a-z",
    )
    candidates: list[tuple[str, float]] = []
    for line in lines:
        text = normalize_line(str(line.get("text", "")))
        low = text.lower()
        if float(line.get("y", 1)) > 0.35:
            continue
        if not text or re.fullmatch(r"\d{1,2}", text):
            continue
        if any(marker in low for marker in ignored):
            continue
        if text.startswith("©"):
            continue
        if not any(character.isalpha() for character in text):
            continue
        candidates.append((text, float(line.get("height", 0))))
    if not candidates:
        return []
    largest = max(height for _, height in candidates)
    minimum = max(0.012, largest * 0.62)
    return [text for text, height in candidates if height >= minimum]


def oriented_lines(record: dict[str, object]) -> list[dict[str, object]]:
    orientation = str(record["orientation"])
    result: list[dict[str, object]] = []
    for original in list(record["lines"]):
        line = dict(original)
        if orientation == "down":
            line["x"] = 1.0 - float(line.get("x", 0)) - float(line.get("width", 0))
            line["y"] = 1.0 - float(line.get("y", 0)) - float(line.get("height", 0))
        result.append(line)
    result.sort(key=lambda line: (-round(float(line.get("y", 0)), 2), float(line.get("x", 0))))
    return result


def build_lessons(pages: list[Page]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    lessons: list[dict[str, object]] = []
    qa_pages: list[dict[str, object]] = []
    current_title: str | None = None
    current_text: list[str] = []
    current_pages: list[int] = []
    current_source = ""

    def finish() -> None:
        nonlocal current_title, current_text, current_pages, current_source
        if not current_title or not current_text:
            current_title, current_text, current_pages = None, [], []
            return
        text = normalize_line(" ".join(current_text))
        sentences = split_sentences(text)
        number = len(lessons) + 1
        lesson_id = f"RAZ-D-{number:03d}"
        reasons: list[str] = []
        if not sentences:
            reasons.append("no_sentences")
        if any(not END_RE.search(sentence) for sentence in sentences):
            reasons.append("missing_terminal_punctuation")
        if any(len(WORD_RE.findall(sentence)) > 45 for sentence in sentences):
            reasons.append("very_long_sentence")
        lessons.append(
            {
                "id": lesson_id,
                "level": "D",
                "title": current_title,
                "titleZh": "",
                "sentences": [
                    {"id": f"{lesson_id}-S{index:03d}", "english": sentence, "chinese": ""}
                    for index, sentence in enumerate(sentences, 1)
                ],
                "sourceName": current_source,
                "extraction": {
                    "method": "apple_vision_scan_ocr",
                    "sourcePages": current_pages,
                    "qaStatus": "review" if reasons else "pass",
                    "qaReasons": reasons,
                },
            }
        )
        current_title, current_text, current_pages = None, [], []

    for page in pages:
        raw_text = " ".join(normalize_line(str(line.get("text", ""))) for line in page.lines)
        raw_low = raw_text.lower()
        title = footer_title(page.lines)
        number = page_number(page.lines)
        if current_title and page.source != current_source:
            finish()
        is_back_matter = "level d leveled book" in raw_low or "all rights reserved" in raw_low
        if current_title and is_back_matter and not title:
            finish()
            continue
        if title:
            if current_title and not similar_title(current_title, title):
                finish()
            if current_title is None:
                current_title = title
                current_source = page.source
            page_body = body_lines(page.lines)
            if page_body:
                current_text.extend(page_body)
                current_pages.append(page.number)
            qa_pages.append(
                {
                    "source": page.source,
                    "page": page.number,
                    "title": title,
                    "pageNumber": number,
                    "orientation": page.orientation,
                    "score": page.score,
                    "body": page_body,
                }
            )
        elif current_title and (
            (number and number >= 3)
            or (
                "leveled book" not in raw_low
                and "written by" not in raw_low
                and any(END_RE.search(text) for text in body_lines(page.lines))
            )
        ):
            page_body = body_lines(page.lines)
            current_text.extend(page_body)
            current_pages.append(page.number)
            qa_pages.append(
                {
                    "source": page.source,
                    "page": page.number,
                    "title": current_title,
                    "pageNumber": number,
                    "orientation": page.orientation,
                    "score": page.score,
                    "body": page_body,
                    "reason": "missing_footer_title",
                }
            )
        elif current_title and ("leveled book" in raw_low or "written by" in raw_low):
            finish()
    finish()
    return lessons, qa_pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("RAZ Book/D-书本"))
    parser.add_argument("--work", type=Path, default=Path("tmp/raz-extraction/d"))
    parser.add_argument("--binary", type=Path, default=Path("tmp/bin/vision-ocr"))
    parser.add_argument("--source", type=Path, default=Path("scripts/vision_ocr.m"))
    parser.add_argument("--output", type=Path, default=Path("tmp/raz-extraction/group-d.json"))
    args = parser.parse_args()

    ensure_binary(args.binary, args.source)
    all_pages: list[Page] = []
    for pdf in sorted(args.root.glob("D_*.pdf")):
        source = pdf.stem
        images = extract_images(pdf, args.work / source / "images")
        cache_path = args.work / source / "vision-upright-v2.jsonl"
        records = run_vision(args.binary, images, cache_path)
        for number, image in enumerate(images, 1):
            record = records[str(image)]
            all_pages.append(
                Page(
                    source=pdf.name,
                    number=number,
                    image=image,
                    orientation=str(record["orientation"]),
                    score=float(record["score"]),
                    lines=oriented_lines(record),
                )
            )

    lessons, qa_pages = build_lessons(all_pages)
    summary = {
        "lessonCount": len(lessons),
        "sentenceCount": sum(len(lesson["sentences"]) for lesson in lessons),
        "passCount": sum(lesson["extraction"]["qaStatus"] == "pass" for lesson in lessons),
        "reviewCount": sum(lesson["extraction"]["qaStatus"] != "pass" for lesson in lessons),
        "pageCount": len(all_pages),
        "rotatedPageCount": sum(page.orientation == "down" for page in all_pages),
    }
    payload = {
        "format": "word-game-raz-course-library",
        "version": 1,
        "summary": summary,
        "lessons": lessons,
        "qaPages": qa_pages,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
