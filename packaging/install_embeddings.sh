#!/usr/bin/env bash
# Optional (1.5.0+): the MiniLM sentence-embedding model for "For you".
#
# Without it the app uses its built-in, dependency-free similarity model. With
# it, interests and suggestions are matched by meaning (all-MiniLM-L6-v2 via
# fastembed / ONNX Runtime, on the CPU, no API keys).
#
# Needs internet once: the packages come from PyPI, the model (~90 MB, an ONNX
# file: weights only, no code) from Hugging Face (Qdrant/all-MiniLM-L6-v2-onnx).
# Uses ~300 MB of disk and about 150-250 MB of RAM while the app runs.
#   sudo bash /opt/homelib/install_embeddings.sh            install / repair
#   sudo bash /opt/homelib/install_embeddings.sh --remove   back to the built-in model
#
# Safety:
# * Every package is locked to an exact version and SHA-256 hash
#   (embeddings-pins/, made by scripts/pin_embeddings.py) and must be a
#   prebuilt wheel, so pip runs no package code while installing and refuses
#   anything that differs from what was pinned.
# * The packages the app already has are not touched, so the small update
#   packages keep working.
# * The model is downloaded once, by the unprivileged homelib user. The app
#   then loads it from disk only (HF_HUB_OFFLINE): it never contacts
#   Hugging Face again.
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
need_root "$@"
PY="$PREFIX/venv/bin/python"
[ -x "$PY" ] || die "no install in $PREFIX (run install.sh first)"
MODELS="$DATA/models"
TAG=$("$PY" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")')
PINS=""
for d in "$PKG_DIR/embeddings-pins" "$PREFIX/embeddings-pins"; do
  [ -f "$d/embeddings-cp$TAG.txt" ] && { PINS="$d/embeddings-cp$TAG.txt"; break; }
done
[ -n "$PINS" ] || die "no pinned package list for Python ${TAG:0:1}.${TAG:1} (embeddings-pins/embeddings-cp$TAG.txt)"
PKGS=$(sed -n 's/^\([A-Za-z0-9._-]*\)==.*/\1/p' "$PINS")

if [ "${1:-}" = "--remove" ]; then
  say "Removing fastembed and the model"
  # shellcheck disable=SC2086
  "$PY" -m pip uninstall -q -y $PKGS >/dev/null 2>&1 || true
  rm -rf "$MODELS"
  have_systemd && systemctl restart homelib && health_check || true
  say "Done. For you uses the built-in model again."
  exit 0
fi

say "Installing fastembed and $(($(echo "$PKGS" | wc -l) - 1)) packages it needs (exact versions, hash-checked)"
"$PY" -m pip install -q --disable-pip-version-check --no-deps --require-hashes --only-binary=:all: -r "$PINS" \
  || die "pip couldn't install the pinned packages (see above). Nothing the app uses was changed."
"$PY" -m pip check >/dev/null 2>&1 || warn "pip check reports a problem: $("$PY" -m pip check 2>&1 | head -3)"

say "Downloading all-MiniLM-L6-v2 (~90 MB) into $MODELS"
install -d -o "$SVC_USER" -g "$SVC_USER" "$MODELS"
sudo -u "$SVC_USER" env HF_HUB_DISABLE_TELEMETRY=1 "$PY" - "$MODELS" <<'PY' || die "the model didn't download or load (check the internet connection and run this again)"
import sys
from fastembed import TextEmbedding
m = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2", cache_dir=sys.argv[1], threads=1)
a, b, c = [list(v) for v in m.embed(["black holes explained", "how stars die", "sourdough bread"])]
dot = lambda x, y: sum(p * q for p, q in zip(x, y))
assert dot(a, b) > dot(a, c), "the model loaded but gave odd results"
# The app loads it like this: from disk only.
TextEmbedding("sentence-transformers/all-MiniLM-L6-v2", cache_dir=sys.argv[1], threads=1, local_files_only=True)
print(f"model ok ({len(a)} dimensions)")
PY

if [ "$(config_value recommender embedding_model auto)" = builtin ]; then
  warn "config.toml says embedding_model = \"builtin\": set it to \"auto\" to use MiniLM"
fi
have_systemd && systemctl restart homelib && health_check || true
say "Done. The next reload (or the early-morning run) re-embeds your history with MiniLM in the background."
say "Check: Settings → Your algorithm, at the bottom of Your interests: \"Similarity model: MiniLM\"."
