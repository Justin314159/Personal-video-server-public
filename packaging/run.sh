#!/usr/bin/env bash
# Run without systemd (testing, or a machine you don't want to install on).
# Creates ./.venv from the vendored wheels on first use. Data goes to ./data.
#   ./run.sh                 normal mode (binds 127.0.0.1:8787)
#   HOMELIB_MOCK=1 ./run.sh  fake YouTube, no internet needed
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
# Optional HOMELIB_* overrides (see .env.example).
if [ -f "$DIR/.env" ]; then set -a; . "$DIR/.env"; set +a; fi
VENV="$DIR/.venv"
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import fastapi, yt_dlp" 2>/dev/null; then
  PY=$(command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10)
  "$PY" -m venv --without-pip "$VENV"
  PIPWHL=$(ls "$DIR"/wheels/pip-*.whl | head -1)
  "$VENV/bin/python" "$PIPWHL/pip" install -q --no-index "$PIPWHL"
  CONS="$DIR/wheels/constraints-cp$("$VENV/bin/python" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")').txt"
  "$VENV/bin/python" -m pip install -q --no-index --find-links "$DIR/wheels" -c "$CONS" -r "$DIR/app/requirements.txt"
fi
export HOMELIB_CONFIG=${HOMELIB_CONFIG:-$DIR/config.toml}
[ -f "$HOMELIB_CONFIG" ] || cp "$DIR/config.example.toml" "$HOMELIB_CONFIG"
export HOMELIB_DATA_DIR=${HOMELIB_DATA_DIR:-$DIR/data}
export PATH="$VENV/bin:$PATH"
cd "$DIR/app" && exec "$VENV/bin/python" -m homelib
