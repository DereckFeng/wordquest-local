#!/usr/bin/env python3
"""Use local Qwen3-VL as a third vote for disputed Level D scan pages."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
from difflib import SequenceMatcher
import json
import re
import time
import urllib.request
from pathlib import Path

from extract_raz_library import END_RE, WORD_RE, normalize_line, split_sentences


PROMPT = (
    "Transcribe only the main story text printed on this textbook page. "
    "Ignore image labels, footer, and page number. Preserve exact capitalization, "
    "punctuation, and quotation marks. Return only the transcription, with no explanation."
)


def request_qwen(server: str, image: Path, max_attempts: int = 3) -> str:
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    payload = {
        "model": "qwen/qwen3-vl-8b",
        "temperature": 0,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
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


def clean_transcription(text: str, title: str = "") -> str:
    text = text.strip()
    text = re.sub(r"^```(?:text)?\s*|\s*```$", "", text, flags=re.I)
    text = re.sub(r"^(?:transcription|main story text)\s*:\s*", "", text, flags=re.I)
    text = text.replace("[Non-Text]", " ").replace("||", " ")
    text = normalize_line(text)
    if title and text.lower().startswith(title.lower() + " "):
        text = text[len(title) :].lstrip()
    return text


def is_disputed(title: str, page: dict[str, object]) -> bool:
    selected = str(page["unlimited"] if page["used"] == "unlimited_ocr" else page["vision"])
    unlimited = str(page["unlimited"])
    low = unlimited.lower()
    artifacts = ("[non-text]", "the ground truth", "the image", "quick brown fox", "||")
    return bool(
        page["used"] == "vision_fallback"
        or any(item in low for item in artifacts)
        or (unlimited and unlimited.lower().startswith(title.lower() + " "))
        or selected.count('"') % 2
    )


def plausible(text: str) -> bool:
    low = text.lower()
    banned = ("i cannot", "i can't", "the image", "ground truth", "no text", "transcription")
    return bool(text and any(character.isalpha() for character in text) and not any(item in low for item in banned))


def normalized_vote(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def has_vision_unlimited_consensus(vision: str, unlimited: str) -> bool:
    if not plausible(vision) or not plausible(unlimited):
        return False
    left, right = normalized_vote(vision), normalized_vote(unlimited)
    if not left or not right:
        return False
    punctuation = lambda text: "".join(character for character in text if character in '.!?,;:"')
    return punctuation(vision) == punctuation(unlimited) and SequenceMatcher(None, left, right).ratio() >= 0.97


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("tmp/raz-extraction/group-d-unlimited.json"))
    parser.add_argument("--work", type=Path, default=Path("tmp/raz-extraction/d/qwen-review"))
    parser.add_argument("--server", default="http://192.168.18.12:1234")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--cache-only", action="store_true", help="Use completed Qwen reviews without calling the server")
    parser.add_argument("--output", type=Path, default=Path("tmp/raz-extraction/group-d-reviewed.json"))
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    lesson_by_id = {lesson["id"]: lesson for lesson in source["lessons"]}
    disputed: list[tuple[str, str, int, str, Path]] = []
    for comparison in source["comparison"]:
        lesson = lesson_by_id[comparison["id"]]
        for page in comparison["pages"]:
            if not is_disputed(str(comparison["title"]), page):
                continue
            source_name = str(lesson["sourceName"])
            page_number = int(page["page"])
            image = (
                Path("tmp/raz-extraction/d")
                / Path(source_name).stem
                / "images"
                / f"page-{page_number:04d}.jpg"
            )
            disputed.append((str(comparison["id"]), str(comparison["title"]), page_number, source_name, image))

    def process(item: tuple[str, str, int, str, Path]) -> tuple[tuple[str, int], str]:
        lesson_id, title, page_number, source_name, image = item
        cache = args.work / f"{Path(source_name).stem}-{page_number:04d}.txt"
        if cache.exists():
            raw = cache.read_text(encoding="utf-8")
        elif args.cache_only:
            return (lesson_id, page_number), ""
        else:
            raw = request_qwen(args.server, image)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(raw, encoding="utf-8")
        return (lesson_id, page_number), clean_transcription(raw, title)

    qwen: dict[tuple[str, int], str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process, item) for item in disputed]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            key, text = future.result()
            qwen[key] = text
            if index % 5 == 0 or index == len(futures):
                print(f"Qwen review {index}/{len(futures)} pages", flush=True)

    reviewed: list[dict[str, object]] = []
    audit: list[dict[str, object]] = []
    comparison_by_id = {item["id"]: item for item in source["comparison"]}
    for lesson in source["lessons"]:
        comparison = comparison_by_id[lesson["id"]]
        pieces: list[str] = []
        lesson_audit: list[dict[str, object]] = []
        reasons: list[str] = []
        for page in comparison["pages"]:
            vision = clean_transcription(str(page["vision"]), str(lesson["title"]))
            unlimited = clean_transcription(str(page["unlimited"]), str(lesson["title"]))
            base = unlimited if page["used"] == "unlimited_ocr" else vision
            candidate = qwen.get((str(lesson["id"]), int(page["page"])), "")
            if is_disputed(str(lesson["title"]), page) and not candidate:
                reasons.append(f"third_ocr_pending_page:{page['page']}")
            consensus = has_vision_unlimited_consensus(vision, unlimited)
            selected = base if consensus else candidate if plausible(candidate) else base
            if candidate and not plausible(candidate):
                reasons.append(f"qwen_rejected_page:{page['page']}")
            if selected:
                pieces.append(selected)
            lesson_audit.append(
                {
                    "page": page["page"],
                    "selected": "vision_unlimited_consensus" if consensus else "qwen3_vl" if selected == candidate and candidate else page["used"],
                    "text": selected,
                }
            )
        sentences = split_sentences(normalize_line(" ".join(pieces)))
        if any("[" in sentence or "]" in sentence or "ground truth" in sentence.lower() for sentence in sentences):
            reasons.append("artifact_text")
        if sum(sentence.count('"') for sentence in sentences) % 2:
            reasons.append("unbalanced_quotes")
        if any(not END_RE.search(sentence) for sentence in sentences):
            reasons.append("missing_terminal_punctuation")
        if any(len(WORD_RE.findall(sentence)) > 45 for sentence in sentences):
            reasons.append("very_long_sentence")
        unique_reasons = list(dict.fromkeys(reasons))
        rebuilt = dict(lesson)
        rebuilt["sentences"] = [
            {"id": f"{lesson['id']}-S{index:03d}", "english": sentence, "chinese": ""}
            for index, sentence in enumerate(sentences, 1)
        ]
        rebuilt["extraction"] = {
            **lesson["extraction"],
            "method": "vision_unlimited_ocr_qwen3_vl_review",
            "qaStatus": "review" if unique_reasons else "pass",
            "qaReasons": unique_reasons,
        }
        reviewed.append(rebuilt)
        audit.append({"id": lesson["id"], "title": lesson["title"], "pages": lesson_audit})

    summary = {
        "lessonCount": len(reviewed),
        "sentenceCount": sum(len(lesson["sentences"]) for lesson in reviewed),
        "passCount": sum(lesson["extraction"]["qaStatus"] == "pass" for lesson in reviewed),
        "reviewCount": sum(lesson["extraction"]["qaStatus"] != "pass" for lesson in reviewed),
        "disputedPageCount": len(disputed),
        "qwenReviewedPageCount": sum(bool(text) for text in qwen.values()),
    }
    payload = {
        "format": "word-game-raz-course-library",
        "version": 1,
        "summary": summary,
        "lessons": reviewed,
        "audit": audit,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
