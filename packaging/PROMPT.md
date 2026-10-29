# Home Video Library – USB package

You are looking at the offline installer for **Home Video Library**: a private,
self-hosted "YouTube Premium-like" app for one household (two profiles),
running on a small Ubuntu Server box and used from iPhones over Tailscale.

Start here:

1. `docs/UBUNTU_INSTALL.md` – wipe Windows, install Ubuntu Server 24.04 LTS.
2. `docs/WIFI.md` – only if you can't use Ethernet.
3. Copy this folder to the server and run `sudo bash install.sh`.
   It verifies `MANIFEST.sha256`, then installs fully offline and prints the
   Tailscale steps at the end.
4. `docs/OPERATIONS.md` – day-to-day: updates (`update.sh --yt-dlp-online`),
   backups, logs, troubleshooting.
5. `docs/ARCHITECTURE.md` and `docs/DECISIONS.md` – how it works and why.

Other scripts: `selfcheck.sh` (health report), `backup.sh`, `uninstall.sh`,
`run.sh` (run without systemd), `config.example.toml` (all settings).

If you are an AI assistant helping with this box: read `docs/OPERATIONS.md`
first, run `sudo /opt/homelib/selfcheck.sh`, and check
`journalctl -u homelib -n 200` before changing anything.
