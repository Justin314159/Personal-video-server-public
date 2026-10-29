#!/usr/bin/env bash
# Remove Home Video Library.
#   sudo ./uninstall.sh           remove the program, KEEP videos, database and config
#   sudo ./uninstall.sh --purge   also delete videos, database, config and the user
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
need_root "$@"
PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1

if have_systemd; then
  systemctl disable --now homelib 2>/dev/null || true
fi
rm -f "$UNIT" /etc/cron.d/homelib-backup
have_systemd && systemctl daemon-reload || true

if [ "$PURGE" = 1 ]; then
  LIB_DIR=$(config_value library dir "$DATA/library" 2>/dev/null || echo "$DATA/library")
  read -r -p "Delete ALL videos ($LIB_DIR), the database ($DATA) and $ETC? Type 'yes': " ans
  [ "$ans" = "yes" ] || die "aborted"
  rm -rf "$LIB_DIR" "$DATA" "$ETC"
  userdel "$SVC_USER" 2>/dev/null || true
  say "Purged data, config and user."
else
  say "Kept $DATA (database, videos) and $ETC (config). Use --purge to delete them."
fi
rm -rf "$PREFIX"
say "Uninstalled. Backups in /var/backups/homelib and /swapfile were left in place."
say "If you used it: sudo tailscale serve reset"
