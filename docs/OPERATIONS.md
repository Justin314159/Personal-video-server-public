# Operations

Everything lives in a few places:

| What | Where |
|---|---|
| Program + Python venv | `/opt/homelib` (`app/`, `venv/`, scripts) |
| Config | `/etc/homelib/config.toml` (example: `config.example.toml` next to it) |
| Database, thumbnails | `/var/lib/homelib` |
| Videos | `/var/lib/homelib/library` (or `[library] dir`) |
| Backups | `/var/backups/homelib` (daily at 04:15, newest 14 kept) |
| Service | `homelib.service` (user `homelib`, `MemoryMax=2G`) |

## Everyday commands

```bash
sudo systemctl status homelib          # running?
journalctl -u homelib -f               # live log (downloads, deletions, errors)
journalctl -u homelib | grep deleted   # every auto-delete with its reason
sudo /opt/homelib/selfcheck.sh         # CPU, RAM, disk, ffmpeg, Python, yt-dlp, deno, Tailscale, health
sudo systemctl restart homelib         # after editing /etc/homelib/config.toml
sudo /opt/homelib/backup.sh            # manual backup now
```

## Keeping YouTube downloads working (important)

YouTube changes often and yt-dlp releases fixes every few days. The USB
package contains the yt-dlp of the day it was built. When downloads start
failing ("Sign in to confirm…", "nsig extraction failed", HTTP 403), update it
from the internet:

```bash
sudo /opt/homelib/update.sh --yt-dlp-online
```

That upgrades `yt-dlp`, its JavaScript challenge solver (`yt-dlp-ejs`) and the
bundled `deno` runtime inside the venv, then restarts the service. Consider a
weekly cron job:

```bash
echo '0 5 * * 1 root /opt/homelib/update.sh --yt-dlp-online >/var/log/homelib-ytdlp.log 2>&1' | sudo tee /etc/cron.d/homelib-ytdlp
```

**"Sign in to confirm you're not a bot".** YouTube sometimes asks this of
networks it considers suspicious. It's common from cloud servers and rare from a
home internet connection. If it happens at home, give yt-dlp your own YouTube
login:

1. In a desktop browser where you're signed in to YouTube, export cookies for
   youtube.com in Netscape `cookies.txt` format (yt-dlp's wiki recommends doing it
   from a private window you then close, so the session isn't rotated).
2. Copy it to the server: `sudo install -o homelib -m 600 cookies.txt /etc/homelib/youtube-cookies.txt`
3. In `/etc/homelib/config.toml`, under `[tools]`: `cookies_file = "/etc/homelib/youtube-cookies.txt"`
4. `sudo systemctl restart homelib`

Treat that file like a password: it is your YouTube login.

**Ubuntu 22.04 note:** yt-dlp releases after 2026.10.21 need Python 3.11+, so on
22.04 (Python 3.10) `--yt-dlp-online` is stuck on 2026.10.21 and will
eventually break. Fix: upgrade to 24.04, or `sudo apt install python3.11 python3.11-venv`
(from the deadsnakes PPA), then `sudo rm -rf /opt/homelib/venv` and re-run `install.sh`
(it prefers the newest Python it finds).

## Updating the app from a new USB package

Copy the new `homelib-usb.zip` to your home folder on the server (step 5 of
[SETUP_CHECKLIST.md](SETUP_CHECKLIST.md)), then:

```bash
cd ~ && rm -rf homelib-usb && python3 -m zipfile -e homelib-usb.zip .
cd homelib-usb && sudo bash update.sh
```

Remove the old `homelib-usb` folder first, as above: files left over from an
older package make the integrity check stop with "files not in the manifest".

It verifies the manifest, takes a backup, replaces `/opt/homelib/app`, installs
the new wheels offline and restarts. Your config, database and videos stay. The
previous app is kept in `/opt/homelib/app.old`. Re-running `install.sh` does
the same thing (it is idempotent).

## Changing settings

Edit `/etc/homelib/config.toml`, then `sudo systemctl restart homelib`. Common ones:

* **Library on another disk:** mount it (e.g. `/mnt/videos`), set `[library] dir`,
  then `sudo chown -R homelib: /mnt/videos` and restart.
* **Free-space guard:** `[library] min_free_gb`. New downloads are refused (and
  the app tells you) below this.
* **Concurrency:** `[downloads] max_concurrent` (default 2 = 1 foreground + 1
  Shorts prefetch). Don't raise it on 4 GB.
* **Bind address:** keep `127.0.0.1` with `tailscale serve`. `bind = "tailscale"`
  listens on the Tailscale IP directly (plain HTTP; the PWA's service worker
  then won't register, but everything else works). `0.0.0.0` is refused unless
  `allow_all_interfaces = true`, and then the log prints a loud warning.

Auto-delete hours and default quality can also be changed in the app (Settings).

## Private and shared videos

A download is private to whoever downloaded it ("My queue" or "Just
download"): only they see it in Library, Downloads and search, and it's
auto-deleted once they've watched it. "Share with both of us" (in the download
sheet, preselected on For you → Both of us; or later from a video's menu, the
player, or a playlist) makes it visible to both, puts it in the Shared queue,
and from then on it waits for both of you before auto-delete. New playlists
start private too ("Just for me"); a playlist's "Share with both of us" button
shares it and its videos. Sharing is one-way. This is privacy inside the app,
not security: anyone on the tailnet can still reach the whole server.

Videos already on the server from before 1.4.0 keep their old setting: "My
queue" downloads were already private, "Just download" ones stay shared.

## Captions and transcripts

New downloads fetch captions automatically (`[downloads] subtitles = true`,
languages in `subtitle_langs`, default `["en"]`). YouTube's uploaded subtitles
are preferred, otherwise its automatic captions. They're stored as cleaned
`.vtt` files in `<library dir>/subs/` and deleted together with the video.

* Videos downloaded before 1.3.0 have none: open the video → **Captions** →
  "Get captions from YouTube".
* Your own SRT/VTT file: **Captions** → pick a language → "Subtitle file".
* A caption download failing (YouTube rate limits subtitles sometimes) never
  stops the video download. Retry later with the button above.
* **Transcript → Copy** puts the whole transcript on the clipboard as plain
  paragraphs (a new paragraph at each pause), ready to paste into Notes.

## Tailscale

The install script prints the steps. Summary:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up                     # approve in the browser
sudo tailscale serve --bg 8787        # HTTPS https://<host>.<tailnet>.ts.net → 127.0.0.1:8787
tailscale serve status
```

Enable 2FA on the Tailscale login, enable MagicDNS + HTTPS certificates in the
admin console, and disable key expiry for the server. Never use `tailscale funnel`
for this app (that makes it public).

## Backups and restore

`backup.sh` makes a consistent online copy of the SQLite database plus
`config.toml` and the profile photos (`avatars/`). Videos are not backed up
(they're temporary by design).

Restore:

```bash
sudo systemctl stop homelib
mkdir /tmp/r && sudo tar -xzf /var/backups/homelib/homelib-YYYYMMDD-HHMMSS.tar.gz -C /tmp/r
sudo cp /tmp/r/homelib.db /var/lib/homelib/homelib.db
sudo rm -f /var/lib/homelib/homelib.db-wal /var/lib/homelib/homelib.db-shm
[ -d /tmp/r/avatars ] && sudo cp -r /tmp/r/avatars /var/lib/homelib/
sudo chown -R homelib: /var/lib/homelib/homelib.db /var/lib/homelib/avatars 2>/dev/null
sudo systemctl start homelib
```

## Uninstall

```bash
sudo /opt/homelib/uninstall.sh           # remove program, keep data + config
sudo /opt/homelib/uninstall.sh --purge   # also delete videos, DB, config, user
sudo tailscale serve reset
```

## Running without systemd

From the unzipped package: `./run.sh` (real mode) or `HOMELIB_MOCK=1 ./run.sh`
(fake YouTube). Data goes to `./data`, the config to `./config.toml`.

## Troubleshooting

| Symptom | Check |
|---|---|
| Phone says "Can't reach the server" | Tailscale on? `tailscale status` on the server; `systemctl status homelib`; `tailscale serve status`. |
| Download fails immediately with 507 | Disk below `min_free_gb`. Delete videos or lower the threshold. |
| Downloads fail with extractor errors | `sudo /opt/homelib/update.sh --yt-dlp-online`. Selfcheck should show a JS runtime (deno). |
| Video won't play in Safari | It was downloaded as "Best quality" with VP9/AV1. Tap "Open in VLC", or download again as Compatible. |
| Box feels slow during downloads | Expected on a 2-core CPU during merges; ffmpeg already runs at nice 10 / idle IO. Lower `max_concurrent` to 1. |
| Service restarts with OOM | `journalctl -u homelib | grep -i memory`; check swap is on (`swapon --show`). |
| Nothing in "For you" | It fills after the 03:30 job or the reload button (top right of For you); needs internet. Look for `candidate` in the log. |
| Reload says "No new suggestions" | Everything the searches found was already shown in the last 30 days. Search and watch a few new things, add starter topics in "Your algorithm", or try again tomorrow. |

## Mock mode

`HOMELIB_MOCK=1` replaces YouTube with a fake catalog and fake downloads
(writes small real mp4s; needs ffmpeg). Used by the tests and the preview.
Never set it on the real server unless you want fake videos.

## Development

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                             # tests run in mock mode
HOMELIB_MOCK=1 HOMELIB_DATA_DIR=/tmp/hl python -m homelib   # http://127.0.0.1:8787
python ../scripts/build_preview.py                 # rebuild preview/
../scripts/build_usb.sh                            # rebuild dist/homelib-usb.zip (needs PyPI)
```
