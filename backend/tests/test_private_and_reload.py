"""1.4.0: private downloads + "Share with both of us", sharing playlists,
reloading the For you feed, profile photos."""
import base64

from homelib import cleaner, db, library, mock, recommender

from .test_player_features import P1, P2, _download


def _lib_ids(client, headers):
    return {v["id"] for v in client.get("/api/library", headers=headers).json()["videos"]}


# ---- private downloads ---------------------------------------------------------------

def test_my_downloads_are_private_until_shared(client):
    mine = _download(client, 20, "personal")
    plain = _download(client, 21, "none")
    both = _download(client, 22, "shared")
    assert _lib_ids(client, P1) >= {mine, plain, both}
    assert both in _lib_ids(client, P2) and not {mine, plain} & _lib_ids(client, P2)
    # Not in her downloads list or search either.
    dl_ids = {d["video_id"] for d in client.get("/api/downloads", headers=P2).json()["downloads"]}
    assert both in dl_ids and not {mine, plain} & dl_ids
    title = library.get_video(plain)["title"]
    hit = [r for r in client.get("/api/search", params={"q": title}, headers=P2).json()["results"] if r["id"] == plain]
    assert hit and hit[0]["local_status"] is None
    hit = [r for r in client.get("/api/search", params={"q": title}, headers=P1).json()["results"] if r["id"] == plain]
    assert hit[0]["local_status"] == "ready"

    v = client.get(f"/api/videos/{plain}").json()
    assert v["shared"] is False and v["for"] == [1]
    # She can't share it (she can't see it); I can.
    assert client.post(f"/api/videos/{plain}/share", headers=P2).status_code == 404
    v = client.post(f"/api/videos/{plain}/share", headers=P1).json()
    assert v["shared"] is True and v["for"] == [1, 2]
    assert plain in _lib_ids(client, P2)
    assert plain in {i["id"] for i in client.get("/api/queue?scope=shared", headers=P2).json()["items"]}


def test_private_video_deletes_once_i_watched_it(client):
    vid = _download(client, 23, "none")
    client.post(f"/api/watch/{vid}", json={"position": 600, "duration": 600, "ended": True}, headers=P1)
    v = library.get_video(vid)
    ws = {r["profile_id"]: r for r in db.q("SELECT * FROM watch_state WHERE video_id=?", (vid,))}
    ok, reason = cleaner.decide(v, ws, library.relevant_profiles(vid), ws[1]["updated_at"] + 3600, 24)
    assert ok and "profile 1" in reason
    assert client.get(f"/api/videos/{vid}/delete-status").json()["solo"]


def test_she_downloads_the_same_video_too(client):
    vid = _download(client, 24, "none")
    item = mock.public(next(v for v in mock.CATALOG if v["id"] == vid))
    client.post("/api/downloads", json={**item, "quality": "compat", "queue": "none"}, headers=P2)
    # Both picked it, so it's for both of us now.
    assert vid in _lib_ids(client, P2)
    assert library.relevant_profiles(vid) == [1, 2]


# ---- playlists -----------------------------------------------------------------------

def test_share_a_personal_playlist(client):
    vid = _download(client, 25, "none")
    pl = client.post("/api/playlists", json={"name": "Weekend"}, headers=P1).json()
    assert pl["scope"] == "personal"  # private by default
    client.post(f"/api/playlists/{pl['id']}/items", json={"video_id": vid}, headers=P1)
    assert vid not in _lib_ids(client, P2)
    assert client.patch(f"/api/playlists/{pl['id']}", json={"scope": "shared"}, headers=P2).status_code == 404
    pl = client.patch(f"/api/playlists/{pl['id']}", json={"scope": "shared"}, headers=P1).json()
    assert pl["scope"] == "shared" and pl["profile_id"] is None and pl["name"] == "Weekend"
    assert client.get(f"/api/playlists/{pl['id']}", headers=P2).json()["count"] == 1
    assert vid in _lib_ids(client, P2)
    # Still in a playlist, so still never auto-deleted.
    assert client.get(f"/api/videos/{vid}").json()["expires_at"] is None


# ---- For you reload ------------------------------------------------------------------

def test_reload_brings_all_new_suggestions(client):
    yt = client.app_ref.state.yt
    client.get("/api/search", params={"q": "japan train travel"}, headers=P1)
    recommender.fetch_candidates(yt, 1)
    first = [i["id"] for i in client.get("/api/foryou", headers=P1).json()["items"]]
    assert first
    # Opening the feed again doesn't change it; reloading does.
    assert [i["id"] for i in client.get("/api/foryou", headers=P1).json()["items"]] == first
    r = client.post("/api/foryou/refresh", headers=P1).json()
    assert r["fetched"] > 0
    second = [i["id"] for i in client.get("/api/foryou", headers=P1).json()["items"]]
    assert second and not set(first) & set(second)
    # "Both of us" is reloaded separately.
    both = [i["id"] for i in client.get("/api/foryou?mode=both", headers=P2).json()["items"]]
    client.post("/api/foryou/refresh?mode=both", headers=P2)
    again = [i["id"] for i in client.get("/api/foryou?mode=both", headers=P1).json()["items"]]
    assert not set(both) & set(again)


def test_nightly_reload_sets_aside_what_was_shown(client):
    from homelib import scheduler

    yt = client.app_ref.state.yt
    recommender.fetch_candidates(yt, 1)
    first = {i["id"] for i in client.get("/api/foryou", headers=P1).json()["items"]}
    scheduler.nightly(client.app_ref.state.cfg, yt)
    after = {i["id"] for i in client.get("/api/foryou", headers=P1).json()["items"]}
    assert first and not first & after


def test_reload_offline_keeps_the_old_pool(client, monkeypatch):
    yt = client.app_ref.state.yt
    recommender.fetch_candidates(yt, 1)
    before = db.q1("SELECT COUNT(*) n FROM reco_cache WHERE profile_id=1")["n"]

    def boom(*a, **k):
        raise RuntimeError("no internet")

    monkeypatch.setattr(yt, "search", boom)
    assert recommender.fetch_candidates(yt, 1, fresh=True) == 0
    assert db.q1("SELECT COUNT(*) n FROM reco_cache WHERE profile_id=1")["n"] == before


# ---- profile photos ------------------------------------------------------------------

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def test_profile_photo_upload_and_remove(client):
    assert client.get("/api/profiles").json()[0]["avatar"] is None
    data = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
    p = client.put("/api/profiles/1/avatar", json={"data": data}).json()
    assert p["avatar"].startswith("/api/profiles/1/avatar?v=")
    r = client.get(p["avatar"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == JPEG
    assert client.get("/api/profiles").json()[0]["avatar"] == p["avatar"]
    # Not an image / not base64 / too big.
    bad = base64.b64encode(b"hello").decode()
    assert client.put("/api/profiles/1/avatar", json={"data": bad}).status_code == 400
    assert client.put("/api/profiles/1/avatar", json={"data": "%%%"}).status_code == 400
    big = base64.b64encode(JPEG + b"\x00" * library.AVATAR_MAX_BYTES).decode()
    assert client.put("/api/profiles/1/avatar", json={"data": big}).status_code in (400, 422)
    assert client.delete("/api/profiles/1/avatar").json()["avatar"] is None
    assert client.get("/api/profiles/1/avatar").status_code == 404
