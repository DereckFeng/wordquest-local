#!/bin/zsh
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="$ROOT/.tts-venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

"$PYTHON" "$ROOT/scripts/tts_server.py" &
TTS_PID=$!

cleanup() {
  kill "$TTS_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

export WRANGLER_LOG_PATH="$ROOT/.wrangler/wrangler.log"
"$ROOT/node_modules/.bin/vinext" dev "$@"
