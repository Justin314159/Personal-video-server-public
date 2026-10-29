import json
import math

import pytest

from homelib import db, library, recommender
from homelib.db import now

DAY = 86400


def ev(etype, kws, channel="", age_days=0, pid=1):
    db.x("INSERT INTO events(profile_id, type, weight, keywords, channel, ts) VALUES(?,?,?,?,?,?)",
         (pid, etype, library.EVENT_WEIGHTS[etype], json.dumps(kws), channel, now() - age_days * DAY))


def test_event_weights_match_spec():
    assert library.EVENT_WEIGHTS == {"finish": 1.0, "like": 1.5, "dislike": -2.0,
                                     "delete_unwatched": -1.0, "skip": -1.0, "search": 0.2}


def test_decay_half_life():
    assert recommender.decay(0, 30) == 1.0
    assert recommender.decay(30 * DAY, 30) == pytest.approx(0.5)
    assert recommender.decay(60 * DAY, 30) == pytest.approx(0.25)
    assert recommender.decay(7 * DAY, 7) == pytest.approx(0.5)


def test_profile_weights_and_decay(fresh_db):
    ev("finish", ["sourdough", "bread"], "Crumb", age_days=0)
    ev("like", ["sourdough"], "Crumb", age_days=30)
    ev("dislike", ["cats"], "Whiskers")
    ev("search", ["guitar"])
    p = recommender.build_profile(1, recommender.algo_settings(1))
    # finish: 1/sqrt(2) ; like 30 days ago with 30-day half-life: 1.5 * 0.5
    assert p["kw"]["sourdough"]["weight"] == pytest.approx(1 / math.sqrt(2) + 0.75, abs=1e-3)
    assert p["kw"]["bread"]["weight"] == pytest.approx(1 / math.sqrt(2), abs=1e-3)
    assert p["ch"]["Crumb"]["weight"] == pytest.approx(1 + 0.75, abs=1e-3)
    assert p["kw"]["cats"]["weight"] == pytest.approx(-2.0)
    assert p["kw"]["guitar"]["weight"] == pytest.approx(0.2)
    assert "Whiskers" in p["ch"] and "" not in p["ch"]  # searches carry no channel
    assert p["kw"]["sourdough"]["counts"] == {"finish": 1, "like": 1}


def test_half_life_slider_changes_decay(fresh_db):
    ev("finish", ["sourdough"], age_days=14)
    short = recommender.build_profile(1, {**recommender.algo_settings(1), "half_life_days": 7})
    long = recommender.build_profile(1, {**recommender.algo_settings(1), "half_life_days": 90})
    assert short["kw"]["sourdough"]["weight"] == pytest.approx(0.25, abs=1e-3)
    assert long["kw"]["sourdough"]["weight"] > 0.85


def cand(vid, title, channel):
    return {"id": vid, "title": title, "channel": channel, "keywords": library.video_keywords(title)}


def test_scoring_prefers_familiar_and_explains(fresh_db):
    for _ in range(3):
        ev("finish", ["sourdough", "bread"], "Crumb")
    s = recommender.algo_settings(1)
    s["exploration"] = 0.0
    prof = recommender.build_profile(1, s)
    a = recommender.score(cand("a", "Sourdough bread basics", "Crumb"), prof, s)
    b = recommender.score(cand("b", "Rocket engines explained", "Orbit"), prof, s)
    assert a["score"] > b["score"]
    assert any("sourdough" in r and "finished 3" in r for r in a["reasons"])
    assert any(r.startswith("From Crumb") for r in a["reasons"])


def test_exploration_slider_favours_new_topics(fresh_db):
    for _ in range(3):
        ev("finish", ["sourdough"], "Crumb")
    s = recommender.algo_settings(1)
    s["exploration"] = 1.0
    prof = recommender.build_profile(1, s)
    fam = recommender.score(cand("a", "Sourdough", "Crumb"), prof, s)
    new = recommender.score(cand("b", "Rocket engines", "Orbit"), prof, s)
    assert new["breakdown"]["novelty_part"] > fam["breakdown"]["novelty_part"]
    assert new["reasons"][0].startswith("Exploration pick")


def test_channel_loyalty(fresh_db):
    ev("like", ["woodworking"], "Sawdust")
    s = recommender.algo_settings(1)
    s.update(exploration=0.0)
    prof = recommender.build_profile(1, s)
    same_channel_new_topic = cand("a", "Pottery wheel basics", "Sawdust")
    s["channel_loyalty"] = 0.0
    assert recommender.score(same_channel_new_topic, prof, s)["breakdown"]["familiar"] == 0
    s["channel_loyalty"] = 1.0
    assert recommender.score(same_channel_new_topic, prof, s)["breakdown"]["familiar"] > 0


def test_boost_mute_forget(fresh_db):
    ev("finish", ["sourdough"], "Crumb")
    recommender.set_boost(1, "kw", "Pottery", 2.0)
    s = recommender.algo_settings(1)
    prof = recommender.build_profile(1, s)
    assert prof["kw"]["pottery"]["boost"] == 2.0
    r = recommender.score(cand("p", "Pottery for beginners", "Clay"), prof, s)
    assert any("boosted" in x for x in r["reasons"])

    recommender.set_mute(1, "ch", "Clay", True)
    s = recommender.algo_settings(1)
    assert recommender.score(cand("p", "Pottery for beginners", "Clay"), prof, s) is None

    recommender.set_forgotten(1, "kw", "sourdough", True)
    prof = recommender.build_profile(1, recommender.algo_settings(1))
    assert "sourdough" not in prof["kw"]


def test_blend_both_of_us(fresh_db):
    ev("finish", ["sourdough"], "Crumb", pid=1)
    ev("finish", ["guitar"], "Six", pid=2)
    p1 = recommender.build_profile(1)
    p2 = recommender.build_profile(2)
    both = recommender.blend_profiles([p1, p2])
    assert both["kw"]["sourdough"]["weight"] == pytest.approx(0.5)
    assert both["kw"]["guitar"]["weight"] == pytest.approx(0.5)


def test_rank_uses_cache_and_excludes(fresh_db):
    from homelib import mock

    class FakeYT:
        def search(self, q, n, shorts=False):
            return mock.search(q, n, shorts_only=shorts)

    ev("finish", ["sourdough", "focaccia"], "Crumb & Crust")
    assert recommender.fetch_candidates(FakeYT(), 1) > 0
    ranked = recommender.rank(1, "me")
    assert ranked and all(c["reasons"] for c in ranked)
    assert ranked[0]["channel"] == "Crumb & Crust"
    # a disliked candidate disappears
    library.rate_candidate(1, ranked[0], -1)
    assert ranked[0]["id"] not in {c["id"] for c in recommender.rank(1, "me")}
    both = recommender.rank(1, "both")
    assert both and "you both" in " ".join(both[0]["reasons"]) + " you both"


def test_export_and_reset(fresh_db):
    ev("finish", ["sourdough"], "Crumb")
    recommender.set_boost(1, "kw", "bread", 1)
    data = recommender.export(1)
    assert data["format"] == "homelib-algorithm-v1"
    assert "sourdough" in data["keywords"] and data["events"]
    json.dumps(data)
    recommender.reset(1)
    assert recommender.export(1)["events"] == []
    assert recommender.algo_settings(1)["boosts"] == {}
