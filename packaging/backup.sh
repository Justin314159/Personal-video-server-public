#!/usr/bin/env bash
# Back up the database (online, consistent), config and profile photos. Videos are
# not backed up: they are temporary by design. Keeps the newest 14 backups.
#   sudo ./backup.sh [--quiet] [DEST_DIR]
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
need_root "$@"
QUIET=0; [ "${1:-}" = "--quiet" ] && { QUIET=1; shift; }
DEST=${1:-/var/backups/homelib}
mkdir -p "$DEST"; chmod 700 "$DEST"
PY="$PREFIX/venv/bin/python"; [ -x "$PY" ] || PY=python3
DATA_DIR=$( [ -x "$PREFIX/venv/bin/python" ] && config_value library data_dir "$DATA" || echo "$DATA")
STAMP=$(date +%Y%m%d-%H%M%S)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
"$PY" - "$DATA_DIR/homelib.db" "$TMP/homelib.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1:]
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
d = sqlite3.connect(dst)
s.backup(d)   # safe while the server is running (WAL)
d.close(); s.close()
PY
cp "$ETC/config.toml" "$TMP/" 2>/dev/null || true
[ -d "$DATA_DIR/avatars" ] && cp -r "$DATA_DIR/avatars" "$TMP/"
tar -C "$TMP" -czf "$DEST/homelib-$STAMP.tar.gz" .
ls -1t "$DEST"/homelib-*.tar.gz | tail -n +15 | xargs -r rm -f
[ "$QUIET" = 1 ] || say "Backup written: $DEST/homelib-$STAMP.tar.gz"
# Restore: sudo systemctl stop homelib; tar -xzf FILE -C /tmp/r; cp /tmp/r/homelib.db $DATA_DIR/;
#          cp -r /tmp/r/avatars $DATA_DIR/ (if present); chown -R homelib: $DATA_DIR/homelib.db $DATA_DIR/avatars;
#          sudo systemctl start homelib
