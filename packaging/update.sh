#!/usr/bin/env bash
# Update from a newer package (run the update.sh INSIDE the new package):
#   sudo ./update.sh
# Works with either package:
#   homelib-usb.zip     full: app + all Python packages (always works)
#   homelib-update.zip  small: app only, reuses the Python packages already
#                       installed if they're the versions it needs (else it
#                       stops before changing anything; use the full one)
# Or update only yt-dlp from the internet (YouTube changes often!):
#   sudo /opt/homelib/update.sh --yt-dlp-online
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
need_root "$@"

if [ "${1:-}" = "--yt-dlp-online" ]; then
  say "Updating yt-dlp (+ JS solver, deno) from PyPI…"
  "$PREFIX/venv/bin/python" -m pip install -q --upgrade "yt-dlp[default,deno]"
  "$PREFIX/venv/bin/python" -c "import yt_dlp.version as v; print('yt-dlp', v.__version__)"
  have_systemd && systemctl restart homelib && health_check || true
  exit 0
fi

if [ -d "$PKG_DIR/wheels" ]; then
  MODE=full
elif [ -d "$PKG_DIR/requires" ]; then
  MODE=small
else
  die "run the update.sh from the NEW package (USB), not $PREFIX"
fi
verify_manifest
if [ "$MODE" = small ]; then
  say "Small update: checking the Python packages already on this server…"
  if ! missing=$(venv_has_requirements); then
    die "this small update can't be used here, nothing was changed:
$missing
Use the full package (homelib-usb.zip) instead, same steps."
  fi
  say "All needed packages are already installed."
fi
say "Backing up before update…"
bash "$PKG_DIR/backup.sh" --quiet || warn "backup failed, continuing"  # bash: unzipping drops the +x bit
have_systemd && systemctl stop homelib || true
install_app
[ "$MODE" = full ] && install_venv
have_systemd && systemctl start homelib && health_check || true
say "Updated to $(cat "$PKG_DIR/VERSION"). Previous app kept at $PREFIX/app.old"
