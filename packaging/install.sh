#!/usr/bin/env bash
# Home Video Library – offline installer for Ubuntu Server 22.04 / 24.04 / 26.04 (x86_64).
# Safe to run again (idempotent): keeps your config, database and videos.
#   sudo ./install.sh
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
need_root "$@"

echo "${c_bld}Home Video Library installer $(cat "$PKG_DIR/VERSION" 2>/dev/null)${c_off}"
[ "$(uname -m)" = "x86_64" ] || die "this package is built for x86_64 only (found $(uname -m))"
verify_manifest

# 1. Service user (no login shell, no home dir contents).
if ! id "$SVC_USER" >/dev/null 2>&1; then
  say "Creating system user '$SVC_USER'"
  useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin --user-group "$SVC_USER"
fi

# 2. Directories + config (never overwrite an existing config).
mkdir -p "$PREFIX" "$ETC" "$DATA"
if [ ! -f "$ETC/config.toml" ]; then
  say "Writing default config to $ETC/config.toml"
  install -m 644 "$PKG_DIR/config.example.toml" "$ETC/config.toml"
else
  say "Keeping existing $ETC/config.toml"
fi
install -m 644 "$PKG_DIR/config.example.toml" "$ETC/config.example.toml"

# 3. App + venv from vendored wheels (deno and imageio-ffmpeg included).
install_app
install_venv
LIB_DIR=$(config_value library dir "$DATA/library")
DATA_DIR=$(config_value library data_dir "$DATA")
mkdir -p "$LIB_DIR" "$DATA_DIR"
chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR" "$LIB_DIR"
chmod 750 "$DATA_DIR"

if command -v ffmpeg >/dev/null; then
  say "System ffmpeg found: $(ffmpeg -version | head -1)"
else
  warn "No system ffmpeg; using the bundled imageio-ffmpeg binary. (Optional later, when online: sudo apt install ffmpeg)"
fi

# 4. Swap: 4 GB RAM is tight while ffmpeg merges 4K files.
if [ -z "$(swapon --noheadings --show 2>/dev/null)" ]; then
  if [ ! -f /swapfile ]; then
    say "No swap found: creating a 2 GB /swapfile"
    if fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none; then
      chmod 600 /swapfile && mkswap /swapfile >/dev/null
    fi
  fi
  if [ -f /swapfile ]; then
    swapon /swapfile 2>/dev/null || warn "could not enable swap now (it will be tried at next boot)"
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
else
  say "Swap already present: $(swapon --noheadings --show | awk '{print $1" "$3}' | head -1)"
fi

# 5. systemd unit.
say "Writing $UNIT"
cat > "$UNIT" <<UNIT
[Unit]
Description=Home Video Library
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_USER
Environment=HOMELIB_CONFIG=$ETC/config.toml
EnvironmentFile=-$ETC/homelib.env
Environment=PATH=$PREFIX/venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=$PREFIX/app
ExecStart=$PREFIX/venv/bin/python -m homelib
Restart=on-failure
RestartSec=5
# Keep the box responsive on 4 GB RAM.
MemoryMax=2G
MemoryHigh=1700M
TasksMax=256
# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
UNIT

if have_systemd; then
  systemctl daemon-reload
  systemctl enable homelib >/dev/null 2>&1
  systemctl restart homelib
  health_check || true
else
  warn "systemd is not running here (container?). Unit written but not started. Use run.sh to start manually."
fi

# 6. Daily backup of the database + config at 04:15.
cat > /etc/cron.d/homelib-backup <<CRON
15 4 * * * root $PREFIX/backup.sh --quiet
CRON

PORT=$(config_value server port 8787)
BIND=$(config_value server bind 127.0.0.1)
cat <<TXT

${c_bld}Installed.${c_off} Run ${c_bld}sudo $PREFIX/selfcheck.sh${c_off} any time for a health report.
Config: $ETC/config.toml   Videos: $LIB_DIR   Logs: journalctl -u homelib -f

${c_bld}Next: reach it from your iPhones with Tailscale (not installed by this script)${c_off}
  1. On a computer, sign up at https://login.tailscale.com and turn ON two-factor
     authentication for that login (Google/Microsoft/GitHub account 2FA, or
     Tailscale's own MFA under Settings). Your tailnet is the only "login" this app has.
  2. Install Tailscale on this server (needs internet):
       curl -fsSL https://tailscale.com/install.sh | sh
       sudo tailscale up
     Open the printed link and approve the machine.
  3. In the admin console (https://login.tailscale.com/admin/machines) find this
     server, click "…" → Disable key expiry (so it doesn't drop off in 180 days).
  4. Enable HTTPS: admin console → DNS → enable MagicDNS and "HTTPS Certificates".
  5. Publish the app privately on your tailnet with HTTPS:
       sudo tailscale serve --bg $PORT
     It prints a URL like https://$(hostname).<your-tailnet>.ts.net
     (Do NOT use "tailscale funnel": that would make it public.)
  6. On each iPhone: install Tailscale from the App Store, log in to the same
     tailnet, keep it connected (VPN On Demand is handy).
  7. In Safari open the https://….ts.net URL, tap Share → "Add to Home Screen".
  8. Pick your profile. Done.
Current bind address: $BIND:$PORT (127.0.0.1 is right for "tailscale serve").
TXT
