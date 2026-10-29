# Setup checklist

From a Windows micro PC to the app on both iPhones. Work top to bottom and tick
each box. Every command is meant to be typed (or pasted) exactly as shown,
except words in `<angle brackets>`, which you replace with your own value.

**Already installed an older version?** Skip to [Updating an existing install](#updating-an-existing-install).

**Time:** about 1½ hours, most of it waiting for downloads and the Ubuntu install.

**You need:**
- A PC: I used an HP micro Pc(it will be wiped)
- a USB stick, 4 GB or bigger (it will be erased)
- a USB keyboard and an HDMI monitor/TV for the micro PC (only during setup)
- an Ethernet cable from the micro PC to your router (strongly recommended; Wi-Fi: see step 3)
- your Windows laptop, on the same home network
- both iPhones

---

## 1. Before you start

- [ ] Copy anything you want to keep off the micro PC. **Windows and everything on it will be erased.**
- [ ] Have a piece of paper ready. You'll write down: the server's username, password, IP address, and its `https://….ts.net` address.

## 2. Make the Ubuntu installer stick (on the laptop)

- [ ] Go to <https://ubuntu.com/download/server> and click the green **Download Ubuntu Server … LTS** button (24.04 or 26.04, both work). You get one file named like `ubuntu-26.04-live-server-amd64.iso` (about 3 GB). Wait for it to finish.
- [ ] Go to <https://rufus.ie>, scroll to **Download**, click the top link (e.g. `rufus-4.x.exe`). It runs without installing.
- [ ] Plug the USB stick into the laptop.
- [ ] Double-click the Rufus file. If Windows asks "Do you want to allow this app to make changes", click **Yes**. If Rufus asks about checking for updates, click **No**.
- [ ] In Rufus:
  - **Device**: your USB stick (check the size matches the stick).
  - **Boot selection**: click **SELECT**, pick the Ubuntu `.iso` file from Downloads.
  - **Partition scheme**: **GPT**. **Target system**: **UEFI (non CSM)**.
  - Leave everything else as it is.
- [ ] Click **START**.
  - "ISOHybrid image detected": choose **Write in ISO Image mode (Recommended)** → **OK**.
  - "Download required" (extra boot files): click **Yes**.
  - "All data on the device will be destroyed": click **OK**.
- [ ] Wait until the green bar says **READY**, then click **CLOSE**.
- [ ] Eject the stick (taskbar USB icon → **Eject**) and unplug it.

## 3. Install Ubuntu on the micro PC

- [ ] Plug the USB stick, keyboard, monitor and Ethernet cable into the micro PC. Plug in power.
- [ ] Press the power button and immediately tap **F9** about once a second until a **Boot Menu** appears.
- [ ] Use the arrow keys to pick the USB stick (under *UEFI Boot Sources*, named after the stick's brand, e.g. "UEFI – SanDisk") and press **Enter**.
  - **If no Boot Menu or the USB isn't listed:** restart, tap **F10** instead (BIOS setup) → *Advanced* → *Boot Options* → make sure **USB Storage Boot** is ticked → **F10** to save and exit, then try F9 again.
  - **If it starts booting and then refuses ("Secure Boot violation"):** F10 → *Advanced* → *Secure Boot Configuration* → *Legacy Support Disable and Secure Boot Disable* → F10 to save. Try again.
- [ ] Optional but recommended, while in BIOS (F10): *Advanced* → *Power-On Options* → *After Power Loss* → **Power On**. The server then restarts by itself after a power cut.
- [ ] A black menu appears: pick **Try or Install Ubuntu Server** (it's already selected; press **Enter** or wait). It takes a minute or two to load.

The installer is navigated with the keyboard: **arrow keys / Tab** to move, **Space** to tick a box, **Enter** to press the highlighted button. Each screen ends with a **[ Done ]** button at the bottom.

- [ ] **Language**: English (or yours) → Enter.
- [ ] *Installer update available*: **Continue without updating**.
- [ ] **Keyboard configuration**: pick your layout → **Done**.
- [ ] **Choose the type of installation**: keep **(X) Ubuntu Server** (not "minimized"). Leave "Search for third-party drivers" **unticked**. → **Done**.
- [ ] **Network configuration**: the Ethernet line (e.g. `eno1`) should show **DHCPv4** and an address like `192.168.x.x/24`. → **Done**.
  - No address and you only have Wi-Fi: pick the Wi-Fi line → *Edit Wi-Fi* → enter your network and password, or skip and follow [WIFI.md](WIFI.md) after the install.
- [ ] **Proxy configuration**: leave empty → **Done**.
- [ ] **Ubuntu archive mirror**: wait until it says the mirror passed the test → **Done**.
- [ ] **Guided storage configuration**: keep **(X) Use an entire disk**, make sure the internal drive (≈ 500 GB) is selected, keep **[X] Set up this disk as an LVM group**, leave encryption off → **Done**.
- [ ] **Storage configuration** (summary): **Done** → a red box *Confirm destructive action* appears → **Continue**. (This is the moment Windows is erased.)
- [ ] **Profile configuration**:
  - Your name: anything.
  - Your server's name: pick one, e.g. `homelib` (written `YOUR_SERVER_HOSTNAME` below)
  - Pick a username: e.g. `YOUR_USERNAME` (lowercase, no spaces)
  - Password twice: a strong one.
  - **Write the username and password on your paper.** → **Done**.
- [ ] **Upgrade to Ubuntu Pro**: **Skip for now** → **Continue**.
- [ ] **SSH configuration**: press Space on **[X] Install OpenSSH server**. Leave "Allow password authentication over SSH" ticked. → **Done**.
- [ ] **Featured server snaps**: tick **nothing** → **Done**.
- [ ] Wait (10–20 minutes). When the top says **Install complete!**, choose **Reboot Now**.
- [ ] When it says *Please remove the installation medium, then press ENTER*: unplug the USB stick, press **Enter**.

## 4. First boot

- [ ] Wait for `YOUR_SERVER_HOSTNAME login:`. **If text keeps printing after it** (lines starting with `[` or `cloud-init`), wait until it stops and press **Enter** once to get a clean login prompt.
- [ ] Type your username, press Enter, then your password and Enter. **Nothing appears while you type the password.** That's normal.
- [ ] Find the server's address on your network and write it down:
  ```bash
  hostname -I
  ```
  The first number (e.g. `YOUR_SERVER_LAN_IP`, something like 192.168.x.x) is your **server IP**.

**Recommended: continue from the laptop** so you can copy and paste the
commands below instead of typing them.

- [ ] On the laptop, open **PowerShell** (Start menu → type `PowerShell` → Enter) and connect:
  ```powershell
  ssh <username>@<server IP>
  ```
  e.g. `ssh YOUR_USERNAME@YOUR_SERVER_LAN_IP`. The first time it asks *Are you sure you want to continue connecting (yes/no/[fingerprint])?*: type `yes`, Enter. Then the password (invisible again).
- [ ] You're in when the prompt looks like `YOUR_USERNAME@YOUR_SERVER_HOSTNAME:~$`. In PowerShell, **right-click pastes**.

Now, on the server (console or SSH):

- [ ] Update Ubuntu (5–10 minutes):
  ```bash
  sudo apt update && sudo apt full-upgrade -y
  ```
  It asks for your password once (the `sudo` password = your login password). If a purple box asks *Which services should be restarted?*, press **Enter** (keep the defaults).
- [ ] Restart so the updates take effect, wait a minute, then log in (or `ssh` in) again:
  ```bash
  sudo reboot
  ```
- [ ] Set your timezone (the nightly suggestions job runs at 03:30 local time). Find yours, then set it:
  ```bash
  timedatectl list-timezones | grep America
  sudo timedatectl set-timezone America/New_York
  ```
  Replace `America` and `America/New_York` with your region and city (e.g. `Europe/London`). Check with `date`.
- [ ] Let Ubuntu use the whole disk (the installer only gives it about 100 GB):
  ```bash
  sudo lvextend -l +100%FREE -r /dev/ubuntu-vg/ubuntu-lv
  df -h /
  ```
  In the `df` output, **Size** should now be roughly the full drive (e.g. `456G`). If `lvextend` says `Volume group "ubuntu-vg" not found`, you unticked LVM in step 3: skip this, the disk is already fully used.
- [ ] Install the system ffmpeg (optional, a bundled one works too):
  ```bash
  sudo apt install -y ffmpeg
  ```

## 5. Get the app onto the server

First download the package (on the laptop):

- [ ] Open <https://github.com/Justin314159/Personal-video-server-public/releases/latest/download/homelib-usb.zip>. It saves `homelib-usb.zip` (about **100 MB**) to **Downloads**. (It's the newest release from the repository's **Releases** page. You can also build it yourself on Linux/macOS with `bash scripts/build_usb.sh`.) **Don't unzip it.**

Then copy it to the server with **one** of these options:

**Option A: over the network (easiest, needs step 4's SSH working)**

- [ ] In a **new** PowerShell window on the laptop (not the one logged into the server):
  ```powershell
  scp $HOME\Downloads\homelib-usb.zip <username>@<server IP>:~
  ```
  Enter the password. It shows `homelib-usb.zip 100%` when done. Continue with step 6.
  - **Then switch back to the window logged into the server** (prompt `YOUR_USERNAME@YOUR_SERVER_HOSTNAME:~$`). Commands from here on don't work in a laptop window (prompt `PS C:\…`): PowerShell answers with errors like *A parameter cannot be found* or *'&&' is not a valid statement separator*.

**Option B: with the USB stick**

- [ ] Plug the stick into the laptop. Open **File Explorer** → **This PC**, right-click the stick → **Format…** → *File system*: **exFAT** → **Start** → **OK**.
  - If **Format** is missing or fails, or the stick shows a tiny size: open **Rufus**, *Device* = the stick, *Boot selection* = **Non bootable**, *File system* = **exFAT**, **START**.
- [ ] Copy `homelib-usb.zip` from Downloads onto the stick. Eject it and plug it into the micro PC.
- [ ] On the server, find the stick's name:
  ```bash
  lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS
  ```
  Look for a `part` line whose **SIZE** matches the stick (e.g. `sda1  14.9G  part` for a 16 GB stick). The internal drive is the big one (≈465G, usually `nvme0n1`): **not that one**. In the commands below, replace `sda1` if yours differs.
- [ ] Copy the zip off the stick:
  ```bash
  sudo mkdir -p /mnt/usb && sudo mount /dev/sda1 /mnt/usb
  cp /mnt/usb/homelib-usb.zip ~ && sudo umount /mnt/usb
  ```
  - `unknown filesystem type 'exfat'`: run `sudo apt install -y exfatprogs` and repeat.
  - `wrong fs type` or `can't read superblock`: you used the disk name (`sda`) instead of the partition (`sda1`).

## 6. Install the app

Everything in this step runs **on the server**: in the SSH window whose prompt
is `<username>@YOUR_SERVER_HOSTNAME:~$` (or on the server's own keyboard), never in a
PowerShell prompt starting with `PS C:\`.

- [ ] Check the zip is there:
  ```bash
  ls -lh ~/homelib-usb.zip
  ```
  It should show about `99M`.
- [ ] Unzip and install:
  ```bash
  cd ~ && rm -rf homelib-usb && python3 -m zipfile -e homelib-usb.zip .
  cd homelib-usb && sudo bash install.sh
  ```
- [ ] Watch for these lines (it takes 1–3 minutes):
  - `All 129 files verified.` (the number may differ). If it says **checksum mismatch** instead, download the zip again and repeat step 5.
  - `Server is up at http://127.0.0.1:8787`
  - **Installed.** followed by Tailscale steps (they're the same as step 7 below).
  - `ERROR: no supported Python found`: your zip is older than 1.3.0's Python 3.13/3.14 support (Ubuntu 26.04). Download it again (step 5) and repeat this step.
- [ ] Health check:
  ```bash
  sudo /opt/homelib/selfcheck.sh
  ```
  Every line should start with **OK** except **Tailscale** (WARN "not installed"; that's next). If anything says **FAIL**, run `journalctl -u homelib -n 50` and see Troubleshooting in [OPERATIONS.md](OPERATIONS.md).

## 7. Tailscale (lets the iPhones reach the server from anywhere)

On the laptop:

- [ ] Go to <https://login.tailscale.com> and sign up with a Google, Microsoft, GitHub or Apple account. **Use one account you'll also use on both iPhones.**
- [ ] Make sure **two-factor authentication** is on for that Google/Microsoft/GitHub/Apple account. It is the only thing protecting the app; the app has no passwords of its own.
- [ ] If Tailscale shows "Add your first device" screens, you can close them.

On the server:

- [ ] Install Tailscale and connect:
  ```bash
  curl -fsSL https://tailscale.com/install.sh | sh
  sudo tailscale up
  ```
- [ ] It prints a link like `https://login.tailscale.com/a/1a2b3c4d5e`. Open it on the laptop (type it in if you're on the console), sign in, click **Connect**. The server prompt returns with `Success.`

Back on the laptop, in the admin console (<https://login.tailscale.com/admin/machines>):

- [ ] Find **YOUR_SERVER_HOSTNAME** in the list → click the **…** on its right → **Disable key expiry**. (Otherwise it disconnects after 180 days.)
- [ ] Click **DNS** at the top. Make sure **MagicDNS** is enabled (button says *Disable MagicDNS* when it's on). Scroll down to **HTTPS Certificates** → **Enable HTTPS** → confirm.

On the server:

- [ ] Publish the app privately on your tailnet with HTTPS:
  ```bash
  sudo tailscale serve --bg 8787
  ```
  - If it prints *Serve is not enabled on your tailnet* with a link: open the link on the laptop, click **Enable**, then run the command again.
  - Success looks like: `Available within your tailnet: https://YOUR_SERVER_HOSTNAME.YOUR_TAILNET.ts.net/`
- [ ] **Write that `https://….ts.net` address on your paper.** You can show it again any time with `tailscale serve status`.
- [ ] Never run `tailscale funnel` for this app. Funnel makes it public on the internet.

## 8. Each iPhone (do this on both)

- [ ] App Store → install **Tailscale** → open it → **Log in** with the **same** account as in step 7 → when iOS asks to *Add VPN Configurations*, tap **Allow** (and confirm with Face ID / passcode).
- [ ] In the Tailscale app, make sure the switch at the top is **on** (Connected). You should see `YOUR_SERVER_HOSTNAME` in the device list.
- [ ] Recommended: Tailscale app → **Settings** → turn on **VPN On Demand**, so it reconnects by itself.
- [ ] Open **Safari** (not Chrome) and type the `https://….ts.net` address from your paper. The very first load can take ~10 seconds while the HTTPS certificate is created.
- [ ] Tap the **Share** button (square with an arrow) → scroll down → **Add to Home Screen** → keep **Open as Web App** on if shown → **Add**.
- [ ] Open **Videos** from the Home Screen and tap your profile (**Profile 1** or **Profile 2** unless you renamed them in `config.toml`). To rename later: **Settings** → tap the name at the top → type → done.

## 9. First run

- [ ] **Search** tab → type something → tap a result → choose **Compatible 1080p** and **My queue** → **Download**.
- [ ] **Downloads** tab: watch the progress bar (usually a minute or two).
  - If it fails with *Sign in to confirm you're not a bot*: follow the cookies steps in [OPERATIONS.md](OPERATIONS.md) (rare on a home connection).
- [ ] **Library** tab → **My queue** → tap the video to play it.
- [ ] Try the player: double-tap the right side to skip 10 s, drag the bar, tap **1×** for speed, tap **CC** for captions.
- [ ] Music: **Search** → switch to **Music** → find a song → tap the download arrow. Play it in the app, or tap the phone icon next to it to save the mp3 to the Files app.
- [ ] Background playback check: follow "How to test on your iPhone" in [IOS_PLAYBACK.md](IOS_PLAYBACK.md).
- [ ] Optional: **Settings → Share-sheet shortcut**, and build the shortcut on each phone to send videos straight from the YouTube app.
- [ ] Unplug the monitor and keyboard. The server runs on its own from now on. To log in later from the laptop: `ssh <username>@<server IP>` (or `ssh <username>@YOUR_SERVER_HOSTNAME` if Tailscale is also installed on the laptop).

## Keep YouTube downloads working

YouTube changes often. Pick **one**:

- [ ] **Automatic (recommended):** update yt-dlp every Monday at 05:00. Run once on the server:
  ```bash
  echo '0 5 * * 1 root /opt/homelib/update.sh --yt-dlp-online >/var/log/homelib-ytdlp.log 2>&1' | sudo tee /etc/cron.d/homelib-ytdlp
  ```
- [ ] **By hand, every week or two:**
  ```bash
  sudo /opt/homelib/update.sh --yt-dlp-online
  ```

If downloads start failing, run the by-hand command first, then look at `journalctl -u homelib -n 50`.

---

## Updating an existing install

For a server that already runs an older version (your videos, profiles,
settings and history are kept).

- [ ] Download the new `homelib-usb.zip` and copy it to the server exactly as in [step 5](#5-get-the-app-onto-the-server) (Option A or B).
- [ ] On the server, **remove the old unpacked folder first** (otherwise leftover files make the integrity check fail), then unpack and update:
  ```bash
  cd ~ && rm -rf homelib-usb && python3 -m zipfile -e homelib-usb.zip .
  cd homelib-usb && sudo bash update.sh
  ```
- [ ] It should print `All … files verified.`, `Server is up at http://127.0.0.1:8787` and `Updated to 1.3.0` (or newer).
- [ ] Health check: `sudo /opt/homelib/selfcheck.sh`
- [ ] On each iPhone, fully close the Videos app (swipe it away in the app switcher) and open it again to load the new version. If it still looks old, open the address once in Safari and pull down to reload.
- [ ] New in 1.4.0: what you download is now **just yours** (the other profile doesn't see it) unless you pick **Share with both of us**. For you has a **reload** button (top right), transcripts have a **Copy** button, and you can add a **profile photo** in Settings (tap your picture).
- [ ] New in 1.3.0: the **Queue** tab is now **Library** (Videos / My queue / Shared / Playlists). Videos downloaded before the update have no captions yet: open one → **Captions** → **Get captions from YouTube**.
