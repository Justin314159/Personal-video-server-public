# Installing Ubuntu Server 24.04 LTS on the HP Slim (wiping Windows)

This erases **everything** on the HP Slim's drive, including Windows 10. Copy
off anything you want to keep first.

You need: the HP Slim, a USB stick of 4 GB or more for the installer (it will
be erased), a keyboard + monitor (HDMI) for the install, ideally an Ethernet
cable, and a Windows laptop to prepare the stick. A second USB stick (or the
same one afterwards) carries `homelib-usb.zip`.

## 1. Make the installer USB (on the Windows laptop)

1. Download **Ubuntu Server 24.04 LTS** (the `ubuntu-24.04.x-live-server-amd64.iso`
   file, about 3 GB) from <https://ubuntu.com/download/server>.
2. Write it to the USB stick with **one** of these:
   * **balenaEtcher** (<https://etcher.balena.io>): *Flash from file* → pick the ISO →
     *Select target* → the USB stick → *Flash!*. Windows may then say the drive needs
     formatting: click **Cancel**, that's normal.
   * **Rufus** (<https://rufus.ie>): Device = your USB stick, Boot selection = the ISO,
     Partition scheme = **GPT**, Target system = **UEFI (non CSM)**, click *Start*,
     choose **Write in ISO Image mode** if asked.
3. Eject the stick.

## 2. Boot the HP Slim from the USB stick

1. Plug the installer USB, keyboard, monitor and Ethernet into the HP Slim.
2. Power it on and immediately tap **F9** repeatedly. That's HP's *Boot Menu* key.
   (**Esc** opens the HP Startup Menu, where F9 = Boot Device Options;
   **F10** = BIOS setup.)
3. Choose the USB stick (listed under *UEFI Boot Sources*, e.g. "UEFI - SanDisk").
4. If it won't boot from USB: press **F10** → *Advanced* → *Secure Boot Configuration*.
   Ubuntu normally boots fine **with** Secure Boot on; if your model refuses, set
   *Legacy Support Disable and Secure Boot Disable* temporarily. Also check
   *Boot Options* → *USB Storage Boot* is enabled. Save with F10.

While you're in BIOS (F10), worth setting:
* *Advanced → Power-On Options → After Power Loss → **Power On*** so the server
  comes back by itself after a power cut.

## 3. Install choices

At the GRUB menu pick **Try or Install Ubuntu Server**. Then:

1. **Language / keyboard**: yours.
2. **Type of install**: *Ubuntu Server* (not "minimized").
3. **Network**: Ethernet should get an address automatically. On Wi-Fi only,
   you can configure it here, or skip and follow [WIFI.md](WIFI.md) afterwards.
4. **Proxy / mirror**: leave defaults.
5. **Storage**: **Use an entire disk** → select the internal drive (≈500 GB).
   Leave *Set up this disk as an LVM group* ticked (fine either way), no
   encryption needed. On the summary, it lists the Windows partitions being
   **deleted**: that's expected → *Done* → **Continue** to confirm.
   * LVM note: Ubuntu's default LVM only allocates ~100 GB to `/`. After install,
     grow it to the whole disk:
     `sudo lvextend -l +100%FREE -r /dev/ubuntu-vg/ubuntu-lv`
6. **Profile**: your name, server name (e.g. `YOUR_SERVER_HOSTNAME`), username (e.g. `YOUR_USERNAME`),
   a strong password.
7. **Ubuntu Pro**: *Skip for now*.
8. **SSH**: tick **Install OpenSSH server**. (Optionally import your GitHub SSH keys.)
9. **Featured server snaps**: **select none** and continue. Docker, Nextcloud etc.
   are not needed and would eat RAM.
10. Wait for *Install complete*, choose **Reboot now**, and remove the USB stick when told.

## 4. First boot

Log in on the console. `hostname -I` shows the server's IP; from the laptop's
PowerShell you can then `ssh YOUR_USERNAME@<that IP>` and paste commands. (`YOUR_SERVER_HOSTNAME` as a
name only works once Tailscale is set up on both.) Then:

```bash
sudo apt update && sudo apt full-upgrade -y      # if it has internet
sudo apt install -y ffmpeg                       # optional: system ffmpeg (a bundled one is used otherwise)
sudo timedatectl set-timezone Europe/London      # your timezone (list: timedatectl list-timezones)
sudo lvextend -l +100%FREE -r /dev/ubuntu-vg/ubuntu-lv   # if you used LVM (see above)
df -h /
```

## 5. Install Home Video Library from the USB stick

1. On the laptop, copy `homelib-usb.zip` onto a USB stick.
2. On the server, plug it in and mount it:

   ```bash
   lsblk -o NAME,SIZE,TYPE             # the stick is the small one, e.g. sda1 (not the ~465G internal drive)
   sudo mkdir -p /mnt/usb && sudo mount /dev/sda1 /mnt/usb   # use your stick's name
   cp /mnt/usb/homelib-usb.zip ~ && sudo umount /mnt/usb
   ```
3. Unzip and install (Ubuntu Server has no `unzip` by default; Python does it):

   ```bash
   cd ~
   rm -rf homelib-usb                  # an older unpacked copy would fail the integrity check
   python3 -m zipfile -e homelib-usb.zip .
   cd homelib-usb
   sudo bash install.sh
   ```

   (`python3 -m zipfile` doesn't keep the executable bit, hence `bash install.sh`.
   The installer checks the SHA-256 manifest before touching anything and needs
   no internet.)
4. Follow the Tailscale steps it prints at the end, then on each iPhone open the
   `https://….ts.net` address in Safari → Share → **Add to Home Screen**.
5. Health check any time: `sudo /opt/homelib/selfcheck.sh`.

Ubuntu Server **26.04 LTS** (Python 3.13/3.14) and **22.04** (Python 3.10) work the same way; the package has wheels for Python 3.10–3.14. See the yt-dlp note in
[OPERATIONS.md](OPERATIONS.md) about 22.04.
