"""The "For you" recommender. Pure Python + SQLite, fully inspectable.

1. Taste profile. Every event (finish +1, like +1.5, dislike -2,
   delete-unwatched -1, skip<2s -1, search +0.2) adds its weight to the
   video's keywords and channel, multiplied by a time decay
   0.5 ** (age_days / half_life). Each keyword gets weight / sqrt(#keywords)
   so long titles don't dominate. Users can boost, mute or forget any term.

2. Scoring a candidate:
      topic    = sum(keyword weights) / sqrt(#keywords)
      channel  = channel weight
      familiar = (1 - loyalty) * topic + loyalty * channel
      score    = (1 - explore) * tanh(familiar / 3)
                 + explore * novelty            (share of unknown keywords)
                 + explore * 0.3 * daily_jitter (stable for a day)
   Muted keywords/channels, disliked and already downloaded videos are excluded.

4. Reloading the feed (the button, or the nightly run) sets aside everything
   the feed was showing (for 30 days) and swaps the candidate pool for a
   fresh batch, so a reload always brings new suggestions.

3. Every score comes with human-readable reasons and a numeric breakdown.

Optional sentence-transformers embeddings add a 'semantic' term; they are off
by default and not recommended on a 4 GB machine (see docs/DECISIONS.md).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections import defaultdict

from . import db, library, text
from .db import now

log = logging.getLogger("homelib.recommender")

DEFAULT_ALGO = {
    "half_life_days": 30,     # slider 7..90
    "exploration": 0.2,       # slider 0..1 (0 = only familiar, 1 = mostly new)
    "channel_loyalty": 0.4,   # slider 0..1 (0 = topics only, 1 = channels only)
    "boosts": {},             # {"kw:sourdough": 2.0, "ch:Knife & Fire": 2.0}
    "mutes": [],              # ["kw:cats", "ch:Some Channel"]
    "forgotten": [],          # terms the profile ignores entirely
    "starter_topics": ["home cooking", "woodworking", "japan travel", "space science", "guitar lessons"],
    "include_shorts_in_feed": False,
}
BOOST_DEFAULT = 2.0
EMBEDDINGS_ENABLED = False  # set from config by app.create_app; see embeddings.py
SEEN_DAYS = 30             # how long a reloaded-away suggestion stays hidden
RELOAD_PER_QUERY = 20     # results per search on a reload; each reload digs 10 deeper, up to 50
_reload_lock = threading.Lock()
EVENT_LABELS = {"finish": "finished", "like": "liked", "dislike": "disliked",
                "delete_unwatched": "deleted unwatched", "skip": "skipped", "search": "searched"}


def algo_settings(profile_id: int) -> dict:
    s = db.get_setting(f"profile:{profile_id}", "algo", {}) or {}
    out = json.loads(json.dumps(DEFAULT_ALGO))
    out.update(s)
    return out


def save_algo_settings(profile_id: int, patch: dict) -> dict:
    cur = algo_settings(profile_id)
    if "half_life_days" in patch:
        cur["half_life_days"] = max(7, min(90, int(patch["half_life_days"])))
    for k in ("exploration", "channel_loyalty"):
        if k in patch:
            cur[k] = max(0.0, min(1.0, float(patch[k])))
    if "starter_topics" in patch:
        cur["starter_topics"] = [str(t).strip()[:60] for t in patch["starter_topics"] if str(t).strip()][:20]
    if "include_shorts_in_feed" in patch:
        cur["include_shorts_in_feed"] = bool(patch["include_shorts_in_feed"])
    for k in ("boosts", "mutes", "forgotten"):
        if k in patch:
            cur[k] = patch[k]
    db.set_setting(f"profile:{profile_id}", "algo", cur)
    return cur


def term_key(kind: str, term: str) -> str:
    if kind not in ("kw", "ch"):
        raise ValueError("kind must be 'kw' (keyword) or 'ch' (channel)")
    term = term.strip()
    return f"{kind}:{term.lower() if kind == 'kw' else term}"


def set_boost(profile_id: int, kind: str, term: str, amount: float | None) -> dict:
    a = algo_settings(profile_id)
    key = term_key(kind, term)
    if amount:
        a["boosts"][key] = max(-5.0, min(5.0, float(amount)))
        a["mutes"] = [m for m in a["mutes"] if m != key]
    else:
        a["boosts"].pop(key, None)
    return save_algo_settings(profile_id, a)


def set_mute(profile_id: int, kind: str, term: str, muted: bool) -> dict:
    a = algo_settings(profile_id)
    key = term_key(kind, term)
    a["mutes"] = [m for m in a["mutes"] if m != key] + ([key] if muted else [])
    if muted:
        a["boosts"].pop(key, None)
    return save_algo_settings(profile_id, a)


def set_forgotten(profile_id: int, kind: str, term: str, forgotten: bool) -> dict:
    a = algo_settings(profile_id)
    key = term_key(kind, term)
    a["forgotten"] = [m for m in a["forgotten"] if m != key] + ([key] if forgotten else [])
    return save_algo_settings(profile_id, a)


# ---- taste profile ------------------------------------------------------------

def decay(age_seconds: float, half_life_days: float) -> float:
    return 0.5 ** (max(0.0, age_seconds) / 86400.0 / max(half_life_days, 0.01))


def build_profile(profile_id: int, settings: dict | None = None, now_ts: float | None = None,
                  events: list[dict] | None = None) -> dict:
    """Return {'kw': {term: entry}, 'ch': {name: entry}} where
    entry = {'weight': float, 'learned': float, 'boost': float, 'counts': {type: n}}."""
    s = settings or algo_settings(profile_id)
    t = now_ts or now()
    if events is None:
        events = db.q("SELECT type, weight, keywords, channel, ts FROM events WHERE profile_id=? "
                      "AND type<>'deleted'", (profile_id,))
    forgotten = set(s.get("forgotten", []))
    terms: dict[str, dict] = defaultdict(lambda: {"learned": 0.0, "counts": defaultdict(int)})
    for e in events:
        if not e.get("weight"):
            continue
        d = decay(t - e["ts"], s["half_life_days"])
        kws = e["keywords"] if isinstance(e["keywords"], list) else json.loads(e["keywords"] or "[]")
        if kws:
            share = e["weight"] * d / math.sqrt(len(kws))
            for k in kws:
                ent = terms[f"kw:{k}"]
                ent["learned"] += share
                ent["counts"][e["type"]] += 1
        if e.get("channel"):
            ent = terms[f"ch:{e['channel']}"]
            ent["learned"] += e["weight"] * d
            ent["counts"][e["type"]] += 1
    for key, b in s.get("boosts", {}).items():
        terms[key]  # make sure boosted-but-never-seen terms exist
    out: dict[str, dict] = {"kw": {}, "ch": {}}
    for key, ent in terms.items():
        if key in forgotten:
            continue
        kind, name = key.split(":", 1)
        boost = float(s.get("boosts", {}).get(key, 0.0))
        out[kind][name] = {"weight": round(ent["learned"] + boost, 4), "learned": round(ent["learned"], 4),
                           "boost": boost, "counts": dict(ent["counts"]),
                           "muted": key in s.get("mutes", [])}
    return out


def blend_profiles(profiles: list[dict]) -> dict:
    """'Both of us': average of the individual profiles (missing terms count as 0)."""
    out: dict[str, dict] = {"kw": {}, "ch": {}}
    n = max(1, len(profiles))
    for kind in ("kw", "ch"):
        names = set().union(*(p[kind].keys() for p in profiles)) if profiles else set()
        for name in names:
            ents = [p[kind].get(name) for p in profiles]
            counts: dict[str, int] = defaultdict(int)
            for e in ents:
                for k, c in (e or {}).get("counts", {}).items():
                    counts[k] += c
            out[kind][name] = {
                "weight": round(sum((e or {}).get("weight", 0) for e in ents) / n, 4),
                "learned": round(sum((e or {}).get("learned", 0) for e in ents) / n, 4),
                "boost": round(sum((e or {}).get("boost", 0) for e in ents) / n, 4),
                "counts": dict(counts),
                "muted": any((e or {}).get("muted") for e in ents),
            }
    return out


def blend_settings(settings: list[dict]) -> dict:
    s = json.loads(json.dumps(DEFAULT_ALGO))
    n = max(1, len(settings))
    for k in ("half_life_days", "exploration", "channel_loyalty"):
        s[k] = sum(x[k] for x in settings) / n
    s["mutes"] = sorted(set().union(*(set(x["mutes"]) for x in settings)))
    return s


# ---- scoring ------------------------------------------------------------------

def _jitter(video_id: str, day: str) -> float:
    h = hashlib.sha256(f"{day}:{video_id}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2 ** 32


def _counts_phrase(counts: dict) -> str:
    parts = [f"{EVENT_LABELS.get(k, k)} {n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])
             if k in EVENT_LABELS]
    return ", ".join(parts[:3])


def score(candidate: dict, profile: dict, settings: dict, now_ts: float | None = None,
          who: str = "you") -> dict | None:
    """Score one candidate. Returns None if it is excluded (muted)."""
    mutes = set(settings.get("mutes", []))
    kws = candidate.get("keywords") or text.keywords(candidate.get("title", ""))
    channel = candidate.get("channel", "")
    if f"ch:{channel}" in mutes:
        return None
    if any(f"kw:{k}" in mutes for k in kws):
        return None
    contrib = []
    for k in kws:
        ent = profile["kw"].get(k)
        if ent and ent["weight"]:
            contrib.append((k, ent["weight"] / math.sqrt(max(1, len(kws))), ent))
    topic = sum(c[1] for c in contrib)
    ch_ent = profile["ch"].get(channel)
    ch_w = ch_ent["weight"] if ch_ent else 0.0
    loyalty = settings["channel_loyalty"]
    familiar = (1 - loyalty) * topic + loyalty * ch_w
    known = sum(1 for k in kws if k in profile["kw"] and abs(profile["kw"][k]["weight"]) >= 0.05)
    novelty = (1 - known / len(kws)) if kws else 0.0
    if ch_ent and abs(ch_w) >= 0.05:
        novelty *= 0.5  # a familiar channel is only half "new"
    explore = settings["exploration"]
    day = time.strftime("%Y-%m-%d", time.localtime(now_ts or now()))
    jit = _jitter(candidate["id"], day)
    fam_part = (1 - explore) * math.tanh(familiar / 3.0)
    nov_part = explore * novelty
    jit_part = explore * 0.3 * jit
    total = fam_part + nov_part + jit_part

    reasons = []
    for k, w, ent in sorted(contrib, key=lambda c: -c[1]):
        if w <= 0 or len(reasons) >= 2:
            continue
        if ent["boost"] > 0 and ent["learned"] <= 0.05:
            reasons.append(f"You boosted the topic “{k}”")
        else:
            phrase = _counts_phrase(ent["counts"])
            extra = " (boosted)" if ent["boost"] > 0 else ""
            reasons.append(f"Topic “{k}”{extra}: {who} {phrase}" if phrase else f"Topic “{k}”{extra}")
    if ch_ent and ch_w > 0.05 and loyalty > 0:
        phrase = _counts_phrase(ch_ent["counts"])
        reasons.append(f"From {channel}" + (f": {who} {phrase}" if phrase else "")
                       + (" (boosted)" if ch_ent["boost"] > 0 else ""))
    negatives = [(k, w) for k, w, _ in contrib if w < 0]
    if negatives:
        k, _ = min(negatives, key=lambda c: c[1])
        reasons.append(f"Down-weighted: “{k}” ({_counts_phrase(profile['kw'][k]['counts']) or 'muted'})")
    if nov_part > fam_part and nov_part > 0.05:
        reasons.insert(0, "Exploration pick: mostly new topics for " + ("you" if who == "you" else "you both"))
    if not reasons:
        reasons.append("Starter topic" if candidate.get("source", "").startswith("starter") else "Popular match")
    return {
        "score": round(total, 4),
        "reasons": reasons,
        "breakdown": {"topic": round(topic, 3), "channel": round(ch_w, 3), "familiar": round(familiar, 3),
                      "novelty": round(novelty, 3), "familiar_part": round(fam_part, 3),
                      "novelty_part": round(nov_part, 3), "jitter_part": round(jit_part, 3)},
    }


def excluded_video_ids(profile_ids: list[int]) -> set[str]:
    """Things not worth recommending: already in the library, watched, or disliked."""
    ids = {r["id"] for r in db.q("SELECT id FROM videos WHERE status IN ('ready','downloading','queued')")}
    if profile_ids:
        ph = ",".join("?" * len(profile_ids))
        ids |= {r["video_id"] for r in db.q(
            f"SELECT video_id FROM watch_state WHERE profile_id IN ({ph}) AND (watched=1 OR rating<0)", profile_ids)}
    return ids


def rank(profile_id: int, mode: str = "me", limit: int = 40, shorts: bool | None = False,
         profile_names: dict[int, str] | None = None) -> list[dict]:
    """Score the cached candidate pool. mode='both' blends both profiles."""
    pids = library.profile_ids()
    if mode == "both":
        sets = [algo_settings(p) for p in pids]
        prof = blend_profiles([build_profile(p, s) for p, s in zip(pids, sets)])
        settings = blend_settings(sets)
        who = "you both"
        pool_ids = pids
        excluded = excluded_video_ids(pids) | seen_ids(0)
        cache_pid = 0
    else:
        settings = algo_settings(profile_id)
        prof = build_profile(profile_id, settings)
        who = "you"
        pool_ids = [profile_id]
        excluded = excluded_video_ids([profile_id]) | seen_ids(profile_id)
        cache_pid = profile_id
    ph = ",".join("?" * len(pool_ids))
    rows = db.q(f"SELECT video_id, meta, source FROM reco_cache WHERE profile_id IN ({ph})", pool_ids)
    seen: set[str] = set()
    out = []
    for r in rows:
        if r["video_id"] in seen or r["video_id"] in excluded:
            continue
        seen.add(r["video_id"])
        meta = json.loads(r["meta"])
        if shorts is not None and bool(meta.get("is_short")) != shorts:
            continue
        meta["source"] = r["source"]
        sc = score(meta, prof, settings, who=who)
        if sc is None:
            continue
        out.append({**meta, **sc, "thumb": f"/api/thumb/{meta['id']}"})
    if EMBEDDINGS_ENABLED and out:
        _add_semantic(out, pool_ids, settings)
    out.sort(key=lambda c: (-c["score"], c["id"]))
    out = out[:limit]
    # Cache the scored result (used by "Why am I seeing this?" and debugging).
    t = now()
    with db.tx():
        for c in out:
            db.x("INSERT INTO reco_cache(profile_id, video_id, meta, source, score, reasons, fetched_at, scored_at) "
                 "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(profile_id, video_id) DO UPDATE SET score=excluded.score, "
                 "reasons=excluded.reasons, scored_at=excluded.scored_at",
                 (cache_pid, c["id"], json.dumps(_meta_only(c)), c.get("source", ""), c["score"],
                  json.dumps({"reasons": c["reasons"], "breakdown": c["breakdown"]}), t, t))
    return out


# ---- reloading the feed -------------------------------------------------------------

def _shown_setting(profile_id: int, mode: str) -> tuple[str, str]:
    # "Both of us" is one feed for the two of you, so it is remembered once.
    return ("global", "foryou_shown_both") if mode == "both" else (f"profile:{profile_id}", "foryou_shown")


def remember_shown(profile_id: int, mode: str, video_ids: list[str]) -> None:
    """What the For you feed is currently showing (set aside on the next reload)."""
    db.set_setting(*_shown_setting(profile_id, mode), list(video_ids))


def seen_ids(bucket: int) -> set[str]:
    return {r["video_id"] for r in db.q("SELECT video_id FROM reco_seen WHERE profile_id=? AND ts>=?",
                                        (bucket, now() - SEEN_DAYS * 86400))}


def set_aside_shown(profile_id: int, mode: str) -> int:
    """Hide the suggestions the feed was showing, so a reload brings new ones."""
    scope, key = _shown_setting(profile_id, mode)
    ids = db.get_setting(scope, key, []) or []
    bucket = 0 if mode == "both" else profile_id
    t = now()
    with db.tx():
        for vid in ids:
            db.x("INSERT INTO reco_seen(profile_id, video_id, ts) VALUES(?,?,?) "
                 "ON CONFLICT(profile_id, video_id) DO UPDATE SET ts=excluded.ts", (bucket, vid, t))
        db.x("DELETE FROM reco_seen WHERE ts < ?", (t - SEEN_DAYS * 86400,))
    db.set_setting(scope, key, [])
    return len(ids)


def reload_feed(yt, profile_id: int, mode: str = "me") -> dict:
    """The reload button: set aside what you were shown and fetch a fresh batch.
    'both' refreshes both profiles' candidates."""
    pids = library.profile_ids() if mode == "both" else [profile_id]
    with _reload_lock:
        set_aside_shown(profile_id, mode)
        fetched = sum(fetch_candidates(yt, p, fresh=True) for p in pids)
    return {"fetched": fetched, "suggestions": len(rank(profile_id, mode))}


def nightly_reload(yt) -> None:
    """The 03:30 run: the same full reload for every profile and for "Both of us"."""
    pids = library.profile_ids()
    with _reload_lock:
        for p in pids:
            set_aside_shown(p, "me")
        set_aside_shown(pids[0], "both")
        for p in pids:
            try:
                fetch_candidates(yt, p, fresh=True)
            except Exception:
                log.exception("candidate fetch failed for profile %s", p)


def _add_semantic(cands: list[dict], pids: list[int], settings: dict) -> None:
    from . import embeddings

    ph = ",".join("?" * len(pids))
    liked = [json.loads(r["data"]).get("title", "") for r in db.q(
        f"SELECT data FROM events WHERE profile_id IN ({ph}) AND type IN ('finish','like') "
        "ORDER BY ts DESC LIMIT 50", pids)]
    sims = embeddings.similarity([t for t in liked if t], [c.get("title", "") for c in cands])
    for c, sim in zip(cands, sims):
        part = round(0.3 * (1 - settings["exploration"]) * sim, 4)
        c["score"] = round(c["score"] + part, 4)
        c["breakdown"]["semantic_part"] = part
        if part > 0.15:
            c["reasons"].append("Similar to titles " + ("you" if len(pids) == 1 else "you both") + " enjoyed")


def _meta_only(c: dict) -> dict:
    return {k: c.get(k) for k in ("id", "title", "channel", "channel_id", "duration", "url", "thumb_url",
                                  "keywords", "is_short", "view_count")}


def explain(profile_id: int, video_id: str, mode: str = "me") -> dict:
    pids = library.profile_ids() if mode == "both" else [profile_id]
    r = db.q1(f"SELECT meta, source FROM reco_cache WHERE video_id=? AND profile_id IN "
              f"({','.join('?' * len(pids))},0)", (video_id, *pids))
    if r:
        meta = json.loads(r["meta"])
        meta["source"] = r["source"]
    else:
        meta = library.get_video(video_id)
    if mode == "both":
        sets = [algo_settings(p) for p in pids]
        prof = blend_profiles([build_profile(p, s) for p, s in zip(pids, sets)])
        settings, who = blend_settings(sets), "you both"
    else:
        settings = algo_settings(profile_id)
        prof, who = build_profile(profile_id, settings), "you"
    kws = meta.get("keywords") or text.keywords(meta.get("title", ""))
    sc = score({**meta, "keywords": kws}, prof, settings, who=who)
    terms = [{"kind": "kw", "term": k, **(prof["kw"].get(k) or {"weight": 0, "counts": {}})} for k in kws]
    ch = meta.get("channel", "")
    terms.append({"kind": "ch", "term": ch, **(prof["ch"].get(ch) or {"weight": 0, "counts": {}})})
    return {"video": _meta_only({**meta, "keywords": kws}), "result": sc, "terms": terms,
            "settings": {k: settings[k] for k in ("half_life_days", "exploration", "channel_loyalty")},
            "source": meta.get("source", "")}


# ---- candidate fetching ---------------------------------------------------------

def candidate_queries(profile_id: int, max_queries: int = 6, rotation: int = 0) -> list[tuple[str, str, bool]]:
    """(query, source label, shorts?) to run for the nightly candidate fetch.
    rotation shifts which long-tail and starter topics are picked, so each
    reload searches for something a bit different."""
    s = algo_settings(profile_id)
    prof = build_profile(profile_id, s)
    mutes = set(s["mutes"])
    kws = [(k, e["weight"]) for k, e in prof["kw"].items() if e["weight"] > 0 and f"kw:{k}" not in mutes]
    chs = [(c, e["weight"]) for c, e in prof["ch"].items() if e["weight"] > 0 and f"ch:{c}" not in mutes]
    kws.sort(key=lambda t: -t[1])
    chs.sort(key=lambda t: -t[1])
    qs: list[tuple[str, str, bool]] = []
    top = [k for k, _ in kws[:6]]
    if rotation and top:  # pair the favourite topics up differently on each reload
        r = rotation % len(top)
        top = top[r:] + top[:r]
    for i in range(0, min(len(top), 4), 2):
        pair = " ".join(top[i:i + 2])
        qs.append((pair, f"topic:{pair}", False))
    for c, _ in chs[:2]:
        qs.append((c, f"channel:{c}", False))
    # Exploration: the long tail of weaker interests plus the starter topics.
    tail = [k for k, _ in kws[6:14]]
    starters = list(s["starter_topics"])
    day = int(now() // 86400) + rotation
    if tail:
        qs.append((tail[day % len(tail)], "explore:" + tail[day % len(tail)], False))
    for i in range(max(1, max_queries - len(qs))):
        if starters:
            t = starters[(day + i) % len(starters)]
            qs.append((t, f"starter:{t}", False))
    # Shorts candidates for the Shorts feed.
    shorts_seed = top[0] if top else (starters[day % len(starters)] if starters else "funny")
    qs.append((shorts_seed, f"shorts:{shorts_seed}", True))
    seen, uniq = set(), []
    for q in qs:
        if q[0].lower() + str(q[2]) not in seen:
            seen.add(q[0].lower() + str(q[2]))
            uniq.append(q)
    return uniq[: max_queries + 1]


def fetch_candidates(yt, profile_id: int, per_query: int = 10, fresh: bool = False) -> int:
    """Run the candidate searches and store results in reco_cache.

    fresh=True (a reload) digs deeper into the results, rotates the topics
    and replaces the profile's pool with what came back."""
    n = 0
    t = now()
    rotation = 0
    if fresh:
        rotation = (db.get_setting(f"profile:{profile_id}", "reload_count", 0) or 0) + 1
        db.set_setting(f"profile:{profile_id}", "reload_count", rotation)
        per_query = max(per_query, RELOAD_PER_QUERY + 10 * (rotation % 4))
    for query, source, shorts in candidate_queries(profile_id, rotation=rotation):
        try:
            results = yt.search(query, per_query, shorts=shorts)
        except Exception as e:
            log.warning("candidate search %r failed: %s", query, e)
            continue
        with db.tx():
            for r in results:
                r = dict(r)
                r["keywords"] = text.keywords(r.get("title", ""))
                db.x("INSERT INTO reco_cache(profile_id, video_id, meta, source, fetched_at) VALUES(?,?,?,?,?) "
                     "ON CONFLICT(profile_id, video_id) DO UPDATE SET meta=excluded.meta, source=excluded.source, "
                     "fetched_at=excluded.fetched_at", (profile_id, r["id"], json.dumps(r), source, t))
                n += 1
    # Forget candidates that weren't refreshed for a week, or, on a reload,
    # everything from before it (unless every search failed, e.g. offline).
    cutoff = t if (fresh and n) else t - 7 * 86400
    db.x("DELETE FROM reco_cache WHERE profile_id=? AND fetched_at < ?", (profile_id, cutoff))
    log.info("fetched %d candidates for profile %d", n, profile_id)
    return n


# ---- transparency -----------------------------------------------------------------

def export(profile_id: int) -> dict:
    s = algo_settings(profile_id)
    prof = build_profile(profile_id, s)
    events = db.q("SELECT id, video_id, type, weight, keywords, channel, data, ts FROM events "
                  "WHERE profile_id=? ORDER BY ts DESC", (profile_id,))
    for e in events:
        e["keywords"] = json.loads(e["keywords"])
        e["data"] = json.loads(e["data"])
    return {"format": "homelib-algorithm-v1", "exported_at": now(), "profile_id": profile_id,
            "settings": s, "event_weights": library.EVENT_WEIGHTS,
            "keywords": prof["kw"], "channels": prof["ch"], "events": events}


def reset(profile_id: int) -> None:
    with db.tx():
        db.x("DELETE FROM events WHERE profile_id=? AND type<>'deleted'", (profile_id,))
        db.x("DELETE FROM settings WHERE scope=? AND key='algo'", (f"profile:{profile_id}",))
        db.x("DELETE FROM reco_cache WHERE profile_id IN (?, 0)", (profile_id,))
