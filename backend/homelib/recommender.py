"""The "For you" recommender: two stages, all local (SQLite + a small CPU model).

Stage 1, candidates (`fetch_candidates`, run by the nightly job and the reload
button). A fixed budget of yt-dlp flat searches per refresh (QUERY_BUDGET),
politely spaced, cached for a few hours:
  * interest searches: each interest (interests.py) gets a share of the
    budget by weight, at most a third, pinned ones at least one; every
    search is a different phrasing (label, keyword pairs, example titles,
    "... explained", lexicon searches), rotated on each reload;
  * channel searches: the Videos tab of channels you finished or liked
    (share set by the Channel loyalty slider);
  * exploration searches (share = the Exploration slider): topics next to
    your interests (topics.py) and topics outside all of them;
  * subscriptions are checked separately (subscriptions.py).
  New candidates are embedded in the same background job.

Stage 2, ranking (`rank`). Blocked channels/topics, "not interested", watched,
already downloaded and reloaded-away videos are dropped. Each candidate gets
  0.5 x interest  (similarity to its closest interest x that interest's weight)
+ 0.2 x keywords  (the 1.4 keyword/channel profile: boosts, mutes, forget)
+ 0.15 x similar  (to videos you liked / asked more of / rewatched)
+ channel         (you like the channel; you're subscribed)
+ freshness       (small, only new uploads from channels you like or similar ones)
- 0.6 x avoid     (similar to what you disliked / asked less of)
+ a small daily shuffle.

Feed assembly (`assemble`): exploration slots = Exploration slider x feed
size; each interest gets slots by weight but at most MAX_SHARE of them;
interests and exploration are interleaved; new uploads from subscriptions
are added on top (they don't take discovery slots).

"Both of us" scores every candidate for each profile and favours what both
like (0.6 x the lower score + 0.4 x the mean). Anything either of you blocked,
marked "not interested" or "less like this" (or something very like it) is
left out.

Every score comes with readable reasons and a numeric breakdown ("Why am I
seeing this?").
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import db, embeddings, interests, library, subscriptions, text, topics
from .db import now

log = logging.getLogger("homelib.recommender")

DEFAULT_ALGO = {
    "half_life_days": 30,     # slider 7..90
    "exploration": 0.2,       # slider 0..1: share of the feed (and searches) spent exploring
    "channel_loyalty": 0.4,   # slider 0..1 (0 = topics only, 1 = channels matter most)
    "boosts": {},             # {"kw:sourdough": 2.0, "ch:Knife & Fire": 2.0}; kw: entries are manual interests
    "mutes": [],              # ["kw:cats", "ch:Some Channel"]: blocked topics and channels
    "forgotten": [],          # terms the profile ignores entirely
    "starter_topics": ["woodworking", "japan travel", "space science", "guitar lessons"],
    "include_shorts_in_feed": False,
}
BOOST_DEFAULT = 2.0
SEEN_DAYS = 30            # how long a reloaded-away suggestion stays hidden
RELOAD_PER_QUERY = 20     # results per search on a reload
FEED_SIZE = 40
MAX_SHARE = 0.3           # no interest gets more than this share of the interest slots...
RELAX_SHARE = 0.5         # ...unless there's nothing else to show
QUERY_SHARE_CAP = 0.34    # ...or of the interest searches
SUB_SLOTS = 6             # new subscription uploads added to the feed
FRESH_DAYS = 14
FRESH_BONUS = 0.1
SEARCH_TTL = 6 * 3600
REFRESH_WAIT = 20         # the reload button waits this long before answering "still working"
EVENT_LABELS = {"finish": "finished", "like": "liked", "dislike": "disliked", "rewatch": "rewatched",
                "delete_unwatched": "deleted unwatched", "skip": "skipped", "search": "searched",
                "more": "asked for more", "less": "asked for less", "not_interested": "not interested",
                "watched_mostly": "watched most of", "subscribed": "subscribed"}

# Set from the config by configure() (app.create_app).
QUERY_BUDGET = 14         # yt-dlp searches per profile per refresh (+1 for Shorts)
CHANNEL_CHECKS = 30       # subscribed channels checked per refresh
REQUEST_DELAY = 1.5       # seconds between yt-dlp requests (randomised +-25 %)
THUMBS_DIR = None

_reload_lock = threading.Lock()
_job: dict = {"thread": None, "done": None, "result": None, "kind": None, "started": None}
_job_lock = threading.Lock()


def configure(cfg) -> None:
    global QUERY_BUDGET, CHANNEL_CHECKS, REQUEST_DELAY, THUMBS_DIR
    QUERY_BUDGET = cfg.query_budget
    CHANNEL_CHECKS = cfg.channel_checks
    REQUEST_DELAY = 0.0 if cfg.mock else cfg.request_delay
    THUMBS_DIR = cfg.thumbs_dir
    mode = "builtin" if cfg.mock and cfg.embedding_model == "auto" else cfg.embedding_model
    embeddings.configure(mode, cfg.data_dir / "models", cfg.embed_threads)
    for pid in library.profile_ids():
        interests.migrate(pid)


# ---- settings ----------------------------------------------------------------------

def algo_settings(profile_id: int) -> dict:
    interests.migrate(profile_id)
    s = db.get_setting(f"profile:{profile_id}", "algo", {}) or {}
    out = json.loads(json.dumps(DEFAULT_ALGO))
    out.update(s)
    # Keyword boosts live on as manual interests (one source of truth).
    out["boosts"] = {**{k: v for k, v in out.get("boosts", {}).items() if not k.startswith("kw:")},
                     **interests.manual_boosts(profile_id)}
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
    for k in ("mutes", "forgotten"):
        if k in patch:
            cur[k] = list(patch[k])
    if "boosts" in patch:
        wanted = dict(patch["boosts"])
        have = interests.manual_boosts(profile_id)
        for key, amount in wanted.items():
            if key.startswith("kw:") and have.get(key) != amount:
                interests.set_manual_topic(profile_id, key[3:], amount)
        for key in have:
            if key not in wanted:
                interests.set_manual_topic(profile_id, key[3:], None)
        cur["boosts"] = wanted
    stored = {**cur, "boosts": {k: v for k, v in cur["boosts"].items() if not k.startswith("kw:")}}
    db.set_setting(f"profile:{profile_id}", "algo", stored)
    return algo_settings(profile_id)


def term_key(kind: str, term: str) -> str:
    if kind not in ("kw", "ch"):
        raise ValueError("kind must be 'kw' (keyword) or 'ch' (channel)")
    term = term.strip()
    return f"{kind}:{term.lower() if kind == 'kw' else term}"


def set_boost(profile_id: int, kind: str, term: str, amount: float | None) -> dict:
    """Boost a keyword/topic (a manual interest) or a channel."""
    key = term_key(kind, term)
    a = algo_settings(profile_id)
    a["mutes"] = [m for m in a["mutes"] if m != key]
    if kind == "kw":
        interests.set_manual_topic(profile_id, term, max(-5.0, min(5.0, float(amount))) if amount else None)
        return save_algo_settings(profile_id, {"mutes": a["mutes"]})
    if amount:
        a["boosts"][key] = max(-5.0, min(5.0, float(amount)))
    else:
        a["boosts"].pop(key, None)
    return save_algo_settings(profile_id, {"mutes": a["mutes"], "boosts": a["boosts"]})


def set_mute(profile_id: int, kind: str, term: str, muted: bool, vector: list | None = None) -> dict:
    """Block (mute) a topic or a channel. A blocked topic also blocks videos
    that are about the same thing (embedding similarity), not just the word."""
    a = algo_settings(profile_id)
    key = term_key(kind, term)
    a["mutes"] = [m for m in a["mutes"] if m != key] + ([key] if muted else [])
    if muted:
        a["boosts"].pop(key, None)
        if kind == "kw":
            interests.set_manual_topic(profile_id, term, None)
            if vector is not None:
                embeddings.store(interests.block_key(profile_id, key), vector)
    else:
        db.x("DELETE FROM embeddings WHERE key=?", (interests.block_key(profile_id, key),))
    return save_algo_settings(profile_id, {"mutes": a["mutes"], "boosts": a["boosts"]})


def set_forgotten(profile_id: int, kind: str, term: str, forgotten: bool) -> dict:
    a = algo_settings(profile_id)
    key = term_key(kind, term)
    a["forgotten"] = [m for m in a["forgotten"] if m != key] + ([key] if forgotten else [])
    return save_algo_settings(profile_id, {"forgotten": a["forgotten"]})


def block_interest(profile_id: int, iid: int) -> dict:
    """Block an interest: its topic is muted (by meaning) and the interest hidden."""
    r = interests.get_row(profile_id, iid)
    vec = r["vec"] if r["vec"] and r["model"] == embeddings.model_name() else None
    out = set_mute(profile_id, "kw", r["label"], True, vector=vec)
    if r["kind"] == "learned":
        interests.delete(profile_id, iid)
    return out


# ---- the 1.4 keyword profile (still one of the ranking terms) ---------------------------

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
    boosts: dict[str, float] = {}
    for key, b in s.get("boosts", {}).items():
        kind, name = key.split(":", 1)
        words = text.keywords(name) if kind == "kw" else [name]
        if kind == "kw" and len(words) > 1:  # a multi-word topic boosts each of its words
            for w in words:
                boosts[f"kw:{w}"] = boosts.get(f"kw:{w}", 0.0) + float(b) / math.sqrt(len(words))
        else:
            boosts[f"{kind}:{words[0] if words else name}"] = float(b)
    for key in boosts:
        terms[key]  # make sure boosted-but-never-seen terms exist
    out: dict[str, dict] = {"kw": {}, "ch": {}}
    for key, ent in terms.items():
        if key in forgotten:
            continue
        kind, name = key.split(":", 1)
        boost = boosts.get(key, 0.0)
        out[kind][name] = {"weight": round(ent["learned"] + boost, 4), "learned": round(ent["learned"], 4),
                           "boost": round(boost, 4), "counts": dict(ent["counts"]),
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


def _jitter(video_id: str, day: str) -> float:
    h = hashlib.sha256(f"{day}:{video_id}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2 ** 32


def _counts_phrase(counts: dict) -> str:
    parts = [f"{EVENT_LABELS.get(k, k)} {n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])
             if k in EVENT_LABELS and k != "subscribed"]
    if counts.get("subscribed"):
        parts.insert(0, "subscribe to a channel about it")
    return ", ".join(parts[:3])


def score(candidate: dict, profile: dict, settings: dict, now_ts: float | None = None,
          who: str = "you") -> dict | None:
    """The 1.4 keyword score of one candidate. None if it is muted."""
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


# ---- stage 1: candidates ------------------------------------------------------------

class Budget:
    """A fixed number of yt-dlp requests, spaced REQUEST_DELAY apart (+-25 %)."""

    def __init__(self, n: int, delay: float | None = None):
        self.left = int(n)
        self.used = 0
        self.delay = REQUEST_DELAY if delay is None else delay
        self._last = 0.0

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        self.used += 1
        if self._last and self.delay > 0:
            wait = self.delay * random.uniform(0.75, 1.25) - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
        self._last = time.time()
        return True


@dataclass
class Query:
    q: str
    source: str
    kind: str                 # interest | channel | explore | starter | shorts
    shorts: bool = False
    channel_id: str = ""
    interest_id: int | None = None


def allocate(weights: dict, n: int, cap_share: float, pinned: set | None = None, pinned_min: int = 1,
             salt: int = 0) -> dict:
    """Split n slots by weight, at most ceil(cap_share * n) each, pinned ones at
    least pinned_min. Returns {key: slots}; the sum can be < n when everything is capped."""
    pinned = pinned or set()
    keys = [k for k, w in weights.items() if w > 0 or k in pinned]
    out = {k: 0 for k in keys}
    if n <= 0 or not keys:
        return out
    cap = max(1, math.ceil(cap_share * n))
    left = n
    for k in sorted(pinned & set(keys), key=lambda k: -weights.get(k, 0)):
        give = min(pinned_min, cap, left)
        out[k] += give
        left -= give
    total = sum(max(0.0, weights[k]) for k in keys) or 1.0

    def tiebreak(k):
        return int(hashlib.sha1(f"{k}:{salt}".encode()).hexdigest()[:6], 16) / 16 ** 6 * 0.25

    while left > 0:
        open_ = [k for k in keys if out[k] < cap]
        if not open_:
            break
        k = max(open_, key=lambda k: (max(0.0, weights[k]) / total * n - out[k] + tiebreak(k), str(k)))
        out[k] += 1
        left -= 1
    return out


def _active_interests(profile_id: int, settings: dict, compute: bool | None = None) -> list[dict]:
    rs = interests.ensure(profile_id, settings, compute=compute)
    model = embeddings.model_name()
    out = []
    for r in rs:
        r["weight"] = interests.effective_weight(r)
        if r["vec"] is not None and r["model"] != model:
            r["vec"] = None
        out.append(r)
    return out


def _blocked_phrase(q: str, settings: dict) -> bool:
    ws = set(text.keywords(q))
    for m in settings.get("mutes", []):
        if m.startswith("kw:"):
            words = text.keywords(m[3:]) or [m[3:]]
            if all(w in ws for w in words):
                return True
    return False


def plan_queries(profile_id: int, settings: dict | None = None, budget: int | None = None,
                 rotation: int = 0) -> list[Query]:
    """The searches for one refresh: interests, channels, exploration (+1 Shorts)."""
    s = settings or algo_settings(profile_id)
    B = QUERY_BUDGET if budget is None else budget
    ints = [r for r in _active_interests(profile_id, s) if r["weight"] > 0 or r["pinned"]]
    explore = s["exploration"]
    n_explore = round(B * explore) if ints else B
    if explore >= 0.05 and n_explore == 0:
        n_explore = 1
    subs = subscriptions.channel_ids(profile_id)
    mutes = set(s.get("mutes", []))
    prof = build_profile(profile_id, s)
    chans = sorted(((c, e["weight"]) for c, e in prof["ch"].items()
                    if e["weight"] > 0.3 and f"ch:{c}" not in mutes), key=lambda t: -t[1])
    chan_ids = _channel_ids([c for c, _ in chans])
    chans = [(c, w) for c, w in chans if chan_ids.get(c) not in subs]
    n_channel = min(len(chans), round((B - n_explore) * s["channel_loyalty"] * 0.5)) if ints else 0
    n_int = B - n_explore - n_channel
    alloc = allocate({r["id"]: r["weight"] for r in ints}, n_int, QUERY_SHARE_CAP,
                     pinned={r["id"] for r in ints if r["pinned"]}, salt=rotation)
    n_explore += n_int - sum(alloc.values())  # capped interests: the rest goes to exploring

    out: list[Query] = []
    for r in sorted(ints, key=lambda r: -r["weight"]):
        for q in interests.query_phrases(r, rotation)[:alloc.get(r["id"], 0)]:
            out.append(Query(q, f"interest:{r['id']}", "interest", interest_id=r["id"]))
    for c, _ in _rotate(chans[:max(n_channel * 2, 1)], rotation)[:n_channel]:
        out.append(Query(c, f"channel:{c}", "channel", channel_id=chan_ids.get(c, "")))
    out += _exploration_queries(profile_id, s, ints, n_explore, rotation)
    seed = (interests.query_phrases(ints[0], rotation)[:1] if ints else []) or s["starter_topics"][:1] or ["funny"]
    out.append(Query(seed[0], f"shorts:{seed[0]}", "shorts", shorts=True))
    seen, uniq = set(), []
    for qy in out:
        k = qy.q.lower() + str(qy.shorts)
        if k not in seen and not _blocked_phrase(qy.q, s):
            seen.add(k)
            uniq.append(qy)
    return uniq


def _rotate(xs: list, r: int) -> list:
    if not xs:
        return xs
    r %= len(xs)
    return xs[r:] + xs[:r]


def _channel_ids(names: list[str]) -> dict[str, str]:
    if not names:
        return {}
    rows = db.q(f"SELECT channel, channel_id FROM videos WHERE channel_id<>'' AND channel IN "
                f"({','.join('?' * len(names))})", names)
    return {r["channel"]: r["channel_id"] for r in rows}


def _exploration_queries(profile_id: int, s: dict, ints: list[dict], n: int, rotation: int) -> list[Query]:
    if n <= 0:
        return []
    day = int(now() // 86400) + rotation
    th = embeddings.thresholds()
    labels = {r["label"] for r in ints}
    ref = embeddings.RefSet([r["vec"] for r in ints if r["vec"]])
    negs = embeddings.RefSet([v for v, _, _ in interests.feedback_vectors(
        profile_id, s, interests.NEGATIVE_EXPLICIT, limit=60)])

    taken = {p.lower() for r in ints for p in interests.query_phrases(r)}

    def far_from_dislikes(q: str) -> bool:
        if q.lower() in taken:
            return False
        taken.add(q.lower())
        v = embeddings.embed_text(q, compute=embeddings.ready())
        return v is None or max(negs.sims(v) or [0.0]) < th["join"]

    out: list[Query] = []
    # Next to what you like: neighbouring topics from the lexicon.
    adjacent = []
    for r in sorted(ints, key=lambda r: -r["weight"]):
        key = topics.dominant([(r["label"], 1.0)] + [(e, 1.0) for e in r["stats"].get("examples", [])])
        for adj in topics.adjacent(key) if key else []:
            if topics.label(adj) not in labels:
                adjacent.append((adj, r["label"]))
        if not key and r["stats"].get("keywords"):
            adjacent.append((None, f"{r['stats']['keywords'][0]} documentary"))
    adjacent = _rotate(list(dict.fromkeys(adjacent)), day)
    for adj, near in adjacent[: math.ceil(n / 2) if ints else 0]:
        if adj is None:
            q = near
        else:
            opts = list(topics.seeds(adj)) or [topics.label(adj)]
            q = opts[day % len(opts)]
        if far_from_dislikes(q):
            out.append(Query(q, f"explore:{q}", "explore"))
    # Outside everything you like: starter topics, then a general list, then
    # any topic in the lexicon.
    general = _rotate(list(topics.OUTSIDE_SEEDS), day)
    every = _rotate([q for t in topics.TOPICS.values() for q in t[3]], day)
    pool = list(dict.fromkeys(_rotate(list(s.get("starter_topics", [])), day) + general + every))
    for q in pool:
        if len(out) >= n:
            break
        v = embeddings.embed_text(q, compute=embeddings.ready())
        if v is not None and ref and max(ref.sims(v)) >= th["merge"]:
            continue  # that's already one of your interests
        if far_from_dislikes(q):
            out.append(Query(q, ("starter:" if q in s.get("starter_topics", []) else "explore:") + q,
                             "starter" if q in s.get("starter_topics", []) else "explore"))
    return out[:n]


def candidate_queries(profile_id: int, max_queries: int | None = None, rotation: int = 0) -> list[tuple[str, str, bool]]:
    """(query, source label, shorts?) for a refresh (used by the Shorts prefetch)."""
    return [(q.q, q.source, q.shorts) for q in plan_queries(profile_id, budget=max_queries, rotation=rotation)]


def cached_search(yt, query: str, n: int, shorts: bool, budget: Budget | None, since: float) -> list[dict] | None:
    """yt.search with a cache. None means the request budget is used up."""
    kind = f"{'shorts' if shorts else 'search'}:{n}"
    r = db.q1("SELECT results, fetched_at FROM search_cache WHERE query=? AND kind=?", (query.lower(), kind))
    if r and r["fetched_at"] >= since:
        return json.loads(r["results"])
    if budget is not None and not budget.take():
        return None
    res = yt.search(query, n, shorts=shorts)
    _cache_put(query, kind, res)
    return res


def cached_channel(yt, channel_id: str, channel: str, n: int, budget: Budget | None, since: float) -> list[dict] | None:
    kind = f"channel:{n}"
    r = db.q1("SELECT results, fetched_at FROM search_cache WHERE query=? AND kind=?", (channel_id, kind))
    if r and r["fetched_at"] >= since:
        return json.loads(r["results"])
    if budget is not None and not budget.take():
        return None
    res = yt.channel_uploads(channel_id, n, channel)
    _cache_put(channel_id, kind, res)
    return res


def _cache_put(query: str, kind: str, res: list[dict]) -> None:
    t = now()
    db.x("INSERT INTO search_cache(query, kind, results, fetched_at) VALUES(?,?,?,?) ON CONFLICT(query, kind) "
         "DO UPDATE SET results=excluded.results, fetched_at=excluded.fetched_at",
         (query.lower() if not kind.startswith("channel") else query, kind, json.dumps(res), t))
    db.x("DELETE FROM search_cache WHERE fetched_at < ?", (t - 2 * SEARCH_TTL,))


def fetch_candidates(yt, profile_id: int, per_query: int = 10, fresh: bool = False,
                     budget: Budget | None = None, cache_since: float | None = None) -> int:
    """Stage 1 for one profile: run the planned searches, store the results in
    reco_cache and embed them.

    fresh=True (a reload) rotates the searches, digs deeper and replaces the
    profile's pool with what came back. If most searches fail (offline), the
    old pool is kept so the feed doesn't go empty."""
    n = ok = failed = 0
    t = now()
    rotation = 0
    if fresh:
        rotation = (db.get_setting(f"profile:{profile_id}", "reload_count", 0) or 0) + 1
        db.set_setting(f"profile:{profile_id}", "reload_count", rotation)
        per_query = max(per_query, RELOAD_PER_QUERY)
    since = (cache_since if cache_since is not None else t) if fresh else t - SEARCH_TTL
    budget = budget or Budget(QUERY_BUDGET + 1)
    s = algo_settings(profile_id)
    for qy in plan_queries(profile_id, s, rotation=rotation):
        try:
            if qy.kind == "channel" and qy.channel_id and hasattr(yt, "channel_uploads"):
                results = cached_channel(yt, qy.channel_id, qy.q, per_query, budget, since)
            else:
                results = cached_search(yt, qy.q, per_query, qy.shorts, budget, since)
        except Exception as e:
            failed += 1
            log.warning("candidate search %r failed: %s", qy.q, e)
            continue
        if results is None:
            log.info("request budget used up; skipping %r", qy.q)
            continue
        ok += 1
        with db.tx():
            for r in results:
                r = dict(r)
                r["keywords"] = text.keywords(r.get("title", ""))
                db.x("INSERT INTO reco_cache(profile_id, video_id, meta, source, fetched_at) VALUES(?,?,?,?,?) "
                     "ON CONFLICT(profile_id, video_id) DO UPDATE SET meta=excluded.meta, source=excluded.source, "
                     "fetched_at=excluded.fetched_at", (profile_id, r["id"], json.dumps(r), qy.source, t))
                n += 1
    embed_pool([profile_id])
    # Forget candidates that weren't refreshed for a week, or, on a reload,
    # everything from before it (unless most searches failed, e.g. offline).
    cutoff = t if (fresh and n and ok >= failed) else t - 7 * 86400
    db.x("DELETE FROM reco_cache WHERE profile_id=? AND fetched_at < ?", (profile_id, cutoff))
    log.info("fetched %d candidates for profile %d (%d searches ok, %d failed, %d requests)",
             n, profile_id, ok, failed, budget.used)
    return n


def candidate_text(m: dict) -> str:
    return ". ".join(p for p in (m.get("title", ""), (m.get("description") or "")[:300],
                                 " ".join(m.get("keywords") or [])) if p)


def embed_pool(profile_ids: list[int], compute: bool = True) -> int:
    """Embed candidates and subscription uploads that don't have a vector yet (batched)."""
    ph = ",".join("?" * len(profile_ids))
    metas = [json.loads(r["meta"]) for r in db.q(f"SELECT meta FROM reco_cache WHERE profile_id IN ({ph})",
                                                 profile_ids)]
    metas += [json.loads(r["meta"]) for r in db.q(
        f"SELECT u.meta FROM channel_uploads u JOIN subscriptions s ON s.channel_id=u.channel_id "
        f"WHERE s.profile_id IN ({ph})", profile_ids)]
    want = {m["id"]: (candidate_text(m), m.get("channel", "")) for m in metas}
    have = embeddings.cached(list(want))
    missing = {k: v for k, v in want.items() if k not in have}
    if missing and compute:
        embeddings.vectors(missing, compute=True)
    return len(missing)


# ---- stage 2: ranking -------------------------------------------------------------------

@dataclass
class Ctx:
    pid: int
    settings: dict
    kwprof: dict
    ints: list
    int_ref: embeddings.RefSet
    maxw: float
    pos: list
    pos_ref: embeddings.RefSet
    neg: list
    neg_ref: embeddings.RefSet
    subs: set
    sub_names: set
    block_words: list
    block_ref: embeddings.RefSet
    block_keys: list
    chan_vecs: dict
    chan_ref: embeddings.RefSet
    th: dict
    hard_neg: set = field(default_factory=set)


def build_ctx(profile_id: int, settings: dict | None = None, compute: bool | None = None) -> Ctx:
    s = settings or algo_settings(profile_id)
    compute = embeddings.ready() if compute is None else compute
    ints = [r for r in _active_interests(profile_id, s, compute) if r["vec"] is not None]
    pos = interests.feedback_vectors(profile_id, s, interests.POSITIVE_EXPLICIT, limit=80, compute=compute)
    neg = interests.feedback_vectors(profile_id, s, interests.NEGATIVE_EXPLICIT, limit=120, compute=compute)
    block_words, block_vecs, block_keys = [], [], []
    for m in s.get("mutes", []):
        if not m.startswith("kw:"):
            continue
        phrase = m[3:]
        block_words.append((m, text.keywords(phrase) or [phrase.lower()]))
        v = embeddings.cached([interests.block_key(profile_id, m)]).get(interests.block_key(profile_id, m))
        v = v or embeddings.embed_text(phrase, compute=compute)
        if v:
            block_vecs.append(v)
            block_keys.append(m)
    # What the channels you like are about (for "new upload from a similar channel").
    chan_vecs: dict[str, list] = {}
    groups: dict[str, list] = defaultdict(list)
    for r in ints:
        for ch in r["stats"].get("channels", []):
            groups[ch].append(r["vec"])
    for ch, vs in groups.items():
        chan_vecs[ch] = embeddings.weighted_mean(vs, [1.0] * len(vs))
    hard_neg = {r["video_id"] for r in db.q(
        "SELECT DISTINCT video_id FROM events WHERE profile_id=? AND type IN ('less','not_interested') "
        "AND video_id IS NOT NULL", (profile_id,))}
    return Ctx(pid=profile_id, settings=s, kwprof=build_profile(profile_id, s), ints=ints,
               int_ref=embeddings.RefSet([r["vec"] for r in ints]),
               maxw=max([r["weight"] for r in ints if r["weight"] > 0] or [1.0]),
               pos=pos, pos_ref=embeddings.RefSet([v for v, _, _ in pos]),
               neg=neg, neg_ref=embeddings.RefSet([v for v, _, _ in neg]),
               subs=subscriptions.channel_ids(profile_id), sub_names=subscriptions.subscribed_names(profile_id),
               block_words=block_words, block_ref=embeddings.RefSet(block_vecs), block_keys=block_keys,
               chan_vecs=chan_vecs, chan_ref=embeddings.RefSet(list(chan_vecs.values())),
               th=embeddings.thresholds(), hard_neg=hard_neg)


def blocked(c: dict, vec: list | None, ctx: Ctx) -> str | None:
    """Why this candidate is blocked for this profile (None if it isn't)."""
    mutes = set(ctx.settings.get("mutes", []))
    if f"ch:{c.get('channel', '')}" in mutes:
        return f"channel {c.get('channel')}"
    kws = set(c.get("keywords") or text.keywords(c.get("title", "")))
    title = (c.get("title") or "").lower()
    for key, words in ctx.block_words:
        if all(w in kws for w in words) or key[3:] in title:
            return f"topic “{key[3:]}”"
    if vec is not None and len(ctx.block_ref):
        sims = ctx.block_ref.sims(vec)
        i = max(range(len(sims)), key=sims.__getitem__)
        if sims[i] >= ctx.th["block"]:
            return f"topic “{ctx.block_keys[i][3:]}”"
    return None


def score_candidate(c: dict, vec: list | None, ctx: Ctx, chan_vec: list | None = None,
                    now_ts: float | None = None) -> dict:
    t = now_ts or now()
    model_scale = embeddings.sim_scale
    # Closest interest, weighted by how much that interest counts.
    best, best_val, best_rel, best_sim, avoid_manual = None, 0.0, 0.0, 0.0, 0.0
    for r, sim in zip(ctx.ints, ctx.int_ref.sims(vec) if vec is not None else []):
        rel = model_scale(sim)
        if r["weight"] < 0:
            avoid_manual = max(avoid_manual, rel * min(1.0, -r["weight"] / 5))
            continue
        val = rel * (0.6 + 0.4 * min(1.0, r["weight"] / ctx.maxw))
        if val > best_val:
            best, best_val, best_rel, best_sim = r, val, rel, sim
    if vec is None and c.get("source", "").startswith("interest:"):  # not embedded yet: trust the search
        iid = int(c["source"].split(":", 1)[1])
        best = next((r for r in ctx.ints if r["id"] == iid), None)
        best_val = best_rel = 0.5 if best else 0.0
        best_sim = ctx.th["join"] if best else 0.0
    # It belongs to an interest only if it's as close as the interest's own
    # videos are; results of exploration searches need to be clearly on-topic.
    exploring = c.get("source", "").startswith(("explore:", "starter:"))
    member = best is not None and best_sim >= ctx.th["merge" if exploring else "join"]
    kw = score(c, ctx.kwprof, ctx.settings, now_ts=t) or {"breakdown": {"familiar": 0.0}, "reasons": []}
    kw_part = math.tanh(kw["breakdown"]["familiar"] / 3.0)

    def strongest(ref, items):
        if vec is None or not len(ref):
            return 0.0, None
        vals = [model_scale(sm) * min(1.0, s / 2.0) for sm, (_, s, _) in zip(ref.sims(vec), items)]
        i = max(range(len(vals)), key=vals.__getitem__)
        return vals[i], items[i][2]

    pos, pos_vid = strongest(ctx.pos_ref, ctx.pos)
    neg, neg_vid = strongest(ctx.neg_ref, ctx.neg)
    avoid = max(neg, avoid_manual)
    ch = c.get("channel", "")
    ch_ent = ctx.kwprof["ch"].get(ch)
    ch_w = ch_ent["weight"] if ch_ent else 0.0
    subscribed = c.get("channel_id") in ctx.subs or ch in ctx.sub_names
    loyalty = ctx.settings.get("channel_loyalty", 0.4)
    chan = (0.05 + 0.15 * loyalty) * math.tanh(ch_w / 2) + (0.2 if subscribed else 0.0)
    # Freshness: age doesn't matter, except a small lift for new uploads from
    # channels you like or channels that are about the same things.
    fresh, fresh_why = 0.0, ""
    ts = c.get("upload_ts")
    if ts and t - ts < FRESH_DAYS * 86400:
        affinity = 1.0 if subscribed else 0.8 if ch_w > 0.3 else 0.0
        if not affinity and chan_vec is not None and len(ctx.chan_ref):
            if max(ctx.chan_ref.sims(chan_vec)) >= ctx.th["high"]:
                affinity = 0.6
        if affinity:
            fresh = FRESH_BONUS * affinity * (1 - (t - ts) / (FRESH_DAYS * 86400))
            fresh_why = ("New upload from a channel you subscribe to" if subscribed else
                         "New upload from a channel you like" if affinity == 0.8 else
                         "New upload from a channel like ones you watch")
    day = time.strftime("%Y-%m-%d", time.localtime(t))
    jit = 0.03 * _jitter(c["id"], day)
    total = 0.5 * best_val + 0.2 * kw_part + 0.15 * pos + chan + fresh - 0.6 * avoid + jit

    reasons = []
    if member:
        phrase = _counts_phrase(best["stats"].get("counts", {}))
        lab = f"Matches your interest “{best['label']}”" + (" (pinned)" if best["pinned"] else "")
        if best["kind"] == "manual":
            lab += " (added by you)"
        elif phrase:
            lab += f": you {phrase}"
        reasons.append(lab)
    if fresh_why:
        reasons.append(fresh_why)
    if pos >= 0.5:
        reasons.append("Similar to videos you liked")
    if ch_ent and ch_w > 0.3 and not subscribed:
        reasons.append(f"From {ch}: you " + (_counts_phrase(ch_ent["counts"]) or "like it"))
    for r in kw["reasons"]:
        if r.startswith(("Topic", "You boosted")) and len(reasons) < 3:
            reasons.append(r)
    if avoid >= 0.4:
        reasons.append("Down-weighted: like things you asked for less of")
    if not reasons:
        reasons.append("Exploration pick: something new for you")
    return {
        "score": round(total, 4), "reasons": reasons,
        "interest_id": best["id"] if member else None,
        "interest": best["label"] if member else None,
        "breakdown": {"interest_part": round(0.5 * best_val, 3), "keyword_part": round(0.2 * kw_part, 3),
                      "similar_part": round(0.15 * pos, 3), "channel_part": round(chan, 3),
                      "fresh_part": round(fresh, 3), "avoid_part": round(-0.6 * avoid, 3),
                      "jitter_part": round(jit, 3), "relevance": round(best_rel, 3), "avoid": round(avoid, 3)},
        "avoid_vid": neg_vid if neg >= 0.4 else None,
    }


def excluded_video_ids(profile_ids: list[int]) -> set[str]:
    """Things not worth recommending: already in the library, watched, disliked or not interested."""
    ids = {r["id"] for r in db.q("SELECT id FROM videos WHERE status IN ('ready','downloading','queued')")}
    if profile_ids:
        ph = ",".join("?" * len(profile_ids))
        ids |= {r["video_id"] for r in db.q(
            f"SELECT video_id FROM watch_state WHERE profile_id IN ({ph}) AND (watched=1 OR rating<0)", profile_ids)}
        ids |= {r["video_id"] for r in db.q(
            f"SELECT video_id FROM events WHERE profile_id IN ({ph}) AND type='not_interested'", profile_ids)}
    return ids


def _pool(profile_ids: list[int]) -> list[dict]:
    ph = ",".join("?" * len(profile_ids))
    out, seen = [], set()
    for r in db.q(f"SELECT video_id, meta, source FROM reco_cache WHERE profile_id IN ({ph}) "
                  f"ORDER BY profile_id, fetched_at DESC", profile_ids):
        if r["video_id"] in seen:
            continue
        seen.add(r["video_id"])
        m = json.loads(r["meta"])
        m["source"] = r["source"]
        out.append(m)
    return out


def _channel_vectors(cands: list[dict], vecs: dict) -> dict[str, list]:
    groups: dict[str, list] = defaultdict(list)
    for c in cands:
        if c["id"] in vecs and c.get("channel"):
            groups[c["channel"]].append(vecs[c["id"]])
    return {ch: embeddings.weighted_mean(vs, [1.0] * len(vs)) for ch, vs in groups.items()}


def _vectors_for(cands: list[dict]) -> dict:
    return embeddings.vectors({c["id"]: (candidate_text(c), c.get("channel", "")) for c in cands},
                              compute=embeddings.ready())


def rank(profile_id: int, mode: str = "me", limit: int = FEED_SIZE, shorts: bool | None = False,
         profile_names: dict[int, str] | None = None) -> list[dict]:
    """Score the candidate pool and assemble the feed. mode='both' is the
    shared feed for the two of you."""
    pids = library.profile_ids() if mode == "both" else [profile_id]
    bucket = 0 if mode == "both" else profile_id
    ctxs = [build_ctx(p) for p in pids]
    excluded = excluded_video_ids(pids)
    seen_away = seen_ids(bucket)
    hard_neg = set().union(*(c.hard_neg for c in ctxs))
    cands = [c for c in _pool(pids) if c["id"] not in excluded and c["id"] not in seen_away
             and (shorts is None or bool(c.get("is_short")) == shorts)]
    subs_new: list[dict] = []
    if shorts is not True:
        if mode == "both":
            common = set.intersection(*(c.subs for c in ctxs)) if ctxs else set()
            ups = [u for u in subscriptions.new_uploads(pids[0]) if u.get("channel_id") in common]
        else:
            ups = subscriptions.new_uploads(profile_id)
        have = {c["id"] for c in cands}
        for u in ups:
            if u["id"] in excluded:
                continue
            u = {**u, "keywords": text.keywords(u.get("title", "")), "source": f"sub:{u.get('channel', '')}"}
            subs_new.append(u)
            if u["id"] in have:
                cands = [c for c in cands if c["id"] != u["id"]]
    vecs = _vectors_for(cands + subs_new)
    chan_vecs = _channel_vectors(cands, vecs)
    names = profile_names or {p["id"]: p["name"] for p in library.profiles()}
    near = min(c.th["near"] for c in ctxs)
    hard_ref = embeddings.RefSet(list(embeddings.cached(list(hard_neg)).values())) if mode == "both" else None

    sub_ids = {u["id"] for u in subs_new}
    scored = []
    for c in cands + subs_new:
        v = vecs.get(c["id"])
        if any(blocked(c, v, x) for x in ctxs):
            continue
        if mode == "both":
            if c["id"] in hard_neg or (v is not None and hard_ref and max(hard_ref.sims(v)) >= near):
                continue  # one of you said less / not interested to (almost) this
            parts = [score_candidate(c, v, x, chan_vecs.get(c.get("channel", ""))) for x in ctxs]
            lo, mean = min(p["score"] for p in parts), sum(p["score"] for p in parts) / len(parts)
            top_i = max(range(len(parts)), key=lambda i: parts[i]["score"])
            res = dict(parts[top_i])
            res["score"] = round(0.6 * lo + 0.4 * mean, 4)
            ws = [next((r["weight"] for r in x.ints if r["id"] == p["interest_id"]), 0.0) for p, x in zip(parts, ctxs)]
            if all(p["interest_id"] for p in parts):
                # Something you both like: its own lane, weighted by both interests.
                res["group"] = "both:" + "+".join(str(p["interest_id"]) for p in parts)
                res["group_weight"] = sum(ws)
            elif res["interest_id"]:
                res["group"] = f"{pids[top_i]}:{res['interest_id']}"
                res["group_weight"] = 0.5 * ws[top_i]
            else:
                res["group"], res["group_weight"] = "explore", 0
            res["reasons"] = _both_reasons(parts, pids, names)
            res["breakdown"] = {**res["breakdown"], "per_profile": {str(p): x["score"] for p, x in zip(pids, parts)}}
        else:
            res = score_candidate(c, v, ctxs[0], chan_vecs.get(c.get("channel", "")))
            res["group"] = res["interest_id"] if res["interest_id"] else "explore"
            res["group_weight"] = next((r["weight"] for r in ctxs[0].ints if r["id"] == res["interest_id"]), 0)
        if res["breakdown"]["avoid"] >= 0.75 and res["group"] == "explore":
            continue  # an "exploration" pick that's really something you said no to
        res.pop("avoid_vid", None)
        item = {**c, **res, "thumb": f"/api/thumb/{c['id']}"}
        if c["id"] in sub_ids:
            item["slot"] = "subscription"
            item["new_upload"] = True
            item["reasons"] = [f"New from {c.get('channel', 'a channel')} (you subscribe)"
                               + (" (you both do)" if mode == "both" else "")] + item["reasons"][:1]
        scored.append(item)

    explore_share = sum(x.settings["exploration"] for x in ctxs) / len(ctxs)
    pinned = {(f"{x.pid}:{r['id']}" if mode == "both" else r["id"]) for x in ctxs for r in x.ints if r["pinned"]}
    out = assemble([s for s in scored if s.get("slot") != "subscription"],
                   [s for s in scored if s.get("slot") == "subscription"], limit, explore_share, pinned)
    _cache_scores(bucket, out)
    return out


def _both_reasons(parts: list[dict], pids: list[int], names: dict) -> list[str]:
    labs = [p["interest"] for p in parts]
    if all(labs):
        if len(set(labs)) == 1:
            r = [f"You both like “{labs[0]}”"]
        else:
            r = ["For you both: " + " · ".join(f"{names.get(p, p)} likes “{lab}”" for p, lab in zip(pids, labs))]
    elif any(labs):
        i = next(i for i, lab in enumerate(labs) if lab)
        r = [f"{names.get(pids[i], 'One of you')} likes “{labs[i]}”; new for the other"]
    else:
        r = ["Exploration pick: new for you both"]
    extra = [x for p in parts for x in p["reasons"] if x.startswith("New upload")]
    return r + extra[:1]


def assemble(scored: list[dict], subs: list[dict], limit: int, explore_share: float, pinned: set | None = None) -> list[dict]:
    """Pick and order the feed: interest slots with per-interest caps,
    exploration slots from the slider, varied order, subscriptions on top."""
    pinned = pinned or set()
    groups: dict = defaultdict(list)
    for s in sorted(scored, key=lambda s: (-s["score"], s["id"])):
        groups[s["group"]].append(s)
    explore = groups.pop("explore", [])
    n_exp = round(limit * explore_share)
    if explore_share >= 0.05 and explore and n_exp == 0:
        n_exp = 1
    n_int = limit - n_exp
    weights = {g: max(items[0].get("group_weight") or 0.0, 0.05) for g, items in groups.items()}
    quota = allocate(weights, n_int, MAX_SHARE, pinned=pinned & set(groups), pinned_min=2)
    quota = {g: min(q, len(groups[g])) for g, q in quota.items()}
    spare = n_int - sum(quota.values())
    n_exp = min(len(explore), n_exp + spare)  # capped interests leave room for discovery
    spare = limit - n_exp - sum(quota.values())
    if spare > 0:  # nothing else to show: let interests go up to RELAX_SHARE
        relax_cap = math.ceil(RELAX_SHARE * limit)
        for g in sorted(groups, key=lambda g: -weights[g]):
            add = min(spare, relax_cap - quota.get(g, 0), len(groups[g]) - quota.get(g, 0))
            if add > 0:
                quota[g] = quota.get(g, 0) + add
                spare -= add
    lanes = {g: groups[g][:q] for g, q in quota.items() if q > 0}
    if n_exp:
        lanes["explore"] = explore[:n_exp]
    for s in lanes.get("explore", []):
        s["slot"] = "explore"
        if not any(r.startswith("Exploration") for r in s["reasons"]):
            s["reasons"] = ["Exploration pick: something new for you"] + s["reasons"][:2]
    for g, items in lanes.items():
        if g != "explore":
            for s in items:
                s["slot"] = "interest"
    # Interleave: each lane advances by 1/size, so lanes are spread evenly and
    # the same interest rarely shows up twice in a row.
    order: list[dict] = []
    passes = {g: 0.0 for g in lanes}
    idx = {g: 0 for g in lanes}
    last = None
    while True:
        open_ = [g for g in lanes if idx[g] < len(lanes[g])]
        if not open_:
            break
        open_.sort(key=lambda g: (passes[g], -lanes[g][idx[g]]["score"], str(g)))
        g = open_[0]
        if g == last and len(open_) > 1 and passes[open_[1]] - passes[g] < 1.0 / max(1, len(lanes[g])) * 1.5:
            g = open_[1]
        order.append(lanes[g][idx[g]])
        idx[g] += 1
        passes[g] += 1.0 / len(lanes[g])
        last = g
    order = order[:limit]
    # New uploads from subscriptions: always included, spread over the top of the feed.
    for i, s in enumerate(sorted(subs, key=lambda s: -(s.get("upload_ts") or 0))[:SUB_SLOTS]):
        order.insert(min(len(order), i * 3), s)
    return order


def _cache_scores(bucket: int, out: list[dict]) -> None:
    """Remember the scored feed (used by "Why am I seeing this?" and debugging)."""
    t = now()
    with db.tx():
        for c in out:
            db.x("INSERT INTO reco_cache(profile_id, video_id, meta, source, score, reasons, fetched_at, scored_at) "
                 "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(profile_id, video_id) DO UPDATE SET score=excluded.score, "
                 "reasons=excluded.reasons, scored_at=excluded.scored_at",
                 (bucket, c["id"], json.dumps(_meta_only(c)), c.get("source", ""), c["score"],
                  json.dumps({"reasons": c["reasons"], "breakdown": c["breakdown"]}), t, t))


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
    """Hide the suggestions the feed was showing, so a reload brings new ones.
    New uploads from subscriptions stay until watched (or a week old)."""
    scope, key = _shown_setting(profile_id, mode)
    ids = db.get_setting(scope, key, []) or []
    subs = {r["video_id"] for r in db.q("SELECT video_id FROM channel_uploads")}
    bucket = 0 if mode == "both" else profile_id
    t = now()
    with db.tx():
        for vid in ids:
            if vid in subs:
                continue
            db.x("INSERT INTO reco_seen(profile_id, video_id, ts) VALUES(?,?,?) "
                 "ON CONFLICT(profile_id, video_id) DO UPDATE SET ts=excluded.ts", (bucket, vid, t))
        db.x("DELETE FROM reco_seen WHERE ts < ?", (t - SEEN_DAYS * 86400,))
    db.set_setting(scope, key, [])
    return len(ids)


def refresh(yt, profile_ids: list[int], set_aside: list[tuple[int, str]], rank_modes: list[tuple[int, str]]) -> dict:
    """One full refresh (the nightly run or the reload button): set aside what
    was shown, check subscriptions, rebuild interests, fetch and embed
    candidates, rank. Runs in a background thread; one at a time."""
    with _reload_lock:
        started = now()
        for p, mode in set_aside:
            set_aside_shown(p, mode)
        subs = subscriptions.refresh(yt, profile_ids, budget=Budget(CHANNEL_CHECKS), thumbs_dir=THUMBS_DIR)
        if embeddings.model_name() == embeddings.MINILM:
            embeddings._load_minilm()  # may take a while the first time; we're in the background
        fetched = 0
        for p in profile_ids:
            try:
                interests.rebuild(p, algo_settings(p), compute=True)
                fetched += fetch_candidates(yt, p, fresh=True, cache_since=started)
            except Exception:
                log.exception("candidate fetch failed for profile %s", p)
        suggestions = {}
        for p, mode in rank_modes:
            try:
                suggestions[f"{p}:{mode}"] = len(rank(p, mode))
            except Exception:
                log.exception("ranking failed for profile %s (%s)", p, mode)
        try:
            embeddings.prune()
        except Exception:
            log.exception("embedding cleanup failed")
        return {"fetched": fetched, "suggestions": suggestions, "subscriptions": subs,
                "seconds": round(now() - started, 1)}


def _run_job(kind: str, fn) -> dict:
    """Run fn in a background thread (or join the one already running)."""
    with _job_lock:
        if _job["thread"] is None or not _job["thread"].is_alive():
            done = threading.Event()

            def target():
                try:
                    _job["result"] = fn()
                except Exception as e:
                    log.exception("%s failed", kind)
                    _job["result"] = {"error": str(e)}
                finally:
                    done.set()

            _job.update(thread=threading.Thread(target=target, name=f"reco-{kind}", daemon=True),
                        done=done, result=None, kind=kind, started=now())
            _job["thread"].start()
        return _job


def job_status() -> dict:
    j = _job
    running = j["thread"] is not None and j["thread"].is_alive()
    return {"running": running, "kind": j["kind"] if running else None, "started": j["started"] if running else None}


def reload_feed(yt, profile_id: int, mode: str = "me", wait: float | None = REFRESH_WAIT) -> dict:
    """The reload button: set aside what you were shown and fetch a fresh batch
    in the background. Waits up to `wait` seconds for it; after that the app
    shows "still reloading" and polls."""
    pids = library.profile_ids() if mode == "both" else [profile_id]
    job = _run_job("reload", lambda: refresh(yt, pids, [(profile_id, mode)], [(profile_id, mode)]))
    if wait is None or not job["done"].wait(wait):
        return {"running": True, "done": False, "fetched": 0, "suggestions": None}
    res = job["result"] or {}
    if "error" in res:
        return {"done": True, "error": res["error"], "fetched": 0, "suggestions": 0}
    return {"done": True, "fetched": res.get("fetched", 0), "subscriptions": res.get("subscriptions"),
            "suggestions": sum(res.get("suggestions", {}).values())}


def nightly_reload(yt) -> dict:
    """The early-morning run: the same full refresh for every profile and for "Both of us"."""
    pids = library.profile_ids()
    job = _run_job("nightly", lambda: refresh(yt, pids, [(p, "me") for p in pids] + [(pids[0], "both")],
                                              [(p, "me") for p in pids] + [(pids[0], "both")]))
    job["done"].wait()
    return job["result"] or {}


def _meta_only(c: dict) -> dict:
    return {k: c.get(k) for k in ("id", "title", "channel", "channel_id", "duration", "url", "thumb_url",
                                  "keywords", "is_short", "view_count", "description", "upload_ts")}


def explain(profile_id: int, video_id: str, mode: str = "me") -> dict:
    pids = library.profile_ids() if mode == "both" else [profile_id]
    r = db.q1(f"SELECT meta, source FROM reco_cache WHERE video_id=? AND profile_id IN "
              f"({','.join('?' * len(pids))},0)", (video_id, *pids))
    if r:
        meta = json.loads(r["meta"])
        meta["source"] = r["source"]
    else:
        up = db.q1("SELECT meta FROM channel_uploads WHERE video_id=?", (video_id,))
        meta = json.loads(up["meta"]) if up else library.get_video(video_id)
        meta["source"] = "sub:" if up else ""
    kws = meta.get("keywords") or text.keywords(meta.get("title", ""))
    meta = {**meta, "keywords": kws}
    vec = _vectors_for([meta]).get(meta["id"])
    ctxs = [build_ctx(p) for p in pids]
    parts = [score_candidate(meta, vec, x) for x in ctxs]
    block = next((b for b in (blocked(meta, vec, x) for x in ctxs) if b), None)
    if mode == "both":
        lo, mean = min(p["score"] for p in parts), sum(p["score"] for p in parts) / len(parts)
        names = {p["id"]: p["name"] for p in library.profiles()}
        result = {**parts[0], "score": round(0.6 * lo + 0.4 * mean, 4), "reasons": _both_reasons(parts, pids, names)}
        settings = blend_settings([x.settings for x in ctxs])
        prof = blend_profiles([x.kwprof for x in ctxs])
    else:
        result, settings, prof = parts[0], ctxs[0].settings, ctxs[0].kwprof
    result.pop("avoid_vid", None)
    terms = [{"kind": "kw", "term": k, **(prof["kw"].get(k) or {"weight": 0, "counts": {}})} for k in kws]
    ch = meta.get("channel", "")
    terms.append({"kind": "ch", "term": ch, **(prof["ch"].get(ch) or {"weight": 0, "counts": {}})})
    interest = None
    if result.get("interest_id"):
        row = next((x for c in ctxs for x in c.ints if x["id"] == result["interest_id"]), None)
        interest = interests.view(row) if row else None
    return {"video": _meta_only(meta), "result": None if block else result, "blocked": block, "terms": terms,
            "interest": interest,
            "settings": {k: settings[k] for k in ("half_life_days", "exploration", "channel_loyalty")},
            "source": meta.get("source", ""), "embedder": embeddings.status()}


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
            "settings": s, "event_weights": library.ALL_WEIGHTS, "signal_strengths": interests.SIGNALS,
            "interests": [interests.view(r) for r in interests.rows(profile_id)],
            "subscriptions": db.q("SELECT channel_id, channel, created_at FROM subscriptions WHERE profile_id=?",
                                  (profile_id,)),
            "keywords": prof["kw"], "channels": prof["ch"], "events": events}


def reset(profile_id: int) -> None:
    with db.tx():
        db.x("DELETE FROM events WHERE profile_id=? AND type<>'deleted'", (profile_id,))
        db.x("DELETE FROM settings WHERE scope=? AND key='algo'", (f"profile:{profile_id}",))
        db.x("DELETE FROM reco_cache WHERE profile_id IN (?, 0)", (profile_id,))
        db.x("DELETE FROM embeddings WHERE key LIKE ?", (f"block:{profile_id}:%",))
    interests.reset(profile_id)
