#!/usr/bin/env bash
# Build dist/homelib-usb.zip: app + vendored wheels (cp310-cp314, x86_64) +
# scripts + docs + SHA-256 manifest. Needs internet (PyPI) to fetch wheels.
# Also builds dist/homelib-update.zip, the small app-only update (build_update.sh).
#
# Python package versions come from packaging/pins/constraints-cp3XX.txt, so
# every build bundles exactly the same packages and the small update package
# keeps working. To deliberately move to newer packages (e.g. a yt-dlp fix):
#   UPDATE_PINS=1 bash scripts/build_usb.sh
# rewrites the pins with the newest versions; commit them, and say in
# updates/<version>.md that this release needs the full package.
# Needs updates/<version>.md and its row in updates/README.md (see CLAUDE.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist"
STAGE="$OUT/homelib-usb"
WHEELS_CACHE="${WHEELS_CACHE:-$ROOT/build/wheels}"
PY="${PYTHON:-python3}"  # any Python with pip; it only downloads wheels
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/backend/homelib/__init__.py")
PINS="$ROOT/packaging/pins"
UPDATE_PINS="${UPDATE_PINS:-0}"
fail() { echo "ERROR: $*" >&2; exit 1; }

[ -f "$ROOT/updates/$VERSION.md" ] || fail "no updates/$VERSION.md: every release needs its update page (see CLAUDE.md)"
grep -qF "[$VERSION]($VERSION.md)" "$ROOT/updates/README.md" || fail "add $VERSION to the table in updates/README.md"
if [ "$UPDATE_PINS" != 1 ]; then
  for v in 310 311 312 313 314; do
    [ -f "$PINS/constraints-cp$v.txt" ] || fail "missing packaging/pins/constraints-cp$v.txt (UPDATE_PINS=1 creates it)"
  done
fi
pin_args() {  # pin_args 3.12 -> -c packaging/pins/constraints-cp312.txt (nothing with UPDATE_PINS=1)
  [ "$UPDATE_PINS" = 1 ] || echo "-c $PINS/constraints-cp${1/./}.txt"
}

rm -rf "$STAGE" "$OUT/homelib-usb.zip"
mkdir -p "$STAGE/app" "$STAGE/wheels" "$STAGE/docs" "$WHEELS_CACHE"

echo "== wheels"
PLAT=()
for p in manylinux2014_x86_64 manylinux_2_17_x86_64 manylinux_2_24_x86_64 manylinux_2_27_x86_64 \
         manylinux_2_28_x86_64 manylinux_2_31_x86_64 manylinux_2_34_x86_64 manylinux_2_35_x86_64; do
  PLAT+=(--platform "$p")
done
for v in 3.10 3.11 3.12 3.13 3.14; do
  extra=(); [ "$v" = "3.10" ] && extra=(tomli exceptiongroup)  # markers for <3.11 are not evaluated by pip download
  # shellcheck disable=SC2046
  "$PY" -m pip download -q --disable-pip-version-check -r "$ROOT/backend/requirements.txt" pip "${extra[@]}" \
    $(pin_args "$v") --only-binary=:all: "${PLAT[@]}" --python-version "$v" --implementation cp -d "$WHEELS_CACHE"
done
# Keep only what the five resolutions need (cache may hold stale versions), and
# write exact pins per Python version: pip's offline resolver doesn't skip wheels
# whose Requires-Python excludes the running interpreter, so install.sh passes
# wheels/constraints-cp3XX.txt.
rm -f "$STAGE"/wheels/*
for v in 3.10 3.11 3.12 3.13 3.14; do
  extra=(); [ "$v" = "3.10" ] && extra=(tomli exceptiongroup)  # markers for <3.11 are not evaluated by pip download
  tmp=$(mktemp -d)
  # shellcheck disable=SC2046
  "$PY" -m pip download -q --disable-pip-version-check --no-index --find-links "$WHEELS_CACHE" \
    -r "$ROOT/backend/requirements.txt" pip "${extra[@]}" $(pin_args "$v") --only-binary=:all: "${PLAT[@]}" \
    --python-version "$v" --implementation cp -d "$tmp"
  ls "$tmp" | awk -F- '{ n=$1; gsub("_","-",n); print tolower(n) "==" $2 }' | sort > "$STAGE/wheels/constraints-cp${v/./}.txt"
  cp -n "$tmp"/*.whl "$STAGE/wheels/"
  rm -rf "$tmp"
  if [ "$UPDATE_PINS" = 1 ]; then
    mkdir -p "$PINS" && cp "$STAGE/wheels/constraints-cp${v/./}.txt" "$PINS/"
  else
    cmp -s "$STAGE/wheels/constraints-cp${v/./}.txt" "$PINS/constraints-cp${v/./}.txt" \
      || fail "Python $v resolved to other versions than packaging/pins (diff them; UPDATE_PINS=1 to accept)"
  fi
done
[ "$UPDATE_PINS" = 1 ] && echo "== pins updated in packaging/pins: commit them; this release needs the full package"

echo "== app"
cp -r "$ROOT/backend/homelib" "$STAGE/app/"
cp "$ROOT/backend/requirements.txt" "$STAGE/app/"
find "$STAGE/app" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$ROOT"/packaging/{install.sh,update.sh,uninstall.sh,backup.sh,selfcheck.sh,install_embeddings.sh,run.sh,lib.sh,config.example.toml,PROMPT.md} "$STAGE/"
cp "$ROOT"/docs/*.md "$STAGE/docs/"
mkdir -p "$STAGE/updates" && cp "$ROOT"/updates/*.md "$STAGE/updates/"
echo "$VERSION" > "$STAGE/VERSION"
chmod 755 "$STAGE"/*.sh

echo "== manifest"
( cd "$STAGE" && find . -type f ! -name MANIFEST.sha256 | sed 's|^\./||' | LC_ALL=C sort | xargs sha256sum > MANIFEST.sha256 )

echo "== zip"
( cd "$OUT" && zip -q -r -X homelib-usb.zip homelib-usb )
ls -lh "$OUT/homelib-usb.zip"
echo "files: $(wc -l < "$STAGE/MANIFEST.sha256")  wheels: $(ls "$STAGE/wheels" | wc -l)"

echo "== small update package"
bash "$ROOT/scripts/build_update.sh" "$STAGE"
