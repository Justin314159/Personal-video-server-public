# Updates

One page per release since 1.3.0: what changed, anything to do after updating,
and which parts of the main documentation (`README.md`, `docs/`) it changes.
The main docs describe the app as of 1.3.0; where an update page says
something works differently, the update page wins.

| Version | Highlights |
|---|---|
| [1.4.0](1.4.0.md) | Private downloads + "Share with both of us", For you reload button, copy transcript, profile photos |

## How to update

Your videos, profiles, settings and history are kept.

1. Download the new
   [`homelib-usb.zip`](https://github.com/Justin314159/Personal-video-server-public/releases/latest/download/homelib-usb.zip)
   from the latest Release and copy it to your home folder on the
   server, the same way as in
   [step 5 of the setup checklist](../docs/SETUP_CHECKLIST.md#5-get-the-app-onto-the-server)
   (Option A or B).
2. On the server, **remove the old unpacked folder first** (leftover files make
   the integrity check stop with "files not in the manifest"), then unpack and
   update:
   ```bash
   cd ~ && rm -rf homelib-usb && python3 -m zipfile -e homelib-usb.zip .
   cd homelib-usb && sudo bash update.sh
   ```
3. It should print `All … files verified.`, `Server is up at http://127.0.0.1:8787`
   and `Updated to <version>`.
4. Health check: `sudo /opt/homelib/selfcheck.sh`
5. On each iPhone, fully close the Videos app (swipe it away in the app
   switcher) and open it again to load the new version. If it still looks old,
   open the address once in Safari and pull down to reload.
6. Read the page for the new version (and any versions you skipped) for
   what's new and anything to do afterwards.

`update.sh` verifies the package, takes a backup, replaces `/opt/homelib/app`,
installs the bundled packages offline and restarts. The database is upgraded
automatically on start. The previous app is kept in `/opt/homelib/app.old`.

### Going back

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
(watch progress, new downloads stay on disk but are forgotten).
