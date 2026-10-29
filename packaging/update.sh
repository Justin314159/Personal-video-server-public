#!/usr/bin/env bash
# Update from a newer USB package (run the update.sh INSIDE the new package):
#   sudo ./update.sh
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

[ -d "$PKG_DIR/wheels" ] || die "run the update.sh from the NEW package (USB), not $PREFIX"
verify_manifest
say "Backing up before update…"
"$PKG_DIR/backup.sh" --quiet || warn "backup failed, continuing"
have_systemd && systemctl stop homelib || true
install_app
install_venv
have_systemd && systemctl start homelib && health_check || true
say "Updated to $(cat "$PKG_DIR/VERSION"). Previous app kept at $PREFIX/app.old"
