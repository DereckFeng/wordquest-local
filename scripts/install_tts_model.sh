#!/bin/zsh
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${WORDQUEST_PYTHON:-python3}"
MODEL_DIR="$ROOT/models/kokoro"
VENV="$ROOT/.tts-venv"

"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Kokoro requires Python 3.10 or newer"'
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install kokoro-onnx

mkdir -p "$MODEL_DIR"
curl --fail --location --progress-bar \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx \
  --output "$MODEL_DIR/kokoro-v1.0.int8.onnx"
curl --fail --location --progress-bar \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin \
  --output "$MODEL_DIR/voices-v1.0.bin"

echo "Kokoro-82M unified voice is installed."
