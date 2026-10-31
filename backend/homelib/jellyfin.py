"""Files that let Jellyfin (or Kodi, Plex, ...) show the library nicely.

The library folder is plain mp4s named by YouTube id. Next to each video we
write `<id>.nfo` (title, channel, description) and `<id>.jpg` (the thumbnail),
which Jellyfin reads in a "Home videos and photos" library. Shorts live in
`<library>/shorts/` with a `.ignore` file inside, so Jellyfin skips them.

Nothing here is needed by the app itself: every function is best-effort and
logs instead of raising, so a failure never breaks a download or a delete.
"""
from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from . import db

log = logging.getLogger("homelib.jellyfin")

SHORTS = "shorts"
SIDECARS = (".nfo", ".jpg")


def shorts_dir(library_dir: Path) -> Path:
    d = Path(library_dir) / SHORTS
    d.mkdir(parents=True, exist_ok=True)
    # Jellyfin skips a folder holding an EMPTY .ignore (any text in it would be
    # read as file patterns instead, and the Shorts would show up).
    marker = d / ".ignore"
    if not marker.exists() or marker.stat().st_size:
        marker.write_bytes(b"")
    return d


def place(path: str | Path, short: bool, library_dir: Path) -> Path:
    """Move a finished video into shorts/ if it's a Short (and back out if it
    no longer is). Returns the path it ends up at."""
    p = Path(path)
    want = shorts_dir(library_dir) if short else Path(library_dir)
    if p.parent == want or not p.is_file():
        return p
    dest = want / p.name
    try:
        os.replace(p, dest)
    except OSError as e:
        log.warning("could not move %s to %s: %s", p, want, e)
        return p
    for ext in SIDECARS:  # stale ones from the old place
        p.with_suffix(ext).unlink(missing_ok=True)
    return dest


def _nfo(v: dict) -> bytes:
    root = ET.Element("movie")
    for tag, value in (("title", v.get("title")), ("plot", v.get("description")),
                       ("studio", v.get("channel"))):
        if value:
            ET.SubElement(root, tag).text = value
    if v.get("duration"):
        ET.SubElement(root, "runtime").text = str(max(1, round(v["duration"] / 60)))
    if v.get("downloaded_at"):
        ET.SubElement(root, "dateadded").text = datetime.fromtimestamp(
            v["downloaded_at"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ET.SubElement(root, "uniqueid", type="youtube", default="true").text = v["id"]
    ET.SubElement(root, "tag").text = "YouTube"
    ET.indent(root)
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(root, "utf-8") + b"\n"


def _write(dest: Path, data: bytes) -> None:
    tmp = dest.with_name(f".{dest.name}.tmp")  # dot name: Jellyfin never picks up half a file
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def write_sidecars(vid: str, thumbs_dir: Path) -> None:
    """(Re)write <id>.nfo and <id>.jpg next to a ready video file."""
    v = db.q1("SELECT * FROM videos WHERE id=?", (vid,))
    if not v or v["media"] != "video" or v["status"] != "ready" or not v["file_path"]:
        return
    p = Path(v["file_path"])
    if not p.is_file():
        return
    try:
        _write(p.with_suffix(".nfo"), _nfo(v))
        thumb = Path(thumbs_dir) / f"{vid}.jpg"
        if thumb.is_file():
            _write(p.with_suffix(".jpg"), thumb.read_bytes())
        # Jellyfin reads a new .nfo only when the video itself looks changed, so
        # a video it listed before its .nfo existed would keep its id as title.
        os.utime(p)
    except OSError as e:
        log.warning("could not write Jellyfin files for %s: %s", vid, e)


def remove_sidecars(path: str | Path | None) -> None:
    if path:
        for ext in SIDECARS:
            Path(path).with_suffix(ext).unlink(missing_ok=True)


def sync(library_dir: Path, thumbs_dir: Path) -> dict:
    """Bring videos downloaded before 1.6.0 in line: Shorts into shorts/, and
    missing .nfo/.jpg files written. Cheap when there's nothing to do."""
    moved = written = 0
    shorts_dir(library_dir)
    for v in db.q("SELECT id, file_path, is_short FROM videos "
                  "WHERE status='ready' AND media='video' AND file_path IS NOT NULL"):
        old = Path(v["file_path"])
        if not old.is_file():
            continue
        new = place(old, bool(v["is_short"]), library_dir)
        if new != old:
            db.x("UPDATE videos SET file_path=? WHERE id=?", (str(new), v["id"]))
            moved += 1
        if not new.with_suffix(".nfo").is_file() or (
                not new.with_suffix(".jpg").is_file() and (Path(thumbs_dir) / f"{v['id']}.jpg").is_file()):
            write_sidecars(v["id"], thumbs_dir)
            written += 1
    if moved or written:
        log.info("Jellyfin files: moved %d Short(s) into %s/, wrote files for %d video(s)", moved, SHORTS, written)
    return {"moved": moved, "written": written}


def library_mp4s(library_dir: Path) -> list[Path]:
    lib = Path(library_dir)
    return [f for f in (*lib.glob("*.mp4"), *(lib / SHORTS).glob("*.mp4")) if f.is_file()]
