"""The iOS share-sheet shortcut endpoint."""
from homelib import mock

from .conftest import wait_for


def test_share_video_to_my_queue_by_name(client):
    v = mock.CATALOG[2]
    r = client.post("/api/share", json={"url": f"Watch this! https://youtu.be/{v['id']}?si=abc", "profile": "pRoFiLe 2",
                                        "action": "video-personal"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and v["title"] in j["message"] and "My queue" in j["message"] and "Profile 2" in j["message"]
    assert [i["id"] for i in client.get("/api/queue?scope=personal", headers={"X-Profile": "2"}).json()["items"]] == [v["id"]]
    wait_for(lambda: client.get(f"/api/videos/{v['id']}").json()["status"] == "ready")
    again = client.post("/api/share", json={"url": v["url"], "profile": 2, "action": "video-shared"}).json()
    assert again["message"].startswith("Already downloaded") and "Shared queue" in again["message"]


def test_share_music(client):
    s = mock.SONG_CATALOG[0]
    j = client.post("/api/share", json={"url": f"https://music.youtube.com/watch?v={s['id']}&feature=share",
                                        "profile": "Profile 1", "action": "mp3"}).json()
    assert j["ok"] and "Music (MP3)" in j["message"] and j["video_id"] == "au_" + s["id"]


def test_share_errors_always_have_message(client):
    for body, code in [({"url": "https://example.com/cat", "profile": "Profile 1"}, 400),
                       ({"url": "https://youtu.be/" + mock.CATALOG[0]["id"], "profile": "Nobody"}, 400),
                       ({"url": "https://youtu.be/" + mock.CATALOG[0]["id"], "action": "dance"}, 400),
                       ({"url": "https://youtu.be/zzzzzzzzzzz"}, 404)]:
        r = client.post("/api/share", json=body)
        assert r.status_code == code and r.json()["message"], (body, r.text)
