#!/usr/bin/env bash
# Hardware/tool/config report. Works before install (system python) and after.
#   ./selfcheck.sh [--json]
DIR="$(cd "$(dirname "$0")" && pwd)"
export HOMELIB_CONFIG=${HOMELIB_CONFIG:-/etc/homelib/config.toml}
if [ -x /opt/homelib/venv/bin/python ] && [ -d /opt/homelib/app/homelib ]; then
  cd /opt/homelib/app && exec /opt/homelib/venv/bin/python -m homelib selfcheck "$@"
fi
APP="$DIR/app"; [ -d "$APP/homelib" ] || APP="$DIR/../backend"
PY=$(command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
[ -n "$PY" ] || { echo "FAIL: no python3"; exit 1; }
cd "$APP" && exec "$PY" -m homelib.selfcheck "$@"
