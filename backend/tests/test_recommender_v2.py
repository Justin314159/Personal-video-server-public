"""1.5.0 recommender: interests, caps, exploration, blocks, profiles, shared
feed, freshness, subscriptions. Mock YouTube (mock.CATALOG: 10 channels x 8)."""
import json
import math
import time

import pytest

from homelib import db, embeddings, interests, library, mock, recommender, subscriptions

DAY = 86400
CH = {c: [v for v in mock.CATALOG if v["channel"] == c and not v["is_short"]] for c, _, _ in mock.CHANNELS}


class FakeYT:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def search(self, q, n=15, shorts=False):
        self.calls.append(("search", q))
        if self.fail:
            raise RuntimeError("offline")
        return mock.search(q, n, shorts_only=shorts)

    def channel_uploads(self, cid, n=15, channel=""):
        self.calls.append(("channel", cid))
        if self.fail:
            raise RuntimeError("offline")
        return mock.channel_uploads(cid, n, channel)

    def thumbnail(self, vid, url, dest, **k):
        dest.mkdir(parents=True, exist_ok=True)
        p = dest / f"{vid}.svg"
        p.write_text(mock.thumbnail_svg(vid))
        return p


@pytest.fixture(autouse=True)
def builtin_model():
    embeddings.configure("builtin")
    recommender.REQUEST_DELAY = 0.0
    yield


def watch(pid, v, days_ago=1.0, like=False):
    library.upsert_video(mock.public(v))
    library.update_progress(pid, v["id"], 600, 600, ended=True)
    t = time.time() - days_ago * DAY
    db.x("UPDATE events SET ts=? WHERE profile_id=? AND video_id=?", (t, pid, v["id"]))
    if like:
        library.rate(pid, v["id"], 1)


def varied_history(pid, space=2, others=("Crumb & Crust", "Sawdust Sunday", "Slow Rail Japan", "Six String Lab",
                                          "Morning Mat", "Homelab Hour", "Green Thumb Plot"), per=3):
    for v in CH["Orbit Notes"][:space]:
        watch(pid, v)
    for i, c in enumerate(others):
        for j, v in enumerate(CH[c][:per]):
            watch(pid, v, days_ago=2 + i + j)


def build(pid):
    return interests.rebuild(pid, recommender.algo_settings(pid), compute=True)


def share_of(feed, pred):
    return sum(1 for c in feed if pred(c)) / max(1, len(feed))


# ---- interests: weighting ---------------------------------------------------------

def test_weights_grow_sublinearly_and_decay():
    assert interests.effective_weight({"evidence": 2}) == pytest.approx(math.log1p(2), abs=1e-3)
    two, twenty = math.log1p(2), math.log1p(20)
    assert twenty / two < 3  # 10x the videos is < 3x the weight
    assert interests.decay(30 * DAY, 30) == pytest.approx(0.5)


def test_two_videos_form_one_small_interest(fresh_db):
    varied_history(1)
    rows = build(1)
    space = [r for r in rows if "space" in r["label"].lower() or any(
        "orbit" in c.lower() for c in r["stats"].get("channels", []))]
    assert len(space) == 1, [r["label"] for r in rows]
    weights = sorted((interests.effective_weight(r) for r in rows), reverse=True)
    # Two videos don't outweigh a topic you watched three of.
    assert interests.effective_weight(space[0]) <= weights[0]
    assert space[0]["label"] == "Space science"


def test_two_space_videos_cannot_dominate_the_feed(fresh_db):
    varied_history(1)
    yt = FakeYT()
    recommender.refresh(yt, [1], [], [])
    feed = recommender.rank(1, "me")
    assert len(feed) >= 20
    assert share_of(feed, lambda c: c["channel"] == "Orbit Notes") <= recommender.MAX_SHARE
    assert len({c["channel"] for c in feed}) >= 5


def test_only_two_videos_still_capped(fresh_db):
    for v in CH["Orbit Notes"][:2]:
        watch(1, v)
    recommender.refresh(FakeYT(), [1], [], [])
    feed = recommender.rank(1, "me")
    space = [c for c in feed if c.get("slot") == "interest"]
    assert len(space) <= math.ceil(recommender.MAX_SHARE * len(feed)) + 1
    assert share_of(feed, lambda c: c.get("slot") == "explore") >= 0.5


# ---- feed assembly ------------------------------------------------------------------------

def _fake(n, group, score=1.0, w=1.0):
    return [{"id": f"{group}{i}", "group": group, "group_weight": w, "score": score - i * 0.001, "reasons": ["x"]}
            for i in range(n)]


def test_per_interest_cap_and_interleaving():
    scored = _fake(40, 1, w=5.0) + _fake(40, 2, w=1.0) + _fake(40, 3, w=1.0) + _fake(40, "explore", 0.5)
    out = recommender.assemble(scored, [], 40, 0.2)
    n_int = 40 - 8
    by = {g: sum(1 for s in out if s["group"] == g) for g in (1, 2, 3, "explore")}
    assert by[1] <= math.ceil(recommender.MAX_SHARE * n_int)  # the heavy interest is capped
    assert by["explore"] >= 8
    assert len(out) == 40
    runs = max(len(list(g)) for _, g in __import__("itertools").groupby(s["group"] for s in out))
    assert runs <= 2  # mixed, not clumped


def test_pinned_interest_always_appears():
    scored = _fake(40, 1, w=5.0) + _fake(40, 2, w=4.0) + _fake(3, 9, score=0.1, w=0.05) + _fake(20, "explore")
    out = recommender.assemble(scored, [], 40, 0.2, pinned={9})
    assert sum(1 for s in out if s["group"] == 9) >= 2
    assert 9 in {s["group"] for s in out[:12]}  # not buried at the end


@pytest.mark.parametrize("explore", [0.0, 0.2, 0.5])
def test_exploration_share_follows_the_slider(explore):
    scored = _fake(40, 1) + _fake(40, 2) + _fake(40, 3) + _fake(40, 4) + _fake(40, "explore", 0.2)
    out = recommender.assemble(scored, [], 40, explore)
    assert sum(1 for s in out if s["group"] == "explore") == round(40 * explore)


def test_exploration_slider_controls_searches_and_feed(fresh_db):
    varied_history(1)
    build(1)
    for value in (0.0, 0.5):
        recommender.save_algo_settings(1, {"exploration": value})
        plan = recommender.plan_queries(1, budget=14)
        n_exp = sum(1 for q in plan if q.kind in ("explore", "starter"))
        assert n_exp == round(14 * value), (value, [(q.kind, q.q) for q in plan])
    # The UI slider still talks to the same setting, with the same range.
    assert recommender.save_algo_settings(1, {"exploration": 0.35})["exploration"] == 0.35


def test_query_budget_is_fixed_and_split_by_weight(fresh_db):
    varied_history(1)
    recommender.save_algo_settings(1, {"exploration": 0.2})
    build(1)
    plan = recommender.plan_queries(1, budget=14)
    assert len([q for q in plan if not q.shorts]) <= 14
    per = {}
    for q in plan:
        if q.kind == "interest":
            per[q.interest_id] = per.get(q.interest_id, 0) + 1
    assert max(per.values()) <= math.ceil(recommender.QUERY_SHARE_CAP * 14)
    assert len({(q.q.lower(), q.shorts) for q in plan}) == len(plan)  # varied, no repeats
    yt = FakeYT()
    recommender.fetch_candidates(yt, 1, budget=recommender.Budget(5, delay=0))
    assert len(yt.calls) <= 5


# ---- blocks, not interested, feedback ------------------------------------------------------

def test_blocked_channel_topic_and_not_interested_are_filtered(fresh_db):
    varied_history(1)
    recommender.refresh(FakeYT(), [1], [], [])
    feed = recommender.rank(1, "me")
    victim = feed[0]
    library.not_interested(1, victim)
    recommender.set_mute(1, "ch", "Sawdust Sunday", True)
    recommender.set_mute(1, "kw", "yoga", True)
    feed2 = recommender.rank(1, "me", limit=200)
    assert victim["id"] not in {c["id"] for c in feed2}
    assert not [c for c in feed2 if c["channel"] == "Sawdust Sunday"]
    assert not [c for c in feed2 if "yoga" in c["title"].lower()]
    # Topic blocks match by meaning too, not just the word.
    ctx = recommender.build_ctx(1)
    pilates = mock.public(next(v for v in CH["Morning Mat"] if "pilates" in v["title"].lower()))
    pilates["keywords"] = library.video_keywords(pilates["title"])
    vec = recommender._vectors_for([pilates])[pilates["id"]]
    assert "yoga" not in pilates["title"].lower() and recommender.blocked(pilates, vec, ctx) == "topic “yoga”"
    assert any(n["video_id"] == victim["id"] for n in library.not_interested_list(1))
    library.undo_not_interested(1, victim["id"])
    assert not library.not_interested_list(1)


def test_less_like_this_pushes_similar_videos_down(fresh_db):
    varied_history(1)
    recommender.refresh(FakeYT(), [1], [], [])
    before = {c["id"]: c["score"] for c in recommender.rank(1, "me", limit=200)}
    bread = [v for v in CH["Crumb & Crust"] if v["id"] in {x["id"] for x in mock.CATALOG}]
    library.upsert_video(mock.public(bread[5]))
    library.feedback(1, bread[5]["id"], "less")
    after = {c["id"]: c["score"] for c in recommender.rank(1, "me", limit=200)}
    same = [v["id"] for v in bread if v["id"] in before and v["id"] in after]
    assert same and all(after[i] < before[i] for i in same)


def test_rewatch_and_explicit_feedback_are_recorded(fresh_db):
    v = CH["Orbit Notes"][0]
    library.upsert_video(mock.public(v))
    library.update_progress(1, v["id"], 600, 600, ended=True)
    library.update_progress(1, v["id"], 600, 600, ended=True)  # straight away: not a rewatch yet
    db.x("UPDATE events SET ts=ts-86400 WHERE profile_id=1")
    library.update_progress(1, v["id"], 600, 600, ended=True)
    types = [e["type"] for e in db.q("SELECT type FROM events WHERE profile_id=1 ORDER BY id")]
    assert types.count("finish") == 1 and types.count("rewatch") == 1
    library.feedback(1, v["id"], "more")
    library.feedback(1, v["id"], "less")  # replaces "more"
    assert library.get_feedback(1, v["id"]) == "less"
    items = interests.collect_items(1, recommender.algo_settings(1))
    assert items[v["id"]].signal < 0  # explicit "less" outweighs finishing it


def test_manual_topic_works_without_history_and_wins(fresh_db):
    t = interests.add_topic(1, "Woodworking projects", weight=3.0, pinned=True)
    assert t["kind"] == "manual" and t["pinned"] and t["weight"] == 3.0
    plan = recommender.plan_queries(1, budget=10)
    assert any(q.interest_id == t["id"] for q in plan)
    recommender.refresh(FakeYT(), [1], [], [])
    feed = recommender.rank(1, "me")
    assert any(c.get("interest") == "Woodworking projects" for c in feed)
    # rename, weight, auto, delete
    assert interests.update(1, t["id"], label="Joinery")["label"] == "Joinery"
    assert interests.update(1, t["id"], weight=-1)["weight"] == -1
    interests.delete(1, t["id"])
    assert not interests.rows(1)


def test_learned_interest_edits_survive_rebuilds(fresh_db):
    varied_history(1)
    rows = build(1)
    r = next(r for r in rows if r["label"] == "Space science")
    interests.update(1, r["id"], label="Astronomy", weight=4.0, pinned=True)
    rows = build(1)
    r2 = next(x for x in rows if x["id"] == r["id"])
    assert r2["label"] == "Astronomy" and r2["manual_weight"] == 4.0 and r2["pinned"]
    interests.delete(1, r["id"])
    assert r["id"] not in {x["id"] for x in build(1)}  # deleted stays deleted
    assert any(x["id"] == r["id"] for x in interests.rows(1, include_hidden=True))


def test_block_an_interest(fresh_db):
    varied_history(1)
    rows = build(1)
    r = next(r for r in rows if r["label"] == "Space science")
    recommender.block_interest(1, r["id"])
    assert "kw:space science" in recommender.algo_settings(1)["mutes"]
    recommender.refresh(FakeYT(), [1], [], [])
    assert not [c for c in recommender.rank(1, "me") if c["channel"] == "Orbit Notes"]


# ---- migration from 1.4 -------------------------------------------------------------------

def test_migrates_14_keywords_and_mutes(fresh_db):
    db.set_setting("profile:1", "algo", {
        "half_life_days": 20, "exploration": 0.3, "channel_loyalty": 0.4,
        "boosts": {"kw:sourdough": 2.0, "kw:pottery": 1.5, "ch:Knife & Fire": 2.0},
        "mutes": ["kw:cats", "ch:Whisker Weekly"], "forgotten": ["kw:bread"],
        "starter_topics": ["home cooking", "woodworking", "japan travel", "space science", "guitar lessons"]})
    s = recommender.algo_settings(1)
    labels = {r["label"]: r for r in interests.rows(1)}
    assert set(labels) == {"sourdough", "pottery"} and labels["pottery"]["manual_weight"] == 1.5
    assert all(r["stats"].get("migrated") for r in labels.values())
    assert s["boosts"] == {"kw:sourdough": 2.0, "kw:pottery": 1.5, "ch:Knife & Fire": 2.0}
    assert s["mutes"] == ["kw:cats", "ch:Whisker Weekly"] and s["forgotten"] == ["kw:bread"]
    assert s["exploration"] == 0.3 and s["half_life_days"] == 20
    assert "home cooking" not in s["starter_topics"] and "woodworking" in s["starter_topics"]
    # stored once: no double counting of the keyword boosts
    raw = db.get_setting("profile:1", "algo")
    assert raw["boosts"] == {"ch:Knife & Fire": 2.0}
    assert interests.migrate(1) is False  # runs once


# ---- profiles -------------------------------------------------------------------------

def test_profiles_are_isolated(fresh_db):
    varied_history(1, others=("Sawdust Sunday", "Six String Lab"))
    yt = FakeYT()
    recommender.refresh(yt, [1], [], [])
    mine = [r["label"] for r in interests.rows(1)]
    feed = [c["id"] for c in recommender.rank(1, "me")]
    # Partner binges cooking, blocks a channel, subscribes to one.
    for v in CH["Knife & Fire"] + CH["Crumb & Crust"]:
        watch(2, v, like=True)
    recommender.set_mute(2, "ch", "Six String Lab", True)
    subscriptions.subscribe(2, mock.CATALOG[0]["channel_id"], "Crumb & Crust")
    interests.add_topic(2, "Pasta")
    recommender.refresh(yt, [2], [], [])
    assert [r["label"] for r in interests.rows(1)] == mine
    assert [c["id"] for c in recommender.rank(1, "me")] == feed
    assert "Six String Lab" in {c["channel"] for c in recommender.rank(1, "me", limit=200)}
    assert not subscriptions.channel_ids(1)
    assert {r["profile_id"] for r in db.q("SELECT profile_id FROM reco_cache WHERE profile_id IN (1,2)")} == {1, 2}
    assert not any("cooking" in r["label"].lower() or "baking" in r["label"].lower() for r in interests.rows(1))


# ---- the shared feed --------------------------------------------------------------------

def test_shared_feed_favours_common_topics_and_excludes_either_veto(fresh_db):
    for v in CH["Orbit Notes"][:3] + CH["Sawdust Sunday"][:3]:
        watch(1, v)
    for v in CH["Orbit Notes"][3:6] + CH["Knife & Fire"][:3]:
        watch(2, v)
    yt = FakeYT()
    recommender.refresh(yt, [1, 2], [], [])
    both = recommender.rank(1, "both")
    assert both
    # The thing you both like comes first and outscores what only one of you likes.
    assert both[0]["channel"] == "Orbit Notes" and both[0]["reasons"][0] == "You both like “Space science”"
    common = [c["score"] for c in both if c["group"].startswith("both:")]
    single = [c["score"] for c in both if c["group"] not in ("explore",) and not c["group"].startswith("both:")]
    assert common and single and min(common) > max(single)
    # Things either of you blocked or said no to stay out.
    knife = next(c for c in recommender.rank(1, "both", limit=200) if c["channel"] == "Knife & Fire")
    library.upsert_video({k: knife.get(k) for k in ("id", "title", "channel", "channel_id", "duration", "url")})
    library.feedback(1, knife["id"], "less")
    nope = next(c for c in both if c["id"] != knife["id"])
    library.not_interested(2, nope)
    recommender.set_mute(2, "ch", "Sawdust Sunday", True)
    ids = {c["id"] for c in recommender.rank(1, "both", limit=200)}
    assert knife["id"] not in ids and nope["id"] not in ids
    assert not [c for c in recommender.rank(1, "both", limit=200) if c["channel"] == "Sawdust Sunday"]
    # ...but they're only out of the shared feed and the vetoing person's own.
    assert "Sawdust Sunday" in {c["channel"] for c in recommender.rank(1, "me", limit=200)}


# ---- freshness ---------------------------------------------------------------------------

def test_freshness_only_for_liked_or_similar_channels(fresh_db):
    varied_history(1)
    ctx = recommender.build_ctx(1)
    v = mock.public(CH["Orbit Notes"][4])
    old = {**v, "keywords": [], "upload_ts": time.time() - 3000 * DAY}
    new = {**v, "keywords": [], "upload_ts": time.time() - 1 * DAY}
    vec = recommender._vectors_for([old])[old["id"]]
    a, b = recommender.score_candidate(old, vec, ctx), recommender.score_candidate(new, vec, ctx)
    assert b["breakdown"]["fresh_part"] > 0 and a["breakdown"]["fresh_part"] == 0
    assert 0 < b["score"] - a["score"] <= recommender.FRESH_BONUS + 1e-6  # small
    # An unknown, unrelated channel gets no freshness lift (age-neutral).
    cat = mock.public(CH["Whisker Weekly"][0])
    cat_new = {**cat, "keywords": [], "upload_ts": time.time() - DAY}
    cvec = recommender._vectors_for([cat_new])[cat_new["id"]]
    assert recommender.score_candidate(cat_new, cvec, ctx)["breakdown"]["fresh_part"] == 0
    # A similar but unknown channel does (its videos look like an interest's channel).
    lookalike = {**new, "channel": "Space Weekly", "channel_id": "UC" + "x" * 22}
    res = recommender.score_candidate(lookalike, vec, ctx, chan_vec=vec)
    assert res["breakdown"]["fresh_part"] > 0 and "like ones you watch" in " ".join(res["reasons"])


# ---- subscriptions -------------------------------------------------------------------------

def test_subscription_refresh_stores_metadata_only(fresh_db, cfg):
    cid = CH["Slow Rail Japan"][0]["channel_id"]
    subscriptions.subscribe(1, cid, "Slow Rail Japan")
    yt = FakeYT()
    st = subscriptions.refresh(yt, [1], thumbs_dir=cfg.thumbs_dir, force=True)
    assert st["checked"] == 1 and st["new"] == 8
    ups = subscriptions.uploads_for(1)[0]["uploads"]
    assert len(ups) == 8 and ups[0]["title"] and ups[0]["upload_ts"] and ups[0]["duration"]
    assert all(u["new"] for u in ups if time.time() - u["upload_ts"] < 7 * DAY)
    # Nothing downloaded: no download jobs, no video files, no library rows.
    assert not db.q("SELECT 1 FROM downloads")
    assert not db.q("SELECT 1 FROM videos WHERE status IN ('ready','queued','downloading')")
    assert not list(cfg.library_dir.glob("*.mp4")) if cfg.library_dir.exists() else True
    assert (cfg.thumbs_dir / f"{ups[0]['id']}.svg").is_file()  # but the thumbnail is kept
    # Not shown to the other profile.
    assert subscriptions.uploads_for(2) == []
    # Seen markers.
    subscriptions.mark_seen(1, [ups[0]["id"]])
    assert not subscriptions.uploads_for(1)[0]["uploads"][0]["new"]
    # A re-check right after is skipped (cached); a failure keeps what we had.
    assert subscriptions.refresh(yt, [1])["skipped"] == 1
    assert subscriptions.refresh(FakeYT(fail=True), [1], force=True)["failed"] == 1
    assert len(subscriptions.uploads_for(1)[0]["uploads"]) == 8


def test_new_uploads_get_guaranteed_feed_slots(fresh_db):
    varied_history(1)
    cid = CH["Whisker Weekly"][0]["channel_id"]  # a channel unrelated to any interest
    subscriptions.subscribe(1, cid, "Whisker Weekly")
    recommender.refresh(FakeYT(), [1], [], [])
    feed = recommender.rank(1, "me")
    subs = [c for c in feed if c.get("slot") == "subscription"]
    assert subs and all(c["channel"] == "Whisker Weekly" and c["new_upload"] for c in subs)
    assert len(subs) <= recommender.SUB_SLOTS
    assert len(feed) - len(subs) == recommender.FEED_SIZE  # on top of the normal feed
    assert feed[0].get("slot") == "subscription"
    # Not in the other profile's feed.
    assert not [c for c in recommender.rank(2, "me") if c.get("slot") == "subscription"]
    # Subscribing is a strong positive: there's a subscribe event for the channel.
    assert db.q1("SELECT 1 FROM events WHERE profile_id=1 AND type='subscribe' AND channel='Whisker Weekly'")
    subscriptions.unsubscribe(1, cid)
    assert not [c for c in recommender.rank(1, "me") if c.get("slot") == "subscription"]


# ---- robustness -----------------------------------------------------------------------------

def test_offline_refresh_keeps_the_last_good_feed(fresh_db):
    varied_history(1)
    recommender.refresh(FakeYT(), [1], [], [])
    feed = [c["id"] for c in recommender.rank(1, "me")]
    res = recommender.refresh(FakeYT(fail=True), [1], [], [(1, "me")])
    assert res["fetched"] == 0
    assert [c["id"] for c in recommender.rank(1, "me")] == feed


def test_budget_paces_requests():
    b = recommender.Budget(3, delay=0.05)
    t = time.time()
    assert all(b.take() for _ in range(3)) and not b.take()
    assert time.time() - t >= 0.07 and b.used == 3


def test_search_cache(fresh_db):
    yt = FakeYT()
    since = time.time() - 60
    recommender.cached_search(yt, "sourdough", 5, False, None, since)
    recommender.cached_search(yt, "Sourdough", 5, False, None, since)
    assert len(yt.calls) == 1


def test_minilm_path_with_a_stub(fresh_db, monkeypatch):
    """The fastembed code path, with a stand-in model (the real one needs a download)."""
    class Stub:
        def embed(self, texts, batch_size=32):
            for t in texts:
                yield [1.0 if "space" in t.lower() else 0.0, 1.0, 0.0]

    embeddings.configure("minilm")
    monkeypatch.setattr(embeddings, "_minilm", Stub())
    assert embeddings.model_name() == embeddings.MINILM and embeddings.ready()
    a = embeddings.embed_text("space rockets")
    b = embeddings.embed_text("Space telescopes")
    c = embeddings.embed_text("bread")
    assert embeddings.dot(a, b) > embeddings.dot(a, c)
    assert db.q1("SELECT COUNT(*) n FROM embeddings WHERE model=?", (embeddings.MINILM,))["n"] == 3
    monkeypatch.setattr(embeddings, "_minilm", None)
    monkeypatch.setattr(embeddings, "_fastembed_ok", False)
    assert embeddings.model_name() == embeddings.BUILTIN  # not installed: built-in fallback


# ---- HTTP API ---------------------------------------------------------------------------------

P1, P2 = {"X-Profile": "1"}, {"X-Profile": "2"}


def test_interest_editor_api(client):
    r = client.post("/api/interests", json={"label": "Space science"}, headers=P1).json()
    assert r["kind"] == "manual" and r["weight"] == interests.MANUAL_DEFAULT
    iid = r["id"]
    assert client.patch(f"/api/interests/{iid}", json={"weight": 3.5, "pinned": True}, headers=P1).json()["pinned"]
    assert client.patch(f"/api/interests/{iid}", json={"weight": 9}, headers=P1).status_code == 422
    assert client.patch(f"/api/interests/{iid}", json={"label": "Astronomy"}, headers=P2).status_code == 404
    st = client.get("/api/interests", headers=P1).json()
    assert [i["label"] for i in st["interests"]] == ["Space science"] and st["embeddings"]["model"]
    assert client.get("/api/interests", headers=P2).json()["interests"] == []
    a = client.get("/api/algorithm", headers=P1).json()
    assert a["settings"]["boosts"]["kw:space science"] == 3.5 and a["interests"]
    client.post("/api/algorithm/mute", json={"kind": "kw", "term": "cooking"}, headers=P1)
    assert client.get("/api/interests", headers=P1).json()["blocked"]["topics"] == ["cooking"]
    v = mock.public(mock.CATALOG[3])
    assert client.post("/api/candidates/not-interested", json={"video": v}, headers=P1).json()["ok"]
    assert [n["video_id"] for n in client.get("/api/interests", headers=P1).json()["not_interested"]] == [v["id"]]
    client.delete(f"/api/not-interested/{v['id']}", headers=P1)
    assert client.get("/api/interests", headers=P1).json()["not_interested"] == []
    assert client.delete(f"/api/interests/{iid}", headers=P1).json()["ok"]


def test_feedback_and_subscription_api(client):
    v = mock.public(mock.CATALOG[12])
    library.upsert_video(v)
    assert client.post(f"/api/videos/{v['id']}/feedback", json={"value": "more"}, headers=P1).json()["feedback"] == "more"
    assert client.get(f"/api/videos/{v['id']}", headers=P1).json()["feedback"] == "more"
    assert client.get(f"/api/videos/{v['id']}", headers=P2).json()["feedback"] is None
    assert client.post(f"/api/videos/{v['id']}/feedback", json={"value": "meh"}, headers=P1).status_code == 422
    # subscribe by channel id, or via a video
    r = client.post("/api/subscriptions", json={"video_id": v["id"]}, headers=P1).json()
    assert r["subscribed"] and r["channel_id"] == v["channel_id"]
    subs = client.get("/api/subscriptions", headers=P1).json()
    assert subs["channels"][0]["uploads"] and subs["channels"][0]["channel"] == v["channel"]
    assert client.get(f"/api/videos/{v['id']}", headers=P1).json()["subscribed"]
    assert client.get("/api/subscriptions", headers=P2).json()["channels"] == []
    ids = [u["id"] for u in subs["channels"][0]["uploads"]]
    client.post("/api/subscriptions/seen", json={"ids": ids}, headers=P1)
    assert client.get("/api/subscriptions/new-count", headers=P1).json()["new_count"] == 0
    ch = client.get(f"/api/channels/{v['channel_id']}", headers=P2).json()
    assert ch["videos"] and not ch["subscribed"]
    assert client.delete(f"/api/subscriptions/{v['channel_id']}", headers=P1).json()["subscribed"] is False
    assert client.delete(f"/api/subscriptions/{v['channel_id']}", headers=P1).status_code == 404
    assert client.post("/api/subscriptions", json={"channel_id": "nope"}, headers=P1).status_code == 400


def test_reload_runs_in_the_background(client, monkeypatch):
    monkeypatch.setattr(recommender, "REFRESH_WAIT", 0)
    r = client.post("/api/foryou/refresh", headers=P1).json()
    assert "done" in r
    for _ in range(200):
        if not client.get("/api/foryou/status").json()["running"]:
            break
        time.sleep(0.05)
    assert not client.get("/api/foryou/status").json()["running"]
    assert client.get("/api/foryou", headers=P1).json()["items"]
