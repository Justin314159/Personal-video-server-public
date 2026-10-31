# How to update

Everything you need to update a server that already runs the app, from
downloading the package to checking it worked. Your videos, profiles,
settings and history are kept.

What changed in each version is on its own page in this folder (see the
[list of versions](README.md)).

## 1. Pick the package

Each [Release](https://github.com/Justin314159/Personal-video-server-public/releases) comes in two sizes. Both install the same app, with
the same steps (the links below are always the latest release):

| Package | Size | Use it |
|---|---|---|
| [`homelib-update.zip`](https://github.com/Justin314159/Personal-video-server-public/releases/latest/download/homelib-update.zip) | well under 1 MB | **Usually.** Just the app, scripts and docs. It reuses the Python packages (yt-dlp, deno, ffmpeg, …) already on your server. |
| [`homelib-usb.zip`](https://github.com/Justin314159/Personal-video-server-public/releases/latest/download/homelib-usb.zip) | ~100 MB | New installs, or when the small one says it can't be used (the release changed those Python packages). Has everything. |

The [list of versions](README.md) says whether the small package works for
each one. If in doubt, try the small one: before changing anything, it checks
that your server has every package version it needs. If not, it stops, says
which ones are missing, and changes nothing. Then use the full package.

In the commands below, use `homelib-update` for the small package and
`homelib-usb` for the full one.

## 2. Download it on your laptop or computer

Click the package's link above. It
saves `homelib-update.zip` or `homelib-usb.zip` to your **Downloads**
folder. **Don't unzip it.**

## 3. Copy it to the server

You need your **username** on the server and the server's **address**. To
find the address, run `hostname -I` on the server and use the first number
(something like `192.168.x.x`). If your computer is on Tailscale, the server's
Tailscale name works too.

**Option A: over the network with `scp` (easiest)**

Open a terminal on your computer. Don't use a window that's already logged
into the server: `scp` runs on your computer.

- **Windows**: open **PowerShell** (Start menu → type `PowerShell` → Enter):
  ```powershell
  scp $HOME\Downloads\homelib-update.zip <username>@<server address>:~
  ```
- **Mac or Linux**: open **Terminal**:
  ```bash
  scp ~/Downloads/homelib-update.zip <username>@<server address>:~
  ```

For example `scp $HOME\Downloads\homelib-update.zip YOUR_USERNAME@YOUR_SERVER_LAN_IP:~`.
For the full package, write `homelib-usb.zip` instead. The `:~` at the end
means "my home folder on the server".

The first time, it asks *Are you sure you want to continue connecting
(yes/no/[fingerprint])?*: type `yes` and press Enter. Then enter your server
password (nothing appears while you type). It shows `homelib-update.zip 100%`
when done.

**Option B: with a USB stick**

1. Copy the zip from Downloads onto a USB stick formatted as **exFAT** (on
   Windows: File Explorer → This PC → right-click the stick → Format… →
   File system **exFAT**). Eject it and plug it into the server.
2. On the server, find the stick:
   ```bash
   lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS
   ```
   Look for a `part` line whose **SIZE** matches the stick (e.g.
   `sda1  14.9G  part`). The big one (≈465G, usually `nvme0n1`) is the
   server's own drive: **not that one**. Replace `sda1` below if yours differs.
3. Copy the zip off the stick:
   ```bash
   sudo mkdir -p /mnt/usb && sudo mount /dev/sda1 /mnt/usb
   cp /mnt/usb/homelib-update.zip ~ && sudo umount /mnt/usb
   ```
   - `unknown filesystem type 'exfat'`: run `sudo apt install -y exfatprogs`
     and repeat.
   - `wrong fs type` or `can't read superblock`: you used the disk name
     (`sda`) instead of the partition (`sda1`).

## 4. Run the update on the server

1. Log into the server: at the server itself, or from your computer with
   ```bash
   ssh <username>@<server address>
   ```
   You're in when the prompt shows the server's name (e.g.
   `YOUR_USERNAME@YOUR_SERVER_HOSTNAME:~$`).
2. Check the zip arrived: `ls -lh ~/homelib-update.zip`
3. **Remove the old unpacked folder first** (leftover files make the
   integrity check stop with "files not in the manifest"), then unpack and
   update. Small package:
   ```bash
   cd ~ && rm -rf homelib-update && python3 -m zipfile -e homelib-update.zip .
   cd homelib-update && sudo bash update.sh
   ```
   Full package:
   ```bash
   cd ~ && rm -rf homelib-usb && python3 -m zipfile -e homelib-usb.zip .
   cd homelib-usb && sudo bash update.sh
   ```
4. It should print `All … files verified.`, `Server is up at
   http://127.0.0.1:8787` and `Updated to <version>`. The small package also
   prints `All needed packages are already installed.`

## 5. Check it and finish

1. Health check: `sudo /opt/homelib/selfcheck.sh`
2. On each iPhone, fully close the Videos app (swipe it away in the app
   switcher) and open it again to load the new version. If it still looks
   old, open the address once in Safari and pull down to reload.
3. Read the page for the new version (and any versions you skipped) in the
   [list of versions](README.md): what's new, and anything to do afterwards.
4. Optional: delete the zip and the unpacked folder from your home folder on
   the server (`rm -rf ~/homelib-update ~/homelib-update.zip`). They aren't
   needed after the update.

`update.sh` verifies the package, takes a backup, replaces `/opt/homelib/app`,
installs the bundled packages offline (full package only) and restarts. Your
data (`/var/lib/homelib`) and settings (`/etc/homelib`) are never replaced.
The database is upgraded automatically on start. The previous app is kept in
`/opt/homelib/app.old`.

## Going back

If an update misbehaves, restore the previous app and the backup taken just
before the update:

```bash
sudo systemctl stop homelib
sudo rm -rf /opt/homelib/app && sudo mv /opt/homelib/app.old /opt/homelib/app
ls -lt /var/backups/homelib | head        # pick the one from just before you updated
mkdir -p /tmp/r && sudo tar -xzf /var/backups/homelib/<that file> -C /tmp/r
sudo cp /tmp/r/homelib.db /var/lib/homelib/homelib.db
sudo rm -f /var/lib/homelib/homelib.db-wal /var/lib/homelib/homelib.db-shm
sudo chown homelib: /var/lib/homelib/homelib.db
sudo systemctl start homelib
```

Restoring the database undoes anything done in the app since the update
(watch progress, new downloads stay on disk but are forgotten). Check the
page of the version you're leaving for anything extra (for 1.6.0: move the
Shorts back first).
