"""End-to-end through the HTTP API, mock YouTube."""
from homelib import mock

from .conftest import wait_for

P1 = {"X-Profile": "1"}
P2 = {"X-Profile": "2"}


def test_profiles_and_rename(client):
    ps = client.get("/api/profiles").json()
    assert [p["accent"] for p in ps] == ["#46D3B8", "#F2B84B"]
    r = client.patch("/api/profiles/2", json={"name": "Sam"})
    assert r.json()["name"] == "Sam"
    r = client.post("/api/profiles/2/select")
    assert r.status_code == 200 and "hl_profile" in r.headers.get("set-cookie", "")


def test_search_download_watch_flow(client):
    res = client.get("/api/search", params={"q": "sourdough"}, headers=P1).json()["results"]
    assert res and "sourdough" in res[0]["title"].lower()
    item = next(r for r in res if not r["is_short"])
    d = client.post("/api/downloads", json={**item, "quality": "compat", "queue": "personal"}, headers=P1).json()
    assert d["status"] in ("queued", "running", "done")
    wait_for(lambda: client.get(f"/api/videos/{item['id']}").json()["status"] == "ready")
    v = client.get(f"/api/videos/{item['id']}").json()
    assert v["stream"] and v["audience"] == "p:1" and v["relevant"] == [1]
    # queued in my personal queue only
    assert [i["id"] for i in client.get("/api/queue?scope=personal", headers=P1).json()["items"]] == [item["id"]]
    assert client.get("/api/queue?scope=personal", headers=P2).json()["items"] == []
    # stream works
    r = client.get(v["stream"], headers={"Range": "bytes=0-1"})
    assert r.status_code == 206
    # thumbnail works (svg in mock mode)
    assert client.get(f"/api/thumb/{item['id']}").status_code == 200
    # progress + finish
    client.post(f"/api/watch/{item['id']}", json={"position": 30, "duration": 100}, headers=P1)
    assert client.get(f"/api/watch/{item['id']}", headers=P1).json()["position"] == 30
    w = client.post(f"/api/watch/{item['id']}", json={"position": 100, "duration": 100, "ended": True},
                    headers=P1).json()
    assert w["watched"] == 1
    # search + finish made it into the algorithm
    algo = client.get("/api/algorithm", headers=P1).json()
    types = {e["type"] for e in algo["events"]}
    assert {"search", "finish"} <= types
    assert any(k["term"] == "sourdough" for k in algo["keywords"])


def test_pause_resume_cancel(client, monkeypatch):
    monkeypatch.setenv("HOMELIB_MOCK_DL_SECONDS", "3")
    item = mock.public(next(v for v in mock.CATALOG if not v["is_short"]))
    d = client.post("/api/downloads", json={**item, "quality": "best"}, headers=P1).json()
    did = d["id"]
    wait_for(lambda: client.get("/api/downloads").json()["downloads"][0]["status"] == "running")
    client.post(f"/api/downloads/{did}/pause")
    wait_for(lambda: _dl(client, did)["status"] == "paused")
    client.post(f"/api/downloads/{did}/resume")
    wait_for(lambda: _dl(client, did)["status"] in ("running", "done"))
    client.post(f"/api/downloads/{did}/cancel")
    wait_for(lambda: _dl(client, did)["status"] in ("cancelled", "done"))
    if _dl(client, did)["status"] == "cancelled":
        assert client.get(f"/api/videos/{item['id']}").json()["status"] == "new"


def _dl(client, did):
    return next(d for d in client.get("/api/downloads").json()["downloads"] if d["id"] == did)


def test_disk_guard(client):
    client.app_ref.state.cfg.min_free_gb = 10 ** 6  # a petabyte
    item = mock.public(mock.CATALOG[3])
    r = client.post("/api/downloads", json={**item, "quality": "compat"}, headers=P1)
    assert r.status_code == 507 and r.json()["code"] == "disk_full"


def test_foryou_shorts_algorithm_endpoints(client):
    from homelib import recommender, scheduler

    app = client.app_ref
    client.get("/api/search", params={"q": "japan train travel"}, headers=P1)
    recommender.fetch_candidates(app.state.yt, 1)
    recommender.fetch_candidates(app.state.yt, 2)
    fy = client.get("/api/foryou", headers=P1).json()
    assert fy["items"] and all(i["reasons"] for i in fy["items"])
    both = client.get("/api/foryou?mode=both", headers=P1).json()
    assert both["items"]
    why = client.get(f"/api/foryou/why/{fy['items'][0]['id']}", headers=P1).json()
    assert why["result"]["reasons"] and why["terms"]

    # Shorts prefetch keeps >= 10 ahead, all compatible quality
    n = scheduler.prefetch_shorts(app.state.cfg, app.state.yt, app.state.dm)
    assert n >= 10
    wait_for(lambda: len(client.get("/api/shorts", headers=P1).json()["items"]) >= 10, timeout=30)
    sh = client.get("/api/shorts", headers=P1).json()["items"]
    assert all(s["is_short"] and s["quality"] == "compat" for s in sh)
    first = sh[0]["id"]
    client.post(f"/api/shorts/{first}/seen", json={"seconds": 1}, headers=P1)  # skip
    assert first not in {s["id"] for s in client.get("/api/shorts", headers=P1).json()["items"]}
    assert first in {s["id"] for s in client.get("/api/shorts", headers=P2).json()["items"]}

    # algorithm controls
    assert client.put("/api/algorithm", json={"half_life_days": 7, "exploration": 0.5},
                      headers=P1).json()["half_life_days"] == 7
    assert client.put("/api/algorithm", json={"half_life_days": 3}, headers=P1).status_code == 422
    client.post("/api/algorithm/boost", json={"kind": "kw", "term": "pottery"}, headers=P1)
    client.post("/api/algorithm/mute", json={"kind": "ch", "term": "Whisker Weekly"}, headers=P1)
    a = client.get("/api/algorithm", headers=P1).json()
    assert a["settings"]["boosts"]["kw:pottery"] == 2.0 and "ch:Whisker Weekly" in a["settings"]["mutes"]
    assert all(i["channel"] != "Whisker Weekly" for i in client.get("/api/foryou", headers=P1).json()["items"])
    ev = a["events"][0]["id"]
    assert client.delete(f"/api/algorithm/events/{ev}", headers=P1).status_code == 200
    assert client.delete(f"/api/algorithm/events/{ev}", headers=P2).status_code == 404
    ex = client.get("/api/algorithm/export", headers=P1)
    assert ex.status_code == 200 and "attachment" in ex.headers["content-disposition"]
    assert client.post("/api/algorithm/reset", headers=P1).json()["ok"]
    assert client.get("/api/algorithm", headers=P1).json()["events"] == []


def test_like_keep_delete(client):
    item = mock.public(mock.CATALOG[10])
    client.post("/api/downloads", json={**item, "quality": "compat", "queue": "shared"}, headers=P1)
    wait_for(lambda: client.get(f"/api/videos/{item['id']}").json()["status"] == "ready")
    assert client.post(f"/api/videos/{item['id']}/like", headers=P2).json()["rating"] == 1
    assert client.post(f"/api/videos/{item['id']}/dislike", headers=P2).json()["rating"] == -1
    assert client.post(f"/api/videos/{item['id']}/keep", json={"keep": True}).json()["keep"] == 1
    lib = client.get("/api/library").json()
    assert lib["videos"][0]["expires_at"] is None
    r = client.delete(f"/api/videos/{item['id']}", headers=P2).json()
    assert r["reason"] == "manual"
    assert client.get(f"/api/videos/{item['id']}").json()["status"] == "deleted"


def test_appearance_and_settings(client):
    a = client.get("/api/appearance", headers=P2).json()
    assert a["colors"]["accent"] == "#F2B84B" and "oled" in a["presets"]
    a = client.put("/api/appearance", json={"preset": "oled"}, headers=P2).json()
    assert a["colors"]["bg"] == "#000000"
    a = client.put("/api/appearance", json={"colors": {"accent": "#ff0000", "progress": "#00ff00"}},
                   headers=P2).json()
    assert a["colors"]["accent"] == "#FF0000"
    assert client.put("/api/appearance", json={"colors": {"accent": "red"}}, headers=P2).status_code == 400
    s = client.put("/api/settings", json={"default_quality": "best", "auto_delete_hours": 48}).json()
    assert s == {**s, "default_quality": "best", "auto_delete_hours": 48}
    assert client.put("/api/settings", json={"auto_delete_hours": 36}).status_code == 422


def test_status_and_static(client):
    s = client.get("/api/status").json()
    assert s["ok"] and s["mock"] and "free" in s["disk"]
    assert client.get("/api/health").json() == {"ok": True}


def test_paste_link_search(client):
    v = mock.CATALOG[0]
    r = client.get("/api/search", params={"q": f"https://youtu.be/{v['id']}"}).json()["results"]
    assert [x["id"] for x in r] == [v["id"]]
