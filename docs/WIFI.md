# Wi-Fi setup (netplan)

**Use Ethernet if you possibly can.** A wired connection is faster, has lower
latency for streaming, and doesn't drop when the router reboots or the 2.4 GHz
band gets crowded. The HP Slim has a gigabit port; a long cable or a pair of
powerline adapters beats Wi-Fi for an always-on server. With Ethernet plugged
in during the Ubuntu install, there's nothing to configure.

If Wi-Fi is the only option:

## 1. Find the Wi-Fi interface name

```bash
ip link
```

Look for a name starting with `wl`, e.g. `wlp2s0` or `wlo1`. If none shows up,
the Wi-Fi driver isn't loaded; plug in Ethernet once and run
`sudo apt update && sudo apt install linux-firmware` (normally already installed).

Ubuntu Server 24.04 includes `wpasupplicant`, which netplan needs for Wi-Fi.
(Check with `dpkg -l wpasupplicant`.) If it's missing and you have no
Ethernet, the Ubuntu install ISO contains it: mount the USB installer and
`sudo apt install /media/cdrom/pool/main/w/wpa/wpasupplicant_*.deb`.

## 2. Write a netplan file

```bash
sudo nano /etc/netplan/60-wifi.yaml
```

```yaml
network:
  version: 2
  renderer: networkd
  wifis:
    wlp2s0:                 # <- your interface name
      dhcp4: true
      optional: true        # don't block boot if Wi-Fi is down
      access-points:
        "YourNetworkName":
          password: "your-wifi-password"
```

Hidden network: add `hidden: true` under the network name. WPA3-only
networks: add `auth: { key-management: sae, password: "…" }` instead of
`password:`.

Lock the file down (it contains the password) and apply:

```bash
sudo chmod 600 /etc/netplan/60-wifi.yaml
sudo netplan try        # auto-reverts after 120 s if you lose connection
sudo netplan apply
ip addr show wlp2s0     # should show an inet address
ping -c 3 1.1.1.1
```

## 3. Keep it stable

* **Turn off Wi-Fi power saving** (common cause of drop-outs and slow streaming):

  ```bash
  sudo tee /etc/systemd/system/wifi-powersave-off.service >/dev/null <<'EOF'
  [Unit]
  Description=Disable Wi-Fi power saving
  After=network-online.target
  [Service]
  Type=oneshot
  ExecStart=/usr/sbin/iw dev wlp2s0 set power_save off
  [Install]
  WantedBy=multi-user.target
  EOF
  sudo apt install -y iw   # if missing
  sudo systemctl enable --now wifi-powersave-off
  ```

* **Reserve the IP** for the server in your router's DHCP settings (optional;
  Tailscale doesn't need it, but it helps for SSH on the LAN).
* Prefer 5 GHz if the box is near the router.
* Tailscale works fine over Wi-Fi; nothing extra is needed there.

## Troubleshooting

```bash
networkctl status wlp2s0
journalctl -u systemd-networkd -u wpa_supplicant@wlp2s0 -n 50
sudo netplan --debug apply
```
