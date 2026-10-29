#!/usr/bin/env bash
# End-to-end check of an update, on a THROWAWAY Linux machine or container
# (never on the real server): installs the previous release into /opt/homelib,
# runs it once to create its database, applies the new small update package,
# and checks the result. Then cleans up.
#   sudo bash scripts/test_update_e2e.sh OLD_homelib-usb.zip [NEW_homelib-update.zip]
# OLD: the previous Release's homelib-usb.zip. NEW defaults to
# dist/homelib-update.zip (bash scripts/build_usb.sh builds it).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OLD=$(realpath "$1"); NEW=$(realpath "${2:-$ROOT/dist/homelib-update.zip}")
[ "$(id -u)" -eq 0 ] || { echo "run as root (throwaway machine only)" >&2; exit 1; }
for d in /opt/homelib /etc/homelib /var/lib/homelib; do
  [ ! -e "$d" ] || { echo "$d exists: this looks like a real install, refusing" >&2; exit 1; }
done
T=$(mktemp -d)
PID=
cleanup() { [ -z "$PID" ] || kill "$PID" 2>/dev/null || true
            rm -rf "$T" /opt/homelib /etc/homelib /var/lib/homelib /var/backups/homelib; }
trap cleanup EXIT
run_once() {  # start the installed app (mock YouTube), wait for it, then stop it
  (cd /opt/homelib/app && exec env HOMELIB_MOCK=1 HOMELIB_CONFIG=/etc/homelib/config.toml \
     /opt/homelib/venv/bin/python -m homelib) >"$T/run.log" 2>&1 &
  PID=$!
  for _ in $(seq 30); do curl -fsS 127.0.0.1:8787/api/health >/dev/null 2>&1 && break; sleep 1; done
  curl -fsS 127.0.0.1:8787/api/status | grep -o '"version":"[^"]*"' || { cat "$T/run.log"; exit 1; }
  kill "$PID"; wait "$PID" 2>/dev/null || true; PID=
}

echo "== install previous release (core steps of install.sh: no swap, user or systemd unit)"
python3 -m zipfile -e "$OLD" "$T/old"
(cd "$T/old/homelib-usb" && bash -c 'source ./lib.sh; mkdir -p $PREFIX $ETC $DATA
  install -m 644 config.example.toml $ETC/config.toml; install_app; install_venv' >/dev/null)
echo "installed $(cat /opt/homelib/VERSION)"; run_once

echo "== apply the small update"
python3 -m zipfile -e "$NEW" "$T/new"
(cd "$T/new/homelib-update" && bash update.sh)
[ "$(cat /opt/homelib/VERSION)" = "$(cat "$T/new/homelib-update/VERSION")" ] || { echo "FAIL: version not updated" >&2; exit 1; }
ls /var/backups/homelib/homelib-*.tar.gz >/dev/null || { echo "FAIL: no pre-update backup" >&2; exit 1; }
run_once
echo "PASS: small update from $(cat "$T/old/homelib-usb/VERSION") to $(cat /opt/homelib/VERSION)"
