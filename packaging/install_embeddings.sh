#!/usr/bin/env bash
# Optional (1.5.0+): the MiniLM sentence-embedding model for "For you".
#
# Without it the app uses its built-in, dependency-free similarity model. With
# it, interests and suggestions are matched by meaning (all-MiniLM-L6-v2 via
# fastembed / ONNX Runtime, on the CPU, no API keys, nothing leaves the box
# except the one-time download).
#
# Needs internet once (PyPI + Hugging Face). Uses ~300 MB of disk and about
# 150-250 MB of RAM while the app runs.
#   sudo bash /opt/homelib/install_embeddings.sh            install / repair
#   sudo bash /opt/homelib/install_embeddings.sh --remove   back to the built-in model
#
# The Python packages already installed keep their exact versions (they're
# passed as constraints), so the small update packages keep working.
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
need_root "$@"
PY="$PREFIX/venv/bin/python"
[ -x "$PY" ] || die "no install in $PREFIX (run install.sh first)"
MODELS="$DATA/models"

if [ "${1:-}" = "--remove" ]; then
  say "Removing fastembed and the model"
  "$PY" -m pip uninstall -q -y fastembed onnxruntime tokenizers huggingface-hub py-rust-stemmers loguru mmh3 \
    >/dev/null 2>&1 || true
  rm -rf "$MODELS"
  have_systemd && systemctl restart homelib && health_check || true
  say "Done. For you uses the built-in model again."
  exit 0
fi

say "Installing fastembed (keeps every installed package at its current version)"
cons=$(mktemp)
trap 'rm -f "$cons"' EXIT
"$PY" -m pip freeze --exclude-editable | grep -v '^-e ' > "$cons"
"$PY" -m pip install -q --disable-pip-version-check "fastembed>=0.4,<1" -c "$cons" \
  || die "pip couldn't install fastembed without changing the installed packages (see above)"

say "Downloading all-MiniLM-L6-v2 (~90 MB) into $MODELS"
install -d -o "$SVC_USER" -g "$SVC_USER" "$MODELS"
sudo -u "$SVC_USER" "$PY" - "$MODELS" <<'PY' || die "the model didn't download or load (check the internet connection and run this again)"
import sys
from fastembed import TextEmbedding
m = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2", cache_dir=sys.argv[1], threads=1)
a, b, c = [list(v) for v in m.embed(["black holes explained", "how stars die", "sourdough bread"])]
dot = lambda x, y: sum(p * q for p, q in zip(x, y))
assert dot(a, b) > dot(a, c), "the model loaded but gave odd results"
print(f"model ok ({len(a)} dimensions)")
PY

if [ "$(config_value recommender embedding_model auto)" = builtin ]; then
  warn "config.toml says embedding_model = \"builtin\": set it to \"auto\" to use MiniLM"
fi
have_systemd && systemctl restart homelib && health_check || true
say "Done. The next reload (or the early-morning run) re-embeds your history with MiniLM in the background."
say "Check: Settings → Your algorithm, at the bottom of Your interests: \"Similarity model: MiniLM\"."
