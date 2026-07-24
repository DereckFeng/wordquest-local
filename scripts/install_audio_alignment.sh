#!/bin/zsh
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${WORDQUEST_PYTHON:-python3}"
VENV="$ROOT/.audio-venv"
MODEL_DIR="$ROOT/models/faster-whisper-base.en"

"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Audio alignment requires Python 3.10 or newer"'
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install faster-whisper imageio-ffmpeg stable-ts-whisperless==2.19.1

FFMPEG_BINARY="$($VENV/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
ln -sfn "$FFMPEG_BINARY" "$VENV/bin/ffmpeg"

if [[ ! -f "$MODEL_DIR/model.bin" ]]; then
  "$VENV/bin/hf" download Systran/faster-whisper-base.en --local-dir "$MODEL_DIR"
fi

echo "Local RAZ audio alignment tools are installed."
