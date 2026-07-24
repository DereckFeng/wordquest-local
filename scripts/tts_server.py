#!/usr/bin/env python3
"""WordQuest LAN text-to-speech server.

Kokoro ONNX is used when the local model is installed. On macOS, the service
can still provide one centralized system voice while Kokoro is unavailable, so
student devices never need to choose their own browser voice.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "kokoro" / "kokoro-v1.0.int8.onnx"
VOICES_PATH = ROOT / "models" / "kokoro" / "voices-v1.0.bin"
CACHE_DIR = ROOT / ".tts-cache"
HOST = os.environ.get("WORDQUEST_TTS_HOST", "0.0.0.0")
PORT = int(os.environ.get("WORDQUEST_TTS_PORT", "3001"))
VOICE = os.environ.get("WORDQUEST_TTS_VOICE", "af_bella")
MAX_TEXT_LENGTH = 1_500


class SpeechEngine:
    def __init__(self) -> None:
        self._kokoro = None
        self._lock = threading.Lock()
        self.engine = "服务器语音"
        self.voice = "Samantha"
        if MODEL_PATH.exists() and VOICES_PATH.exists():
            try:
                from kokoro_onnx import Kokoro

                self._kokoro = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
                self.engine = "Kokoro-82M"
                self.voice = VOICE
            except Exception as error:
                print(f"Kokoro unavailable, using centralized macOS voice: {error}", flush=True)
        if self._kokoro is None and not (shutil.which("say") and shutil.which("afconvert")):
            raise RuntimeError("Kokoro model is not installed and no macOS voice engine is available")

    def create_wav(self, text: str, slow: bool) -> bytes:
        speed = 0.78 if slow else 0.95
        cache_key = hashlib.sha256(f"{self.engine}\0{self.voice}\0{speed}\0{text}".encode()).hexdigest()
        cache_path = CACHE_DIR / f"{cache_key}.wav"
        if cache_path.exists():
            return cache_path.read_bytes()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if cache_path.exists():
                return cache_path.read_bytes()
            audio = self._kokoro_wav(text, speed) if self._kokoro is not None else self._macos_wav(text, slow)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_bytes(audio)
            temporary.replace(cache_path)
            return audio

    def _kokoro_wav(self, text: str, speed: float) -> bytes:
        import numpy as np

        samples, sample_rate = self._kokoro.create(text, voice=VOICE, speed=speed, lang="en-us")
        pcm = (np.clip(np.asarray(samples), -1, 1) * 32767).astype("<i2")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate))
            wav.writeframes(pcm.tobytes())
        return output.getvalue()

    def _macos_wav(self, text: str, slow: bool) -> bytes:
        rate = "145" if slow else "180"
        with tempfile.TemporaryDirectory(prefix="wordquest-tts-") as directory:
            aiff = Path(directory) / "speech.aiff"
            wav = Path(directory) / "speech.wav"
            subprocess.run(["say", "-v", "Samantha", "-r", rate, "-o", str(aiff), text], check=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@22050", str(aiff), str(wav)], check=True)
            return wav.read_bytes()


ENGINE = SpeechEngine()


class Handler(BaseHTTPRequestHandler):
    server_version = "WordQuestTTS/1.0"

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "X-TTS-Engine, X-TTS-Voice")

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.rstrip("/") != "/health":
            self._json(404, {"error": "not found"})
            return
        self._json(200, {"ok": True, "engine": ENGINE.engine, "voice": ENGINE.voice})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/speak":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 10_000:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "") if isinstance(body, dict) else ""
            slow = bool(body.get("slow", False)) if isinstance(body, dict) else False
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
                raise ValueError("invalid text")
            audio = ENGINE.create_wav(text.strip(), slow)
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("X-TTS-Engine", ENGINE.engine)
            self.send_header("X-TTS-Voice", ENGINE.voice)
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
        except (ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
        except Exception as error:
            print(f"TTS request failed: {error}", flush=True)
            self._json(500, {"error": "speech generation failed"})

    def log_message(self, format: str, *args: object) -> None:
        print(f"TTS {self.address_string()} - {format % args}", flush=True)


if __name__ == "__main__":
    print(f"WordQuest unified voice: {ENGINE.engine} · {ENGINE.voice}", flush=True)
    print(f"TTS service: http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
