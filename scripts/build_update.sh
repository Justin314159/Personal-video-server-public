#!/usr/bin/env bash
# Build dist/homelib-update.zip, the small update package, from a full package
# folder (dist/homelib-usb, made by build_usb.sh, which calls this at the end).
# Same app, scripts and docs, but no wheels: requires/ lists the exact Python
# package versions instead, and update.sh only applies it when the server
# already has those (see venv_has_requirements in packaging/lib.sh).
#   bash scripts/build_update.sh [FULL_PACKAGE_DIR]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist"
FULL="${1:-$OUT/homelib-usb}"
STAGE="$OUT/homelib-update"
[ -d "$FULL/wheels" ] || { echo "not a full package: $FULL" >&2; exit 1; }

rm -rf "$STAGE" "$OUT/homelib-update.zip"
mkdir -p "$STAGE/requires"
cp -r "$FULL/app" "$FULL/docs" "$FULL/VERSION" "$STAGE/"
[ -d "$FULL/updates" ] && cp -r "$FULL/updates" "$STAGE/"
# install.sh and run.sh need the wheels, so they're full-package only.
cp "$FULL"/{update.sh,uninstall.sh,backup.sh,selfcheck.sh,lib.sh,config.example.toml} "$STAGE/"
cp "$FULL"/wheels/constraints-cp*.txt "$STAGE/requires/"
chmod 755 "$STAGE"/*.sh
( cd "$STAGE" && find . -type f ! -name MANIFEST.sha256 | sed 's|^\./||' | LC_ALL=C sort | xargs sha256sum > MANIFEST.sha256 )
( cd "$OUT" && zip -q -r -X homelib-update.zip homelib-update )
ls -lh "$OUT/homelib-update.zip"
