#!/usr/bin/env python3
"""Create padded, sentence-sized MFA windows from quarantined lesson cuts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "tmp/mfa-pilot"
CORPUS = PILOT / "sentence-corpus"
REJECTED = ROOT / "tmp/raz-audio-rejected"
FFMPEG = ROOT / ".audio-venv/bin/ffmpeg"
PADDING = 1.5


def main() -> None:
    pilot = json.loads((PILOT / "manifest.json").read_text(encoding="utf-8"))
    CORPUS.mkdir(parents=True, exist_ok=True)
    windows = []
    skipped = []
    for lesson in pilot["lessons"]:
        report_path = REJECTED / lesson["lessonId"] / "failure-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        clips = report.get("clips", [])
        if not clips:
            skipped.append({"lessonId": lesson["lessonId"], "reason": "no-coarse-boundaries"})
            continue
        source = ROOT / report["sourceAudio"]
        for clip in clips:
            key = f'{lesson["lessonId"]}__{clip["sentenceId"].split("-")[-1]}'
            start = max(0.0, float(clip["start"]) - PADDING)
            end = float(clip["end"]) + PADDING
            wav = CORPUS / f"{key}.wav"
            lab = CORPUS / f"{key}.lab"
            subprocess.run([
                str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end-start:.3f}",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
            ], check=True)
            lab.write_text(clip["english"].strip() + "\n", encoding="utf-8")
            windows.append({
                "key": key,
                "lessonId": lesson["lessonId"],
                "sentenceId": clip["sentenceId"],
                "reason": lesson["reason"],
                "sourceStart": start,
                "sourceEnd": end,
                "coarseStart": clip["start"],
                "coarseEnd": clip["end"],
                "english": clip["english"],
            })
    payload = {
        "format": "wordquest-mfa-sentence-pilot",
        "paddingSeconds": PADDING,
        "windowCount": len(windows),
        "lessonCount": len({row["lessonId"] for row in windows}),
        "skippedLessons": skipped,
        "windows": windows,
    }
    (PILOT / "sentence-manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: payload[key] for key in ("windowCount", "lessonCount", "skippedLessons")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
