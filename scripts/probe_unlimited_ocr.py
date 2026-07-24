#!/usr/bin/env python3
"""Send one or more page images to the LAN-hosted Unlimited-OCR model."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path, nargs="+")
    parser.add_argument("--server", default="http://192.168.18.12:1234")
    parser.add_argument("--model", default="unlimited-ocr")
    parser.add_argument("--prompt", default="document parsing.")
    args = parser.parse_args()

    content = [{"type": "text", "text": args.prompt}]
    for image in args.image:
        mime = mimetypes.guess_type(image.name)[0] or "image/jpeg"
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    payload = {
        "model": args.model,
        "temperature": 0,
        "max_tokens": 4096,
        "messages": [{
            "role": "user",
            "content": content,
        }],
    }
    request = urllib.request.Request(
        f"{args.server.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        result = json.load(response)
    print(result["choices"][0]["message"]["content"])


if __name__ == "__main__":
    main()
