#!/usr/bin/env python3
"""Cross-check and rebuild Level D story text with LAN Unlimited OCR."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import difflib
import json
import re
import time
import urllib.request
from pathlib import Path

from PIL import Image

from extract_raz_library import END_RE, WORD_RE, normalize_line, split_sentences


BLOCK_RE = re.compile(
    r"(?ms)^([a-z_]+)\s+\[([0-9,\s]+)\](.*?)(?=^[a-z_]+\s+\[[0-9,\s]+\]|\Z)"
)


def make_contact(images: list[Path], output: Path) -> None:
    opened = [Image.open(path).convert("RGB") for path in images]
    first = opened[0]
    tile_width, tile_height = max(1, first.width // 2), max(1, first.height // 2)
    canvas = Image.new("RGB", (tile_width * 2, tile_height * 2), "white")
    for index, image in enumerate(opened):
        canvas.paste(image.resize((tile_width, tile_height)), ((index % 2) * tile_width, (index // 2) * tile_height))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=93, optimize=True)


def request_ocr(server: str, image: Path, max_attempts: int = 3) -> str:
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    payload = {
        "model": "unlimited-ocr",
        "temperature": 0,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "document parsing."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                ],
            }
        ],
    }
    request = urllib.request.Request(
        f"{server.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                result = json.load(response)
            return str(result["choices"][0]["message"]["content"])
        except Exception:
            if attempt == max_attempts:
                raise
            time.sleep(attempt * 2)
    raise AssertionError("unreachable")


def parse_blocks(raw: str) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    for match in BLOCK_RE.finditer(raw.strip()):
        coords = [int(value.strip()) for value in match.group(2).split(",")]
        if len(coords) != 4:
            continue
        text = normalize_line(match.group(3))
        blocks.append({"type": match.group(1), "coords": coords, "text": text})
    return blocks


def quadrant(block: dict[str, object]) -> int:
    x0, y0, x1, y1 = block["coords"]
    center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
    column = 0 if center_x < 500 else 1
    row = 0 if center_y < 500 else 1
    return row * 2 + column


def page_texts(raw: str, page_count: int) -> list[str]:
    grouped: list[list[dict[str, object]]] = [[] for _ in range(page_count)]
    for block in parse_blocks(raw):
        index = quadrant(block)
        if index < page_count and block["type"] == "text" and usable_text(str(block["text"])):
            grouped[index].append(block)
    results: list[str] = []
    for blocks in grouped:
        blocks.sort(key=lambda block: (block["coords"][1], block["coords"][0]))
        results.append(normalize_line(" ".join(str(block["text"]) for block in blocks)))
    return results


def usable_text(text: str) -> bool:
    normalized = normalize_line(text)
    low = normalized.lower()
    if not normalized or re.fullmatch(r"\d{1,2}", normalized):
        return False
    if "level d" in low:
        return False
    banned = (
        "the image contains no text",
        "stylistic or background element",
        "must be ignored according to the rules",
    )
    return not any(phrase in low for phrase in banned)


def normalized_compare(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("tmp/raz-extraction/group-d.json"))
    parser.add_argument("--work", type=Path, default=Path("tmp/raz-extraction/d/unlimited"))
    parser.add_argument("--server", default="http://192.168.18.12:1234")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, help="Only process the first N contact sheets")
    parser.add_argument("--output", type=Path, default=Path("tmp/raz-extraction/group-d-unlimited.json"))
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    page_lookup = {(page["source"], page["page"]): page for page in source["qaPages"]}
    ordered_keys: list[tuple[str, int]] = []
    for lesson in source["lessons"]:
        for page_number in lesson["extraction"]["sourcePages"]:
            key = (lesson["sourceName"], page_number)
            if key not in ordered_keys:
                ordered_keys.append(key)

    batches: list[dict[str, object]] = []
    for source_name in sorted({key[0] for key in ordered_keys}):
        keys = [key for key in ordered_keys if key[0] == source_name]
        for offset in range(0, len(keys), 4):
            group = keys[offset : offset + 4]
            batch_id = f"{Path(source_name).stem}-{group[0][1]:04d}-{group[-1][1]:04d}"
            contact = args.work / "contacts" / f"{batch_id}.jpg"
            images = [
                Path("tmp/raz-extraction/d") / Path(source_name).stem / "images" / f"page-{page_number:04d}.jpg"
                for _, page_number in group
            ]
            if not contact.exists():
                make_contact(images, contact)
            batches.append({"id": batch_id, "keys": group, "images": images, "contact": contact})
    if args.limit:
        batches = batches[: args.limit]

    def process(batch: dict[str, object]) -> tuple[str, str]:
        raw_path = args.work / "raw" / f"{batch['id']}.txt"
        if raw_path.exists():
            return str(batch["id"]), raw_path.read_text(encoding="utf-8")
        raw = request_ocr(args.server, batch["contact"])
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(raw, encoding="utf-8")
        return str(batch["id"]), raw

    raw_by_id: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process, batch): batch for batch in batches}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            batch_id, raw = future.result()
            raw_by_id[batch_id] = raw
            if index % 5 == 0 or index == len(batches):
                print(f"Unlimited OCR {index}/{len(batches)} contact sheets", flush=True)

    unlimited_by_page: dict[tuple[str, int], str] = {}
    missing: list[tuple[tuple[str, int], Path]] = []
    for batch in batches:
        keys = list(batch["keys"])
        texts = page_texts(raw_by_id[str(batch["id"])], len(keys))
        for key, image, text in zip(keys, batch["images"], texts):
            if text:
                unlimited_by_page[key] = text
            elif page_lookup.get(key, {}).get("body"):
                missing.append((key, image))

    def process_single(item: tuple[tuple[str, int], Path]) -> tuple[tuple[str, int], str]:
        key, image = item
        raw_path = args.work / "single" / f"{Path(key[0]).stem}-{key[1]:04d}.txt"
        if raw_path.exists():
            raw = raw_path.read_text(encoding="utf-8")
        else:
            raw = request_ocr(args.server, image)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(raw, encoding="utf-8")
        text = normalize_line(
            " ".join(
                str(block["text"])
                for block in parse_blocks(raw)
                if block["type"] == "text" and usable_text(str(block["text"]))
            )
        )
        if text:
            return key, text
        return key, ""

    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(process_single, item) for item in missing]
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                key, text = future.result()
                if text:
                    unlimited_by_page[key] = text
                if index % 5 == 0 or index == len(futures):
                    print(f"Single-page retry {index}/{len(futures)}", flush=True)

    rebuilt: list[dict[str, object]] = []
    comparison: list[dict[str, object]] = []
    for lesson in source["lessons"]:
        pieces: list[str] = []
        page_diffs: list[dict[str, object]] = []
        for page_number in lesson["extraction"]["sourcePages"]:
            key = (lesson["sourceName"], page_number)
            vision = normalize_line(" ".join(page_lookup.get(key, {}).get("body", [])))
            unlimited = unlimited_by_page.get(key, "")
            ratio = difflib.SequenceMatcher(None, normalized_compare(vision), normalized_compare(unlimited)).ratio() if unlimited else 0.0
            selected = unlimited if unlimited and (not vision or ratio >= 0.55) else vision
            if selected:
                pieces.append(selected)
            page_diffs.append(
                {
                    "page": page_number,
                    "vision": vision,
                    "unlimited": unlimited,
                    "similarity": round(ratio, 4),
                    "used": "unlimited_ocr" if selected == unlimited and unlimited else "vision_fallback",
                }
            )
        sentences = split_sentences(normalize_line(" ".join(pieces)))
        rebuilt_lesson = dict(lesson)
        rebuilt_lesson["sentences"] = [
            {"id": f"{lesson['id']}-S{index:03d}", "english": sentence, "chinese": ""}
            for index, sentence in enumerate(sentences, 1)
        ]
        reasons: list[str] = []
        fallback_count = sum(item["used"] == "vision_fallback" for item in page_diffs)
        if fallback_count:
            reasons.append(f"vision_fallback_pages:{fallback_count}")
        if any(not END_RE.search(sentence) for sentence in sentences):
            reasons.append("missing_terminal_punctuation")
        if any(len(WORD_RE.findall(sentence)) > 45 for sentence in sentences):
            reasons.append("very_long_sentence")
        rebuilt_lesson["extraction"] = {
            **lesson["extraction"],
            "method": "unlimited_ocr_with_vision_crosscheck",
            "qaStatus": "review" if reasons else "pass",
            "qaReasons": reasons,
        }
        rebuilt.append(rebuilt_lesson)
        comparison.append({"id": lesson["id"], "title": lesson["title"], "pages": page_diffs})

    summary = {
        "lessonCount": len(rebuilt),
        "sentenceCount": sum(len(lesson["sentences"]) for lesson in rebuilt),
        "passCount": sum(lesson["extraction"]["qaStatus"] == "pass" for lesson in rebuilt),
        "reviewCount": sum(lesson["extraction"]["qaStatus"] != "pass" for lesson in rebuilt),
        "contactSheetCount": len(batches),
        "singlePageRetryCount": len(missing),
    }
    payload = {
        "format": "word-game-raz-course-library",
        "version": 1,
        "summary": summary,
        "lessons": rebuilt,
        "comparison": comparison,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
