"""Files for Jellyfin: <id>.nfo + <id>.jpg next to each video, Shorts in shorts/."""
import xml.etree.ElementTree as ET
from pathlib import Path

from homelib import db, jellyfin, library, mock

from .conftest import wait_for

P1 = {"X-Profile": "1"}


def _download(client, item):
    client.post("/api/downloads", json={**item, "quality": "compat", "queue": "personal"}, headers=P1)
    wait_for(lambda: client.get(f"/api/videos/{item['id']}").json()["status"] == "ready")
    return Path(db.q1("SELECT file_path FROM videos WHERE id=?", (item["id"],))["file_path"])


def test_download_writes_nfo_and_files_shorts_away(client, cfg):
    video = next(v for v in mock.CATALOG if not v["is_short"])
    short = next(v for v in mock.CATALOG if v["is_short"])
    vp, sp = _download(client, video), _download(client, short)

    assert vp == cfg.library_dir / f"{video['id']}.mp4"
    assert sp == cfg.library_dir / "shorts" / f"{short['id']}.mp4"
    assert (cfg.library_dir / "shorts" / ".ignore").read_bytes() == b""  # empty = skip the whole folder
    nfo = ET.parse(vp.with_suffix(".nfo")).getroot()
    assert nfo.tag == "movie"
    assert nfo.findtext("title") == video["title"] and nfo.findtext("studio") == video["channel"]
    assert nfo.find("uniqueid").text == video["id"]
    # the Short still streams from its new place, and counts towards library size
    st = client.get(f"/api/videos/{short['id']}").json()
    assert client.get(st["stream"], headers={"Range": "bytes=0-1"}).status_code == 206
    assert client.get("/api/status").json()["disk"]["library_bytes"] == vp.stat().st_size + sp.stat().st_size

    client.delete(f"/api/videos/{video['id']}", headers=P1)
    assert not vp.exists() and not vp.with_suffix(".nfo").exists()


def test_sync_moves_old_shorts_and_backfills(fresh_db, cfg):
    lib, thumbs = cfg.library_dir, cfg.thumbs_dir
    lib.mkdir(parents=True)
    thumbs.mkdir(parents=True)
    for vid, short in (("aaaaaaaaaaa", False), ("bbbbbbbbbbb", True)):
        (lib / f"{vid}.mp4").write_bytes(b"x")
        (thumbs / f"{vid}.jpg").write_bytes(b"jpg-" + vid.encode())
        library.upsert_video({"id": vid, "title": "Fish & <chips>", "channel": "C", "is_short": short,
                              "duration": 600})
        db.x("UPDATE videos SET status='ready', file_path=? WHERE id=?", (str(lib / f"{vid}.mp4"), vid))

    assert jellyfin.sync(lib, thumbs) == {"moved": 1, "written": 2}
    assert db.q1("SELECT file_path FROM videos WHERE id='bbbbbbbbbbb'")["file_path"] == \
        str(lib / "shorts" / "bbbbbbbbbbb.mp4")
    assert ET.parse(lib / "aaaaaaaaaaa.nfo").getroot().findtext("title") == "Fish & <chips>"
    assert (lib / "aaaaaaaaaaa.jpg").read_bytes() == b"jpg-aaaaaaaaaaa"
    assert ET.parse(lib / "aaaaaaaaaaa.nfo").getroot().findtext("runtime") == "10"
    # second run: nothing to do
    assert jellyfin.sync(lib, thumbs) == {"moved": 0, "written": 0}


def test_music_is_left_alone(fresh_db, cfg):
    music = cfg.library_dir / "music"
    music.mkdir(parents=True)
    (music / "song.mp3").write_bytes(b"x")
    library.upsert_video({"id": "au_ccccccccccc", "title": "Song", "media": "audio"})
    db.x("UPDATE videos SET status='ready', file_path=? WHERE id='au_ccccccccccc'", (str(music / "song.mp3"),))
    jellyfin.sync(cfg.library_dir, cfg.thumbs_dir)
    assert sorted(p.name for p in music.iterdir()) == ["song.mp3"]
