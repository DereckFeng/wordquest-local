#!/usr/bin/env python3
"""Small dependency-free client for a Qwen3-ASR OpenAI-compatible server."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


class QwenAsrError(RuntimeError):
    pass


def parse_qwen_asr_output(value: str) -> str:
    """Return only the transcript from Qwen's optional language metadata wrapper."""

    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"<asr_text>", text, flags=re.IGNORECASE)
    if match:
        metadata = text[: match.start()].lower()
        transcript = text[match.end() :].strip()
        if "language none" in metadata and not transcript:
            return ""
        return transcript
    return re.sub(r"^language\s+[A-Za-z(), -]+\s*", "", text, count=1).strip()


class QwenAsrClient:
    def __init__(self, server_url: str, model: str, timeout: float = 300.0):
        base = server_url.rstrip("/")
        if base.endswith("/chat/completions"):
            self.endpoint = base
        elif base.endswith("/v1"):
            self.endpoint = f"{base}/chat/completions"
        else:
            self.endpoint = f"{base}/v1/chat/completions"
        self.model = model
        self.timeout = timeout

    def transcribe(self, audio: Path) -> str:
        mime = mimetypes.guess_type(audio.name)[0] or "audio/mpeg"
        encoded = base64.b64encode(audio.read_bytes()).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "audio_url",
                            "audio_url": {"url": f"data:{mime};base64,{encoded}"},
                        }
                    ],
                }
            ],
            "temperature": 0,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise QwenAsrError(
                f"Qwen3-ASR rejected audio at {self.endpoint}: HTTP {error.code}: {detail}"
            ) from error
        except (OSError, ValueError) as error:
            raise QwenAsrError(
                f"Qwen3-ASR request failed at {self.endpoint}: {error}"
            ) from error

        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise QwenAsrError(
                f"Qwen3-ASR returned an unexpected response: {json.dumps(result, ensure_ascii=False)}"
            ) from error
        return parse_qwen_asr_output(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--server-url", default="http://192.168.18.12:1234")
    parser.add_argument("--model", default="qwen3-asr-1.7b")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()
    try:
        print(QwenAsrClient(args.server_url, args.model, args.timeout).transcribe(args.audio))
        return 0
    except QwenAsrError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
