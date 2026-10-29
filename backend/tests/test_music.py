"""Music page: search, mp3/m4a downloads, file download, never auto-deleted."""
import pytest

from homelib import cleaner, db, mock
from homelib.db import now
from homelib.formats import ydl_audio_opts

from .conftest import wait_for

P1 = {"X-Profile": "1"}


def test_audio_opts():
    o = ydl_audio_opts("mp3")
    keys = [p["key"] for p in o["postprocessors"]]
    assert o["format"] == "bestaudio/best" and keys[0] == "FFmpegExtractAudio"
    assert o["postprocessors"][0]["preferredcodec"] == "mp3"
    assert ydl_audio_opts("m4a")["format"].startswith("bestaudio[ext=m4a]")
    with pytest.raises(ValueError):
        ydl_audio_opts("flac")


def _download(client, fmt="mp3"):
    res = client.get("/api/music/search", params={"q": "theo lark"}).json()["results"]
    assert res and all(r["channel"] == "Theo Lark" for r in res[:3])
    song = res[0]
    d = client.post("/api/music/download", json={**song, "format": fmt}, headers=P1).json()
    vid = d["video_id"]
    assert vid == "au_" + song["id"] and d["quality"] == fmt
    wait_for(lambda: client.get(f"/api/videos/{vid}").json()["status"] == "ready")
    return song, vid


def test_mp3_flow(client, cfg):
    song, vid = _download(client, "mp3")
    tracks = client.get("/api/music").json()["tracks"]
    assert [t["id"] for t in tracks] == [vid]
    t = tracks[0]
    assert t["media"] == "audio" and t["file"] == f"/api/music/{vid}/file" and t["format"] == "mp3"
    # plays in the page
    r = client.get(t["stream"], headers={"Range": "bytes=0-1"})
    assert r.status_code == 206 and r.headers["content-type"] == "audio/mpeg"
    # "Save to iPhone" is an attachment named after the song
    r = client.get(t["file"])
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;") and "Theo Lark - " in cd and ".mp3" in cd
    # stays out of the video library, queues and shorts
    assert vid not in {v["id"] for v in client.get("/api/library").json()["videos"]}
    assert client.get("/api/queue?scope=personal", headers=P1).json()["items"] == []
    # the same YouTube id can still be downloaded as a video row separately
    assert client.get(f"/api/videos/{song['id']}").status_code == 404
    # search marks it as downloaded
    res = client.get("/api/music/search", params={"q": "theo lark"}).json()["results"]
    assert next(r for r in res if r["id"] == song["id"])["local_status"] == "ready"


def test_m4a_and_default_format(client):
    client.put("/api/settings", json={"music_format": "m4a"})
    assert client.get("/api/settings").json()["music_format"] == "m4a"
    song = mock.public(mock.SONG_CATALOG[5])
    d = client.post("/api/music/download", json=song, headers=P1).json()
    assert d["quality"] == "m4a"
    wait_for(lambda: client.get(f"/api/videos/{d['video_id']}").json()["status"] == "ready")
    r = client.get(f"/api/music/{d['video_id']}/file")
    assert r.headers["content-type"] == "audio/mp4" and ".m4a" in r.headers["content-disposition"]


def test_music_is_never_auto_deleted(client, cfg):
    _, vid = _download(client)
    db.x("UPDATE videos SET downloaded_at=? WHERE id=?", (now() - 1000 * 3600, vid))
    assert cleaner.run(cfg.library_dir, cfg.thumbs_dir, 12) == []
    assert client.get(f"/api/videos/{vid}").json()["status"] == "ready"
    # manual delete works and is not a "deleted unwatched" signal
    client.delete(f"/api/videos/{vid}", headers=P1)
    assert client.get(f"/api/videos/{vid}").json()["status"] == "deleted"
    assert not db.q("SELECT 1 FROM events WHERE type='delete_unwatched'")
    assert client.get(f"/api/music/{vid}/file").status_code == 404


def test_file_endpoint_rejects_videos(client):
    item = mock.public(mock.CATALOG[0])
    client.post("/api/downloads", json={**item, "quality": "compat"}, headers=P1)
    wait_for(lambda: client.get(f"/api/videos/{item['id']}").json()["status"] == "ready")
    assert client.get(f"/api/music/{item['id']}/file").status_code == 404


def test_old_database_gets_media_column(tmp_path):
    import sqlite3

    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    # the v1.0.0 schema: same as today minus the media column
    c.executescript("\n".join(l for l in db.SCHEMA.splitlines() if not l.strip().startswith("media ")))
    c.execute("INSERT INTO videos(id, created_at) VALUES('old00000001', 0)")
    c.commit()
    c.close()
    db.init(p)
    assert "media" in {r["name"] for r in db.q("PRAGMA table_info(videos)")}
    assert db.q1("SELECT media FROM videos WHERE id='old00000001'")["media"] == "video"
