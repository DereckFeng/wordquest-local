#!/usr/bin/env python3
"""Extract sentence-level RAZ lessons from the local PDF collection.

The individual RAZ PDFs in this collection use one of two print layouts:

* landscape sheet: two upright logical pages, left then right;
* portrait sheet: an upright top page and a 180-degree bottom page.

This extractor rebuilds logical reading order, keeps the dominant story font,
removes front/back matter and captions, and emits one dictation sentence per
record. Scan-only compilation PDFs (currently Level D) are intentionally
handled by the separate OCR stage.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pdfplumber
from pypdf import PdfReader


EXTRACTOR_VERSION = 18
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[’'][A-Za-z]+)*(?:-[A-Za-z0-9]+)*")
END_RE = re.compile(r"[.!?][\"'’”)]*$")


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    fontname: str


@dataclass
class Line:
    text: str
    top: float
    bottom: float
    words: list[Word]


@dataclass
class LogicalPage:
    physical_page: int
    side: str
    width: float
    height: float
    words: list[Word]


def level_from_path(path: Path) -> str:
    parent = path.parent.name.upper()
    for letter in LETTERS:
        if parent.startswith(letter):
            return letter
    match = re.match(r"([A-Z])", path.name.upper())
    return match.group(1) if match else "?"


def source_number(path: Path) -> int | None:
    match = re.match(r"[A-Z][ -]?(\d+)", path.stem.upper())
    return int(match.group(1)) if match else None


def clean_filename_title(path: Path) -> str:
    stem = unicodedata.normalize("NFC", path.stem)
    stem = re.sub(r"^[A-Z][ -]?\d+[ -]*", "", stem, flags=re.I)
    stem = re.sub(r"[- ]*raz_[a-z0-9_]+(?:_clr)?$", "", stem, flags=re.I)
    stem = re.sub(r"[- ]*[S]?书本$", "", stem, flags=re.I)
    stem = stem.replace("书本", "")
    stem = re.sub(r"\s+", " ", stem).strip(" -_")
    return stem


def normalize_line(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00ad", "").replace("�", ".")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([“‘])\s+", r"\1", text)
    text = re.sub(r"\s+([”’])", r"\1", text)
    return text


def repair_dotted_spacing(text: str) -> str:
    encoded_spaces = re.findall(r"(?<=[A-Za-zÀ-ÿ,;:])\.(?=[A-Za-zÀ-ÿ“‘])", text)
    if len(encoded_spaces) < 2:
        return text
    text = re.sub(r"^\.{3,}", "", text)
    trailing_period = text.endswith(".")
    sentence_space = "\uE002"
    quoted_period = "\uE003"
    text = re.sub(r"\.(?=[”’\"'])", quoted_period, text)
    text = re.sub(r"\.{2,}\s*(?=[A-ZÀ-Þ“‘\"'])", sentence_space, text)
    text = re.sub(r"\.{2,}", " ", text)
    text = text.replace(".", " ").replace(sentence_space, ". ").replace(quoted_period, ".")
    text = normalize_line(text)
    if trailing_period and not END_RE.search(text):
        text += "."
    return text


def cover_metadata(path: Path) -> tuple[str, int | None, str]:
    reader = PdfReader(path)
    cover_text = "\n".join((page.extract_text() or "") for page in reader.pages[:2])
    lines = [normalize_line(line) for line in cover_text.splitlines() if normalize_line(line)]
    expected_match = re.search(r"Word Count:\s*([\d,]+)", cover_text, flags=re.I)
    expected = int(expected_match.group(1).replace(",", "")) if expected_match else None

    stop_markers = (
        "www.",
        "visit ",
        "connections",
        "writing",
        "social studies",
        "science",
        "leveled book",
        "retold by",
        "written by",
        "illustrated by",
    )
    title = ""
    for index, line in enumerate(lines):
        if "a reading a" not in line.lower() or "level" not in line.lower():
            continue
        collected: list[str] = []
        for previous in reversed(lines[max(0, index - 4) : index]):
            low = previous.lower()
            if any(low.startswith(marker) for marker in stop_markers):
                break
            if len(previous) > 90 or (END_RE.search(previous) and not previous.endswith(":")):
                break
            collected.append(previous)
        if collected:
            title = " ".join(reversed(collected))
            break

    fallback = clean_filename_title(path)
    if not title or len(WORD_RE.findall(title)) > 18:
        title = fallback
    if fallback and len(WORD_RE.findall(title)) == 1 and len(WORD_RE.findall(fallback)) > 1:
        title = fallback
    title_key = re.sub(r"[^a-z0-9]", "", title.lower())
    fallback_key = re.sub(r"[^a-z0-9]", "", fallback.lower())
    if (
        fallback
        and fallback_key
        and fallback_key in title_key
        and len(WORD_RE.findall(title)) > len(WORD_RE.findall(fallback)) + 1
    ):
        title = fallback
    return normalize_line(title), expected, cover_text


def dominant_body_size(document: pdfplumber.PDF) -> float:
    sentence_counts: Counter[float] = Counter()
    for logical in logical_pages(document):
        for line in group_lines(logical.words):
            if line.top >= logical.height * 0.88 or not re.search(r"[.!?][\"'’”)]*$", line.text):
                continue
            for word in line.words:
                size = round(word.size, 1)
                if size >= 9 and any(character.isalnum() for character in word.text):
                    sentence_counts[size] += sum(character.isalnum() for character in word.text)
    if sentence_counts:
        return sentence_counts.most_common(1)[0][0]

    counts: Counter[float] = Counter()
    for page in document.pages[2:]:
        for char in page.chars:
            text = str(char.get("text", ""))
            size = round(float(char.get("size", 0)), 1)
            if size >= 9 and any(character.isalnum() for character in text):
                counts[size] += 1
    if not counts:
        raise ValueError("no usable embedded text")
    return counts.most_common(1)[0][0]


def font_family(fontname: str) -> str:
    name = fontname.split("+")[-1]
    name = re.sub(r"-(?:bold|italic|roman|regular|medium|semibold|cn).*", "", name, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def dominant_body_family(document: pdfplumber.PDF, body_size: float) -> str:
    counts: Counter[str] = Counter()
    for page in document.pages[2:]:
        for char in page.chars:
            if abs(float(char.get("size", 0)) - body_size) > 0.75:
                continue
            text = str(char.get("text", ""))
            if any(character.isalnum() for character in text):
                counts[font_family(str(char.get("fontname", "")))] += 1
    return counts.most_common(1)[0][0] if counts else ""


def convert_words(crop: pdfplumber.page.CroppedPage, side: str, rotate_180: bool) -> LogicalPage:
    x_origin, y_origin, x_end, y_end = crop.bbox
    width, height = x_end - x_origin, y_end - y_origin
    converted: list[Word] = []
    extracted = crop.extract_words(extra_attrs=["size", "fontname"], use_text_flow=False)
    for item in extracted:
        text = str(item["text"])
        x0, x1 = float(item["x0"]) - x_origin, float(item["x1"]) - x_origin
        top, bottom = float(item["top"]) - y_origin, float(item["bottom"]) - y_origin
        if rotate_180:
            text = text[::-1]
            x0, x1 = width - x1, width - x0
            top, bottom = height - bottom, height - top
        converted.append(
            Word(
                text=text,
                x0=x0,
                x1=x1,
                top=top,
                bottom=bottom,
                size=float(item.get("size", 0)),
                fontname=str(item.get("fontname", "")),
            )
        )
    converted.sort(key=lambda word: (round(word.top, 1), word.x0))
    return LogicalPage(physical_page=crop.page_number, side=side, width=width, height=height, words=converted)


def logical_pages(document: pdfplumber.PDF) -> Iterable[LogicalPage]:
    for page in document.pages[2:]:
        width, height = float(page.width), float(page.height)
        if width > height:
            midpoint = width / 2
            yield convert_words(page.crop((0, 0, midpoint, height)), "left", False)
            yield convert_words(page.crop((midpoint, 0, width, height)), "right", False)
        else:
            midpoint = height / 2
            yield convert_words(page.crop((0, 0, width, midpoint)), "top", False)
            yield convert_words(page.crop((0, midpoint, width, height)), "bottom", True)


def group_lines(words: Sequence[Word]) -> list[Line]:
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda item: (item.top, item.x0)):
        if not lines:
            lines.append([word])
            continue
        current_top = sum(item.top for item in lines[-1]) / len(lines[-1])
        tolerance = max(2.0, min(word.size, lines[-1][0].size) * 0.22)
        if abs(word.top - current_top) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])

    result: list[Line] = []
    for items in lines:
        items.sort(key=lambda item: item.x0)
        text = normalize_line(" ".join(item.text for item in items))
        if text:
            result.append(Line(text=text, top=min(item.top for item in items), bottom=max(item.bottom for item in items), words=items))
    return result


def is_title_like(text: str) -> bool:
    minor = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
    tokens = WORD_RE.findall(text)
    return bool(tokens) and all(token.lower() in minor or token[:1].isupper() for token in tokens)


def is_bold_heading(line: Line) -> bool:
    if line.text.lower().startswith("moral:") or len(WORD_RE.findall(line.text)) > 12:
        return False
    meaningful = [word for word in line.words if any(char.isalpha() for char in word.text)]
    all_bold = bool(meaningful) and all(
        "bold" in word.fontname.lower() or "capitals" in word.fontname.lower()
        for word in meaningful
    )
    return all_bold and is_title_like(line.text)


def strip_heading_prefix(line: Line) -> Line:
    words = list(line.words)
    prefix_count = 0
    for word in words:
        font = word.fontname.lower()
        if "bold" in font or "capitals" in font:
            prefix_count += 1
        else:
            break
    if not prefix_count or prefix_count == len(words) or prefix_count > 8:
        return line
    prefix = normalize_line(" ".join(word.text for word in words[:prefix_count]))
    if re.search(r"[.!?]", prefix) or not is_title_like(prefix):
        return line
    remainder = words[prefix_count:]
    return Line(
        text=normalize_line(" ".join(word.text for word in remainder)),
        top=min(word.top for word in remainder),
        bottom=max(word.bottom for word in remainder),
        words=remainder,
    )


def ordered_lines(logical: LogicalPage, selected: Sequence[Word]) -> list[Line]:
    lines = group_lines(selected)
    midpoint = logical.width / 2
    split_rows = 0
    for line in lines:
        left = any((word.x0 + word.x1) / 2 < logical.width * 0.42 for word in line.words)
        right = any((word.x0 + word.x1) / 2 > logical.width * 0.58 for word in line.words)
        gaps = [right_word.x0 - left_word.x1 for left_word, right_word in zip(line.words, line.words[1:])]
        if left and right and gaps and max(gaps) > logical.width * 0.12:
            split_rows += 1
    if split_rows < 3:
        return lines
    left_words = [word for word in selected if (word.x0 + word.x1) / 2 < midpoint]
    right_words = [word for word in selected if (word.x0 + word.x1) / 2 >= midpoint]
    return group_lines(left_words) + group_lines(right_words)


def page_marker(lines: Sequence[Line]) -> str | None:
    candidates = [re.sub(r"[^a-z ]", "", line.text.lower()).strip() for line in lines[:8]]
    compact = [item.replace(" ", "") for item in candidates]
    if any(item in {"contents", "tableofcontent", "tableofcontents"} for item in compact):
        return "skip"
    dotted_entries = sum("..." in line.text for line in lines[:12])
    leader_runs = sum(len(re.findall(r"\.{5,}", line.text)) for line in lines[:12])
    if dotted_entries >= 3 or leader_runs >= 3:
        return "skip"
    combined = "".join(compact[:3])
    terminal = {"glossary", "index", "abouttheauthor", "bibliography", "references"}
    if any(item in terminal for item in candidates):
        return "stop"
    if any(item in terminal for item in compact) or any(combined.startswith(item) for item in terminal):
        return "stop"
    if lines:
        first = normalize_line(lines[0].text).lower()
        if first.endswith(" quest") or first in {"photo credits", "connections", "extension activity"}:
            return "stop"
    return None


def extract_body_text(
    document: pdfplumber.PDF,
    body_size: float | Sequence[float],
    body_family: str = "",
    family_additions: set[tuple[int, str, str]] | None = None,
) -> tuple[str, list[dict[str, object]]]:
    body_sizes = [body_size] if isinstance(body_size, (int, float)) else list(body_size)
    family_additions = family_additions or set()
    pieces: list[str] = []
    page_records: list[dict[str, object]] = []
    stopped = False
    for logical in logical_pages(document):
        all_lines = group_lines(logical.words)
        marker = page_marker(all_lines)
        if marker == "stop":
            stopped = True
        if stopped or marker == "skip":
            page_records.append(
                {
                    "physical_page": logical.physical_page,
                    "side": logical.side,
                    "status": marker or "after_back_matter",
                }
            )
            continue

        family_size_counts: Counter[tuple[str, float]] = Counter()
        for word in logical.words:
            if word.top >= logical.height * 0.88 or word.size < 9.5:
                continue
            family_size_counts[(font_family(word.fontname), round(word.size, 1))] += sum(
                character.isalnum() for character in word.text
            )

        selected_families = {body_family} if body_family else set()
        alternate_family = ""
        alternate_size = 0.0
        alternate_chars = 0
        for (family, size), chars in family_size_counts.most_common():
            if family == body_family or size < 10.5 or chars < 60:
                continue
            alternate_family, alternate_size, alternate_chars = family, size, chars
            break
        body_family_chars = sum(
            chars for (family, _), chars in family_size_counts.items() if family == body_family
        )
        addition_key = (logical.physical_page, logical.side, alternate_family)
        if alternate_family and addition_key in family_additions:
            selected_families.add(alternate_family)

        family_words = [
            word
            for word in logical.words
            if word.top < logical.height * 0.88
            and (not selected_families or font_family(word.fontname) in selected_families)
        ]
        page_counts: Counter[float] = Counter()
        for word in family_words:
            if word.size >= 9.5:
                page_counts[round(word.size, 1)] += sum(character.isalnum() for character in word.text)
        page_size = body_sizes[0]
        if page_counts:
            candidate, candidate_count = page_counts.most_common(1)[0]
            if candidate_count >= 20 and abs(candidate - body_sizes[0]) <= 4:
                page_size = candidate
        allowed_sizes = [page_size, *body_sizes[1:]]
        selected = [
            word for word in family_words if any(abs(word.size - size) <= 0.75 for size in allowed_sizes)
        ]
        selected_lines = ordered_lines(logical, selected)
        selected_marker = page_marker(selected_lines)
        if selected_marker == "skip" and alternate_family and addition_key in family_additions:
            selected = [
                word
                for word in logical.words
                if word.top < logical.height * 0.88
                and font_family(word.fontname) == alternate_family
                and abs(word.size - alternate_size) <= 0.75
            ]
            selected_lines = ordered_lines(logical, selected)
            selected_marker = page_marker(selected_lines)
            page_size = alternate_size
        if selected_marker == "stop":
            stopped = True
        if stopped or selected_marker == "skip":
            page_records.append(
                {
                    "physical_page": logical.physical_page,
                    "side": logical.side,
                    "status": selected_marker or "after_back_matter",
                    "bodyFamilyChars": body_family_chars,
                    "alternateFamily": alternate_family,
                    "alternateSize": alternate_size,
                    "alternateChars": alternate_chars,
                }
            )
            continue
        body_lines = []
        for line in selected_lines:
            if re.fullmatch(r"\d+", line.text):
                continue
            if is_bold_heading(line):
                continue
            line = strip_heading_prefix(line)
            if (
                not line.text
                or "www." in line.text.lower()
                or "http" in line.text.lower()
                or re.search(r"\.{8,}", line.text)
            ):
                continue
            body_lines.append(line.text)
        page_text = repair_dotted_spacing(normalize_line(" ".join(body_lines)))
        if page_text:
            pieces.append(page_text)
        page_records.append(
            {
                "physical_page": logical.physical_page,
                "side": logical.side,
                "status": "body" if page_text else "empty",
                "text": page_text,
                "body_size": page_size,
                "bodyFamilyChars": body_family_chars,
                "alternateFamily": alternate_family,
                "alternateSize": alternate_size,
                "alternateChars": alternate_chars,
            }
        )
    return normalize_line(" ".join(pieces)), page_records


def protect_abbreviations(text: str) -> str:
    protected = text
    abbreviations = [
        "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Sr.", "Jr.", "St.", "Mt.",
        "e.g.", "i.e.", "a.m.", "p.m.", "vs.", "etc.", "U.S.", "U.K.",
    ]
    for abbreviation in abbreviations:
        protected = protected.replace(abbreviation, abbreviation.replace(".", "\uE000"))
    protected = re.sub(
        r"\b(?:[A-Z]\.\s*){2,}(?=[A-Z][a-z])",
        lambda match: match.group(0).replace(".", "\uE000"),
        protected,
    )
    protected = re.sub(
        r"\b(?:[A-Z]\.){2,}",
        lambda match: match.group(0)[:-1].replace(".", "\uE000") + ".",
        protected,
    )
    protected = re.sub(r"(?<=\d)\.(?=\d)", "\uE000", protected)
    return protected


def split_sentences(text: str) -> list[str]:
    protected = protect_abbreviations(text)
    sentences: list[str] = []
    start = 0
    for match in re.finditer(r"[.!?]+[\"'’”)]*", protected):
        end = match.end()
        remainder = protected[end:].lstrip()
        if remainder and remainder[0] in ",;:":
            continue
        if remainder and remainder[0].islower():
            continue
        candidate = protected[start:end].replace("\uE000", ".").strip()
        if candidate:
            sentences.append(normalize_line(candidate))
        start = end
        while start < len(protected) and protected[start].isspace():
            start += 1
    return [sentence for sentence in sentences if WORD_RE.search(sentence)]


def count_words(sentences: Sequence[str]) -> int:
    return sum(len(WORD_RE.findall(sentence)) for sentence in sentences)


def trim_trailing_supplemental(
    sentences: list[str], expected: int | None
) -> tuple[list[str], str]:
    if not expected or count_words(sentences) <= expected:
        return sentences, ""
    headings = (
        "Fire Safety Tips",
        "Discussion Questions",
        "Extension Activity",
        "Think About It",
        "Try This",
    )
    original_delta = abs(count_words(sentences) - expected)
    for index, sentence in enumerate(sentences):
        heading = next((item for item in headings if sentence.startswith(item)), "")
        if not heading or index < len(sentences) * 0.65:
            continue
        trimmed = sentences[:index]
        if abs(count_words(trimmed) - expected) < original_delta:
            return trimmed, heading
    return sentences, ""


def candidate_body_sizes(document: pdfplumber.PDF, primary: float) -> list[float]:
    counts: Counter[float] = Counter()
    for page in document.pages[2:]:
        for char in page.chars:
            text = str(char.get("text", ""))
            size = round(float(char.get("size", 0)), 1)
            if size >= 9.5 and any(character.isalnum() for character in text):
                counts[size] += 1
    primary_count = counts.get(round(primary, 1), max(counts.values(), default=0))
    result: list[float] = []
    for size, count in counts.most_common():
        if abs(size - primary) <= 0.75:
            continue
        if count < max(60, primary_count * 0.03):
            continue
        if any(abs(size - existing) <= 0.75 for existing in result):
            continue
        result.append(size)
        if len(result) == 5:
            break
    return result


def suspicious_reasons(sentences: Sequence[str], expected: int | None, actual: int) -> list[str]:
    reasons: list[str] = []
    if not sentences:
        reasons.append("no_sentences")
    if expected:
        delta = actual - expected
        ratio = abs(delta) / max(expected, 1)
        if ratio > 0.03:
            reasons.append(f"word_count_delta:{delta:+d}")
    if any("�" in sentence or "www." in sentence.lower() for sentence in sentences):
        reasons.append("artifact_character")
    if any(len(WORD_RE.findall(sentence)) > 70 for sentence in sentences):
        reasons.append("very_long_sentence")
    if any(not END_RE.search(sentence) for sentence in sentences):
        reasons.append("missing_terminal_punctuation")
    if sum(sentence.count('"') for sentence in sentences) % 2:
        reasons.append("unbalanced_straight_quotes")
    return reasons


def extract_one(path: Path, sequence: int) -> dict[str, object]:
    level = level_from_path(path)
    title, expected, _ = cover_metadata(path)
    with pdfplumber.open(path) as document:
        body_size = dominant_body_size(document)
        body_family = dominant_body_family(document, body_size)
        selected_sizes = [body_size]
        body_text, page_records = extract_body_text(document, selected_sizes, body_family)
        sentences = split_sentences(body_text)
        actual = count_words(sentences)
        selected_additions: set[tuple[int, str, str]] = set()
        if expected and expected >= 100 and actual < expected * 0.93:
            gap = expected - actual
            candidates: list[tuple[int, int, tuple[int, str, str]]] = []
            for record in page_records:
                family = str(record.get("alternateFamily", ""))
                chars = int(record.get("alternateChars", 0))
                size = float(record.get("alternateSize", 0))
                if not family or chars < 60:
                    continue
                estimated_words = max(1, round(chars / 5.2))
                body_chars = int(record.get("bodyFamilyChars", 0))
                penalty = 0 if body_chars < 50 else 1 if body_chars < 150 else 4
                if size < 11:
                    penalty += 4
                key = (int(record["physical_page"]), str(record["side"]), family)
                candidates.append((estimated_words, penalty, key))

            # Use the publisher's cover word count to select only the missing
            # font-switched story blocks, without sweeping in every caption.
            states: dict[int, tuple[int, set[tuple[int, str, str]]]] = {0: (0, set())}
            maximum = max(gap + 250, round(gap * 1.5))
            for weight, penalty, key in candidates:
                additions: dict[int, tuple[int, set[tuple[int, str, str]]]] = {}
                for total, (cost, chosen) in states.items():
                    new_total = total + weight
                    if new_total > maximum:
                        continue
                    new_cost = cost + penalty
                    if new_total not in states or new_cost < states[new_total][0]:
                        additions[new_total] = (new_cost, chosen | {key})
                for total, state in additions.items():
                    if total not in states or state[0] < states[total][0]:
                        states[total] = state
            best_total, (_, selected_additions) = min(
                states.items(), key=lambda item: (abs(gap - item[0]) * 10 + item[1][0], item[1][0])
            )
            if best_total:
                augmented_text, augmented_records = extract_body_text(
                    document, selected_sizes, body_family, selected_additions
                )
                augmented_sentences = split_sentences(augmented_text)
                augmented_actual = count_words(augmented_sentences)
                if abs(expected - augmented_actual) < abs(expected - actual):
                    body_text, page_records = augmented_text, augmented_records
                    sentences, actual = augmented_sentences, augmented_actual
                else:
                    selected_additions = set()
        sentences, trimmed_supplemental = trim_trailing_supplemental(sentences, expected)
        actual = count_words(sentences)
    number = source_number(path) or sequence
    lesson_id = f"RAZ-{level}-{number:03d}"
    reasons = suspicious_reasons(sentences, expected, actual)
    return {
        "id": lesson_id,
        "level": level,
        "title": title,
        "titleZh": "",
        "sentences": [
            {"id": f"{lesson_id}-S{index:03d}", "english": sentence, "chinese": ""}
            for index, sentence in enumerate(sentences, 1)
        ],
        "sourceName": path.name,
        "extraction": {
            "method": "embedded_text",
            "bodyFontSize": body_size,
            "bodyFontSizes": selected_sizes,
            "bodyFontFamily": body_family,
            "addedFontBlocks": [
                {"physicalPage": page, "side": side, "family": family}
                for page, side, family in sorted(selected_additions)
            ],
            "trimmedSupplementalSection": trimmed_supplemental,
            "expectedWordCount": expected,
            "extractedWordCount": actual,
            "qaStatus": "review" if reasons else "pass",
            "qaReasons": reasons,
            "pages": page_records,
        },
    }


def cache_path(cache_root: Path, path: Path) -> Path:
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    return cache_root / f"{digest}.json"


def upgrade_v10_lesson(lesson: dict[str, object]) -> dict[str, object] | None:
    extraction = lesson.get("extraction")
    if not isinstance(extraction, dict) or not isinstance(extraction.get("pages"), list):
        return None
    page_texts = [
        str(record.get("text", ""))
        for record in extraction["pages"]
        if isinstance(record, dict) and record.get("text")
    ]
    if not page_texts:
        return None
    sentences = split_sentences(normalize_line(" ".join(page_texts)))
    expected = extraction.get("expectedWordCount")
    expected_count = int(expected) if isinstance(expected, (int, float)) else None
    sentences, trimmed = trim_trailing_supplemental(sentences, expected_count)
    lesson_id = str(lesson["id"])
    actual = count_words(sentences)
    upgraded = dict(lesson)
    upgraded["sentences"] = [
        {"id": f"{lesson_id}-S{index:03d}", "english": sentence, "chinese": ""}
        for index, sentence in enumerate(sentences, 1)
    ]
    upgraded_extraction = dict(extraction)
    upgraded_extraction["extractedWordCount"] = actual
    upgraded_extraction["qaReasons"] = suspicious_reasons(sentences, expected_count, actual)
    upgraded_extraction["qaStatus"] = "review" if upgraded_extraction["qaReasons"] else "pass"
    if trimmed:
        upgraded_extraction["trimmedSupplementalSection"] = trimmed
    upgraded["extraction"] = upgraded_extraction
    return upgraded


def load_or_extract(path: Path, sequence: int, cache_root: Path, resume: bool) -> dict[str, object]:
    cached_path = cache_path(cache_root, path)
    stat = path.stat()
    if resume and cached_path.exists():
        cached = json.loads(cached_path.read_text(encoding="utf-8"))
        source = cached.get("_source", {})
        if (
            source.get("version") == EXTRACTOR_VERSION
            and source.get("size") == stat.st_size
            and source.get("mtime_ns") == stat.st_mtime_ns
        ):
            return cached["lesson"]
        if (
            source.get("version") == 10
            and EXTRACTOR_VERSION in {11, 12, 13, 14, 15}
            and source.get("size") == stat.st_size
            and source.get("mtime_ns") == stat.st_mtime_ns
        ):
            upgraded = upgrade_v10_lesson(cached["lesson"])
            if upgraded is not None:
                cached["_source"]["version"] = EXTRACTOR_VERSION
                cached["lesson"] = upgraded
                cached_path.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")
                return upgraded
        if (
            source.get("version") in {11, 12, 13}
            and EXTRACTOR_VERSION == 15
            and source.get("size") == stat.st_size
            and source.get("mtime_ns") == stat.st_mtime_ns
            and cached.get("lesson", {}).get("sentences")
            and cached.get("lesson", {}).get("extraction", {}).get("qaStatus") == "pass"
        ):
            cached["_source"]["version"] = EXTRACTOR_VERSION
            cached_path.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")
            return cached["lesson"]
    lesson = extract_one(path, sequence)
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    cached_path.write_text(
        json.dumps(
            {
                "_source": {
                    "path": str(path),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "version": EXTRACTOR_VERSION,
                },
                "lesson": lesson,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return lesson


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("RAZ Book"))
    parser.add_argument("--level", action="append", help="Only extract one or more levels")
    parser.add_argument("--file", action="append", type=Path, help="Extract an explicit PDF path")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--cache", type=Path, default=Path("tmp/raz-extraction/individual"))
    parser.add_argument("--output", type=Path, default=Path("tmp/raz-extraction/individual-library.json"))
    args = parser.parse_args()

    allowed = {item.upper() for item in args.level} if args.level else set(LETTERS) - {"D"}
    pdfs = list(args.file) if args.file else [
        path for path in sorted(args.root.rglob("*.pdf")) if level_from_path(path) in allowed
    ]
    if args.limit:
        pdfs = pdfs[: args.limit]

    lessons: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    per_level_sequence: Counter[str] = Counter()
    for index, path in enumerate(pdfs, 1):
        level = level_from_path(path)
        per_level_sequence[level] += 1
        try:
            lessons.append(load_or_extract(path, per_level_sequence[level], args.cache, args.resume))
        except Exception as exc:
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
        if index % 25 == 0 or index == len(pdfs):
            print(f"{index}/{len(pdfs)} lessons; errors={len(errors)}", flush=True)

    lessons.sort(key=lambda item: (LETTERS.index(str(item["level"])), str(item["id"]), str(item["title"])))
    summary = {
        "lessonCount": len(lessons),
        "sentenceCount": sum(len(item["sentences"]) for item in lessons),
        "passCount": sum(item["extraction"]["qaStatus"] == "pass" for item in lessons),
        "reviewCount": sum(item["extraction"]["qaStatus"] != "pass" for item in lessons),
        "errorCount": len(errors),
    }
    payload = {
        "format": "word-game-raz-course-library",
        "version": 1,
        "summary": summary,
        "lessons": lessons,
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
