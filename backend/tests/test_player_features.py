"""1.3.0: captions/transcripts, playlists, continue watching, speed, watched
toggle, delete-after-watching votes, dark/light theme modes, frame thumbnails."""
import time

from homelib import cleaner, db, library, mock, subtitles

from .conftest import wait_for

P1 = {"X-Profile": "1"}
P2 = {"X-Profile": "2"}


def _download(client, idx, queue="personal", headers=P1):
    item = mock.public([v for v in mock.CATALOG if not v["is_short"]][idx])
    client.post("/api/downloads", json={**item, "quality": "compat", "queue": queue}, headers=headers)
    wait_for(lambda: client.get(f"/api/videos/{item['id']}").json()["status"] == "ready")
    return item["id"]


# ---- subtitles module ------------------------------------------------------------

def test_parse_srt_and_vtt():
    srt = "1\n00:00:01,000 --> 00:00:04,000\n<i>Hello</i> &amp; there\n\n2\n00:00:05,500 --> 00:00:07,000\nBye\n"
    assert subtitles.normalize(srt) == [{"start": 1.0, "end": 4.0, "text": "Hello & there"},
                                        {"start": 5.5, "end": 7.0, "text": "Bye"}]
    assert subtitles.normalize("WEBVTT\n\n00:01.000 --> 00:02.500\nshort form\n")[0]["start"] == 1.0
    vtt = subtitles.to_vtt([{"start": 61.5, "end": 62, "text": "a < b"}])
    assert "00:01:01.500 --> 00:01:02.000\na &lt; b" in vtt


def test_rolling_auto_captions_are_collapsed():
    raw = mock.subtitles(mock.CATALOG[0]["id"])[0]["text"]
    assert subtitles.looks_rolling(raw)
    cues = subtitles.normalize(raw)
    texts = [c["text"] for c in cues]
    assert texts[0].startswith("welcome back to")
    assert len(texts) == len(set(texts))  # no repeated lines
    assert all(a["end"] <= b["start"] for a, b in zip(cues, cues[1:]))


def test_bad_subtitle_rejected():
    try:
        subtitles.normalize("just some text")
    except subtitles.SubtitleError:
        return
    raise AssertionError("expected SubtitleError")


def test_choose_tracks_prefers_manual_then_auto():
    vtt = [{"ext": "json3", "url": "j"}, {"ext": "vtt", "url": "v"}]
    info = {"subtitles": {"en-GB": vtt}, "automatic_captions": {"en": vtt, "de-orig": vtt}}
    assert subtitles.choose_tracks(info, ["en"]) == [{"lang": "en", "kind": "manual", "url": "v"}]
    info = {"subtitles": {}, "automatic_captions": {"en-orig": vtt, "en": [{"ext": "vtt", "url": "x"}]}}
    assert subtitles.choose_tracks(info, ["en"])[0] == {"lang": "en", "kind": "auto", "url": "v"}
    info = {"automatic_captions": {"fr-orig": vtt, "en": [{"ext": "srv3", "url": "s"}]}}
    assert subtitles.choose_tracks(info, ["en"]) == [{"lang": "fr", "kind": "auto", "url": "v"}]


# ---- captions through the API -----------------------------------------------------

def test_download_saves_captions_and_transcript(client):
    vid = _download(client, 0)
    subs = client.get(f"/api/videos/{vid}/subtitles").json()["subtitles"]
    assert len(subs) == 1 and subs[0]["kind"] == "auto" and subs[0]["label"] == "English (auto)"
    r = client.get(subs[0]["url"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/vtt")
    assert r.text.startswith("WEBVTT")
    tr = client.get(f"/api/subtitles/{subs[0]['id']}/transcript").json()
    assert tr["cues"] and "welcome back" in tr["cues"][0]["text"]


def test_upload_and_delete_subtitle(client):
    vid = _download(client, 1)
    srt = "1\n00:00:00,500 --> 00:00:02,000\nHola\n"
    s = client.post(f"/api/videos/{vid}/subtitles", json={"content": srt, "filename": "Spanish.srt", "lang": "es"}).json()
    assert s["kind"] == "uploaded" and s["label"] == "Spanish" and s["lang"] == "es"
    assert client.get(f"/api/videos/{vid}/subtitles").json()["subtitles"][0]["id"] == s["id"]  # uploads first
    bad = client.post(f"/api/videos/{vid}/subtitles", json={"content": "nope", "filename": "x.srt"})
    assert bad.status_code == 400 and "SRT or VTT" in bad.json()["detail"]
    es = client.post(f"/api/videos/{vid}/subtitles", json={"content": srt, "filename": "es.srt", "lang": "es"}).json()
    assert es["label"] == "Spanish"
    assert client.delete(f"/api/subtitles/{s['id']}").json()["ok"]
    assert client.get(s["url"]).status_code == 404


def test_fetch_captions_for_old_video(client):
    vid = _download(client, 2)
    for s in client.get(f"/api/videos/{vid}/subtitles").json()["subtitles"]:
        client.delete(f"/api/subtitles/{s['id']}")
    r = client.post(f"/api/videos/{vid}/subtitles/fetch").json()
    assert r["added"] == 1 and len(r["subtitles"]) == 1
    client.post(f"/api/videos/{vid}/subtitles/fetch")  # refetch replaces, doesn't duplicate
    assert len(client.get(f"/api/videos/{vid}/subtitles").json()["subtitles"]) == 1


def test_delete_video_removes_captions(client):
    vid = _download(client, 3)
    sub = client.get(f"/api/videos/{vid}/subtitles").json()["subtitles"][0]
    path = db.q1("SELECT path FROM subtitles WHERE id=?", (sub["id"],))["path"]
    client.delete(f"/api/videos/{vid}", headers=P1)
    assert not db.q("SELECT 1 FROM subtitles WHERE video_id=?", (vid,))
    import os
    assert not os.path.exists(path)


# ---- continue watching, speed, watched toggle -----------------------------------------

def test_continue_watching_is_per_profile(client):
    a, b = _download(client, 4, "shared"), _download(client, 5, "shared")
    client.post(f"/api/watch/{a}", json={"position": 60, "duration": 600}, headers=P1)
    time.sleep(0.01)
    client.post(f"/api/watch/{b}", json={"position": 30, "duration": 600}, headers=P1)
    assert [v["id"] for v in client.get("/api/continue", headers=P1).json()["items"]] == [b, a]
    assert client.get("/api/continue", headers=P2).json()["items"] == []
    client.post(f"/api/watch/{b}", json={"position": 600, "duration": 600, "ended": True}, headers=P1)
    assert [v["id"] for v in client.get("/api/continue", headers=P1).json()["items"]] == [a]


def test_speed_is_per_video_and_profile(client):
    vid = _download(client, 6)
    client.post(f"/api/watch/{vid}", json={"position": 42, "duration": 600}, headers=P1)
    assert client.put(f"/api/watch/{vid}/speed", json={"speed": 1.5}, headers=P1).json()["speed"] == 1.5
    w = client.get(f"/api/watch/{vid}", headers=P1).json()
    assert w["speed"] == 1.5 and w["position"] == 42  # speed doesn't reset progress
    assert client.get(f"/api/watch/{vid}", headers=P2).json()["speed"] is None
    assert client.put(f"/api/watch/{vid}/speed", json={"speed": 1}, headers=P1).json()["speed"] is None
    assert client.put(f"/api/watch/{vid}/speed", json={"speed": 9}, headers=P1).status_code == 422


def test_mark_watched_and_unwatched(client):
    vid = _download(client, 7)
    w = client.post(f"/api/watch/{vid}/watched", json={"watched": True}, headers=P1).json()
    assert w["watched"] == 1
    assert client.get("/api/queue?scope=personal", headers=P1).json()["items"] == []  # done with it
    events = lambda: [e["type"] for e in client.get("/api/algorithm", headers=P1).json()["events"] if e["video_id"] == vid]
    assert "finish" in events()
    r = client.post(f"/api/videos/{vid}/delete-vote", headers=P2)  # not theirs: personal video of P1
    assert r.status_code == 400 and "Profile 1's video" in r.json()["detail"]
    assert client.get(f"/api/videos/{vid}").json()["status"] == "ready"
    w = client.post(f"/api/watch/{vid}/watched", json={"watched": False}, headers=P1).json()
    assert w["watched"] == 0 and w["position"] == 0
    assert "finish" not in events()


# ---- delete after watching --------------------------------------------------------------

def test_personal_video_deletes_right_away(client):
    vid = _download(client, 8, "personal")
    st = client.get(f"/api/videos/{vid}/delete-status").json()
    assert st["solo"] and st["relevant"] == [1]
    r = client.post(f"/api/videos/{vid}/delete-vote", headers=P1).json()
    assert r["deleted"]
    assert client.get(f"/api/videos/{vid}").json()["status"] == "deleted"


def test_shared_video_waits_for_both(client):
    vid = _download(client, 9, "shared")
    client.post(f"/api/watch/{vid}", json={"position": 600, "duration": 600, "ended": True}, headers=P1)
    r = client.post(f"/api/videos/{vid}/delete-vote", headers=P1).json()
    assert not r["deleted"] and r["pending"] == [2] and r["state"] == {"1": "voted", "2": "pending"}
    assert client.get(f"/api/videos/{vid}").json()["delete_votes"] == [1]
    # Undo, then vote again
    assert client.delete(f"/api/videos/{vid}/delete-vote", headers=P1).json()["votes"] == []
    client.post(f"/api/videos/{vid}/delete-vote", headers=P1)
    r = client.post(f"/api/videos/{vid}/delete-vote", headers=P2).json()
    assert r["deleted"]


def test_shared_video_vote_plus_watch_counts(client):
    vid = _download(client, 10, "shared")
    client.post(f"/api/videos/{vid}/delete-vote", headers=P1)
    client.post(f"/api/watch/{vid}", json={"position": 600, "duration": 600, "ended": True}, headers=P2)
    st = client.get(f"/api/videos/{vid}/delete-status").json()
    assert st["pending"] == [] and st["state"] == {"1": "voted", "2": "watched"}
    # P2 just watched (no vote): the cleaner removes it once the grace period passes.
    v = library.get_video(vid)
    ws = {r["profile_id"]: r for r in db.q("SELECT * FROM watch_state WHERE video_id=?", (vid,))}
    ok, _ = cleaner.decide(v, ws, [1, 2], time.time() + 3600, 24, {1})
    assert ok


# ---- playlists ------------------------------------------------------------------------

def test_playlists_crud_reorder_and_privacy(client):
    a, b, c = _download(client, 11, "none"), _download(client, 12, "none"), _download(client, 13, "none")
    mine = client.post("/api/playlists", json={"name": "Weekend", "scope": "personal"}, headers=P1).json()
    shared = client.post("/api/playlists", json={"name": "Movie night", "scope": "shared"}, headers=P1).json()
    for v in (a, b, c):
        client.post(f"/api/playlists/{mine['id']}/items", json={"video_id": v}, headers=P1)
    pl = client.post(f"/api/playlists/{mine['id']}/items", json={"video_id": a}, headers=P1).json()
    assert [i["id"] for i in pl["items"]] == [a, b, c] and pl["count"] == 3  # no duplicates
    ids = [i["item_id"] for i in pl["items"]]
    pl = client.post(f"/api/playlists/{mine['id']}/reorder", json={"item_ids": [ids[2], ids[0]]}, headers=P1).json()
    assert [i["id"] for i in pl["items"]] == [c, a, b]
    assert client.patch(f"/api/playlists/{mine['id']}", json={"name": "Lazy Sunday"}, headers=P1).json()["name"] == "Lazy Sunday"
    pl = client.delete(f"/api/playlists/{mine['id']}/items/{ids[1]}", headers=P1).json()
    assert [i["id"] for i in pl["items"]] == [c, a]
    # Personal playlists are private; shared ones are visible to both.
    assert client.get(f"/api/playlists/{mine['id']}", headers=P2).status_code == 404
    assert [p["name"] for p in client.get("/api/playlists", headers=P2).json()["playlists"]] == ["Movie night"]
    assert {p["id"] for p in client.get("/api/playlists", headers=P1).json()["playlists"]} == {mine["id"], shared["id"]}
    client.post(f"/api/playlists/{shared['id']}/items", json={"video_id": a}, headers=P2)
    assert library.get_video(a)["audience"] == "all"  # shared playlist -> everyone's video
    assert set(client.get(f"/api/videos/{a}/playlists", headers=P1).json()["playlist_ids"]) == {mine["id"], shared["id"]}
    assert client.get(f"/api/videos/{a}/playlists", headers=P2).json()["playlist_ids"] == [shared["id"]]
    assert client.delete(f"/api/playlists/{mine['id']}", headers=P2).status_code == 404
    assert client.delete(f"/api/playlists/{mine['id']}", headers=P1).json()["ok"]
    assert client.post("/api/playlists", json={"name": "  ", "scope": "shared"}, headers=P1).status_code == 400


def test_playlist_videos_are_not_auto_deleted(client):
    vid = _download(client, 14, "none")
    pl = client.post("/api/playlists", json={"name": "Keepers", "scope": "shared"}, headers=P1).json()
    client.post(f"/api/playlists/{pl['id']}/items", json={"video_id": vid}, headers=P1)
    v = client.get(f"/api/videos/{vid}").json()
    assert v["in_playlist"] and v["expires_at"] is None
    db.x("UPDATE videos SET downloaded_at=? WHERE id=?", (time.time() - 99 * 3600, vid))
    cfg = client.app_ref.state.cfg
    assert vid not in [d["id"] for d in cleaner.run(cfg.library_dir, cfg.thumbs_dir, 24)]
    # Deleting the video removes it from the playlist.
    client.delete(f"/api/videos/{vid}", headers=P1)
    assert client.get(f"/api/playlists/{pl['id']}", headers=P1).json()["items"] == []


# ---- appearance modes --------------------------------------------------------------------

def test_theme_modes_dark_light_auto(client):
    a = client.get("/api/appearance", headers=P1).json()
    assert a["mode"] == "dark" and a["dark"]["preset"] == "midnight" and a["light"]["preset"] == "daylight"
    assert a["presets"]["linen"]["scheme"] == "light"
    a = client.put("/api/appearance", json={"mode": "auto"}, headers=P1).json()
    assert a["mode"] == "auto" and a["light"]["colors"]["bg"] == "#F6F7F9"
    a = client.put("/api/appearance", json={"preset": "linen"}, headers=P1).json()
    assert a["mode"] == "auto" and a["light"]["preset"] == "linen" and a["dark"]["preset"] == "midnight"
    a = client.put("/api/appearance", json={"colors": {"bg": "#101010"}, "scheme": "dark"}, headers=P1).json()
    assert a["dark"]["colors"]["bg"] == "#101010" and a["light"]["colors"]["bg"] == "#F7F2EB"
    a = client.put("/api/appearance", json={"colors": {"accent": "#ff8800"}}, headers=P1).json()
    assert a["dark"]["colors"]["accent"] == a["light"]["colors"]["accent"] == "#FF8800"
    a = client.put("/api/appearance", json={"mode": "light"}, headers=P1).json()
    assert a["colors"]["bg"] == "#F7F2EB" and a["preset"] == "linen"


def test_old_theme_setting_is_upgraded(client):
    db.set_setting("profile:2", "theme", {"preset": "daylight", "colors": {"text": "#000000", "accent": "#123456"}})
    a = client.get("/api/appearance", headers=P2).json()
    assert a["mode"] == "light" and a["light"]["preset"] == "daylight"
    assert a["colors"]["text"] == "#000000" and a["dark"]["colors"]["text"] == "#F2F3F5"


# ---- thumbnails -----------------------------------------------------------------------

def test_frame_thumbnail_fallback(cfg, tmp_path):
    import shutil

    from homelib.tools import find_ffmpeg
    from homelib.ytlib import YouTube

    ffmpeg, _ = find_ffmpeg()
    clip = mock.fixture_mp4(tmp_path / "fx", False, ffmpeg)
    if not clip:
        return  # no ffmpeg on this machine
    cfg.mock = False
    yt = YouTube(cfg, ffmpeg, None, (None, None))
    out = yt.thumbnail("abcdefghijk", "http://127.0.0.1:9/nothing.jpg", tmp_path / "thumbs",
                       local_file=str(clip), at=1.0)
    assert out and out.suffix == ".jpg" and out.stat().st_size > 0
    shutil.rmtree(tmp_path / "thumbs")
    assert yt.thumbnail("abcdefghijk", "http://127.0.0.1:9/x.jpg", tmp_path / "thumbs") is None
