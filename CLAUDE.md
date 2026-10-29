# Notes for Claude

Home Video Library: FastAPI backend + plain-JS frontend, shipped to a home
Ubuntu server as an offline zip. This public repo must never contain
personal details: no real names, hostnames, tailnet names, IPs, emails,
cookies or keys. Use placeholders (`Profile 1`/`Profile 2`, `Alex`/`Sam`/
`Partner`, `YOUR_SERVER_HOSTNAME.YOUR_TAILNET.ts.net`, `100.64.0.1`).

## Every release (any change that ships to the server)

1. **Version.** Bump `__version__` in `backend/homelib/__init__.py`.
2. **Don't rewrite the main docs.** `README.md` and `docs/*` stay as they
   are; only add a link to something new if it's otherwise unfindable.
   Code comments, tests and `scripts/`/`packaging/` are fine to change.
3. **Write `updates/<version>.md`** and add its row to the table in
   `updates/README.md` (the build refuses to run without both). Same sections
   as `updates/1.4.0.md`:
   - *What's new*, in plain language (not a changelog of files);
   - *After updating*: anything to do, and old behaviour that stays;
   - which package works (small, or full needed and why);
   - *What this changes in the main docs*: table of section → was → now
     for every main-doc statement that's no longer true;
   - *Design notes*: API/data/architecture details.
4. **Both packages.** `scripts/build_usb.sh` builds `dist/homelib-usb.zip`
   (full) and `dist/homelib-update.zip` (small) from the pinned versions in
   `packaging/pins/`. Zips are not committed here: pushing a `v<version>` tag
   (or running "Build release" in Actions) builds both and attaches them to
   the GitHub Release.
   - Keep the pins unless the release needs newer Python packages. Then
     `UPDATE_PINS=1 bash scripts/build_usb.sh`, commit `packaging/pins/`, and
     mark the release "Full package needed" in its page and the table.
5. **Test.** `cd backend && pytest -q`; for UI changes also
   `python scripts/build_preview.py` and check the feature in a browser
   (mock server: `HOMELIB_MOCK=1 HOMELIB_DATA_DIR=/tmp/hl python -m homelib`).
   Then the update itself, on a throwaway machine or container as root:
   `sudo bash scripts/test_update_e2e.sh <previous Release's homelib-usb.zip>`.
   It must print PASS.
6. **Scrub check** before committing. Both must print nothing (or only
   things you've checked are public):
   ```bash
   git grep -nIiE "[a-z0-9._-]+@[a-z0-9-]+\.[a-z]{2,}" -- ':!*.lock'
   git grep -nIE "\b[0-9]{1,3}(\.[0-9]{1,3}){3}\b" -- ':!packaging/pins' | grep -vE "127\.0\.0\.1|0\.0\.0\.0|100\.64\.0\.[01]\b"
   ```

## Handy

- Tests run in mock mode (no internet): `backend/tests/conftest.py`.
- `update.sh` must keep working when run from an unpacked zip: unzipping with
  `python3 -m zipfile` drops the executable bit, so call scripts with `bash`.
- Don't use `pkill -f` with a pattern that also appears in your own shell
  command; it kills the shell.
