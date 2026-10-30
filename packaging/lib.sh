# Shared helpers for the homelib shell scripts. Sourced, not executed.
set -euo pipefail

PREFIX=/opt/homelib
ETC=/etc/homelib
DATA=/var/lib/homelib
SVC_USER=homelib
UNIT=/etc/systemd/system/homelib.service
PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

c_red=$'\e[31m'; c_grn=$'\e[32m'; c_yel=$'\e[33m'; c_bld=$'\e[1m'; c_off=$'\e[0m'
say()  { echo "${c_grn}==>${c_off} $*"; }
warn() { echo "${c_yel}WARNING:${c_off} $*" >&2; }
die()  { echo "${c_red}ERROR:${c_off} $*" >&2; exit 1; }

need_root() { [ "$(id -u)" -eq 0 ] || die "run with sudo: sudo $0 $*"; }

have_systemd() { [ -d /run/systemd/system ] && command -v systemctl >/dev/null; }

verify_manifest() {
  say "Verifying package integrity (SHA-256 manifest)…"
  [ -f "$PKG_DIR/MANIFEST.sha256" ] || die "MANIFEST.sha256 missing: package is incomplete"
  ( cd "$PKG_DIR" && sha256sum --quiet --strict -c MANIFEST.sha256 ) \
    || die "checksum mismatch: the USB copy is corrupt or was modified. Re-copy the zip."
  # Every file in the package must be listed (except the manifest itself).
  local extra
  # LC_ALL=C: sort and comm must agree on the order whatever the system language is.
  # Python caches (from running selfcheck.sh in the package first) are not extras.
  extra=$(cd "$PKG_DIR" && find . -type f ! -name MANIFEST.sha256 ! -path '*/__pycache__/*' | sed 's|^\./||' | LC_ALL=C sort \
          | LC_ALL=C comm -23 - <(awk '{print $2}' MANIFEST.sha256 | sed 's|^\*||' | LC_ALL=C sort)) || true
  [ -z "$extra" ] || die "files not in the manifest: $extra"
  say "All $(wc -l < "$PKG_DIR/MANIFEST.sha256") files verified."
}

# Newest Python 3.10+ on this machine that the package has wheels for
# (Ubuntu 22.04: 3.10, 24.04: 3.12, 26.04: 3.13/3.14).
pick_python() {
  local py tag
  for py in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    command -v "$py" >/dev/null || continue
    tag=$("$py" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}" if sys.version_info >= (3,10) else "")' 2>/dev/null) || continue
    if [ -n "$tag" ] && [ -f "$PKG_DIR/wheels/constraints-cp$tag.txt" ]; then
      echo "$py"; return
    fi
  done
  die "no supported Python found (need 3.10–3.14; found: $(python3 --version 2>&1))"
}

# Create/refresh the venv using only the vendored wheels (no internet).
install_venv() {
  local py; py=$(pick_python)
  say "Using $($py --version) at $(command -v "$py")"
  if [ ! -x "$PREFIX/venv/bin/python" ]; then
    # Fresh Ubuntu lacks python3-venv/ensurepip; create without pip and bootstrap
    # pip from the vendored wheel instead.
    "$py" -m venv --without-pip "$PREFIX/venv" || die "python venv module missing"
  fi
  if ! "$PREFIX/venv/bin/python" -m pip --version >/dev/null 2>&1; then
    local pipwhl; pipwhl=$(ls "$PKG_DIR"/wheels/pip-*.whl | head -1)
    "$PREFIX/venv/bin/python" "$pipwhl/pip" install -q --no-index "$pipwhl"
  fi
  say "Installing Python packages from vendored wheels (offline)…"
  local cons; cons="$PKG_DIR/wheels/constraints-cp$("$PREFIX/venv/bin/python" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")').txt"
  [ -f "$cons" ] || die "no wheels for this Python version (need 3.10–3.14)"
  "$PREFIX/venv/bin/python" -m pip install -q --no-index --find-links "$PKG_DIR/wheels" \
      --disable-pip-version-check --upgrade -c "$cons" -r "$PKG_DIR/app/requirements.txt"
}

# Small update packages (homelib-update.zip) carry no wheels, only the exact
# versions the app was built with (requires/constraints-cp3XX.txt). They can be
# applied when the installed venv has every one of them at that version or
# newer (newer = e.g. after update.sh --yt-dlp-online). Prints what's missing.
venv_has_requirements() {
  [ -x "$PREFIX/venv/bin/python" ] || { echo "no existing install in $PREFIX"; return 1; }
  local tag; tag=$("$PREFIX/venv/bin/python" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")')
  local cons="$PKG_DIR/requires/constraints-cp$tag.txt"
  [ -f "$cons" ] || { echo "this package doesn't support the server's Python $tag"; return 1; }
  "$PREFIX/venv/bin/python" - "$cons" <<'PY'
import re, sys
from importlib.metadata import distributions
try:
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.version import Version

norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
have = {norm(d.metadata["Name"]): d.version for d in distributions()}
missing = []
for line in open(sys.argv[1]):
    name, sep, want = line.strip().partition("==")
    if not sep or norm(name) == "pip":
        continue
    got = have.get(norm(name))
    if got is None:
        missing.append(f"{name} {want} (not installed)")
    elif Version(got) < Version(want):
        missing.append(f"{name} {want} (server has {got})")
print("\n".join(missing))
sys.exit(1 if missing else 0)
PY
}

install_app() {
  say "Installing application to $PREFIX/app"
  rm -rf "$PREFIX/app.new"
  mkdir -p "$PREFIX/app.new"
  cp -r "$PKG_DIR/app/homelib" "$PKG_DIR/app/requirements.txt" "$PREFIX/app.new/"
  rm -rf "$PREFIX/app.old"
  [ -d "$PREFIX/app" ] && mv "$PREFIX/app" "$PREFIX/app.old"
  mv "$PREFIX/app.new" "$PREFIX/app"
  cp "$PKG_DIR"/{update.sh,uninstall.sh,backup.sh,selfcheck.sh,install_embeddings.sh,lib.sh,VERSION} "$PREFIX/" 2>/dev/null || true
  if [ -d "$PKG_DIR/embeddings-pins" ]; then  # for install_embeddings.sh (1.5.0+)
    rm -rf "$PREFIX/embeddings-pins" && cp -r "$PKG_DIR/embeddings-pins" "$PREFIX/"
  fi
  chmod 755 "$PREFIX"/*.sh
}

config_value() {  # config_value section key default
  "$PREFIX/venv/bin/python" - "$ETC/config.toml" "$1" "$2" "$3" <<'PY'
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib
path, sec, key, default = sys.argv[1:]
try:
    d = tomllib.load(open(path, "rb"))
    print(d.get(sec, {}).get(key, default))
except Exception:
    print(default)
PY
}

health_check() {
  local port; port=$(config_value server port 8787)
  local bind; bind=$(config_value server bind 127.0.0.1)
  local host=127.0.0.1
  case "$bind" in 127.0.0.1|0.0.0.0|"") host=127.0.0.1 ;; tailscale) host=$(tailscale ip -4 2>/dev/null | head -1 || echo 127.0.0.1) ;; *) host=$bind ;; esac
  for _ in $(seq 1 30); do
    if curl -fsS "http://$host:$port/api/health" >/dev/null 2>&1 \
       || "$PREFIX/venv/bin/python" -c "import urllib.request,sys; urllib.request.urlopen('http://$host:$port/api/health', timeout=2)" 2>/dev/null; then
      say "Server is up at http://$host:$port"; return 0
    fi
    sleep 1
  done
  warn "server did not answer on http://$host:$port yet. Check: journalctl -u homelib -n 50"
  return 1
}
