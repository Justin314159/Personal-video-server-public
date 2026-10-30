"""Interest clusters: what a profile likes, as a handful of readable topics.

1. Signals. Every video a profile reacted to becomes one item with a signal
   strength (SIGNALS), each event faded by the Memory slider's half-life.
   Explicit feedback counts more than watching. Searches are small items of
   their own, recent uploads of subscribed channels count a little.
2. Clustering. Positive items are grouped greedily by embedding similarity
   (embeddings.py): an item joins the closest interest if it's similar enough,
   otherwise it starts a new one; near-identical interests merge. Manual
   topics (typed in the editor, or 1.4 keyword boosts) are fixed clusters that
   matching items join. Negative items lower the evidence of the interest
   they're closest to.
3. Weight. evidence = sum of the members' signals; weight = log(1 + evidence),
   so it grows sublinearly (2 finished videos: 1.1; 20: 3.0) and decays with
   the signals. A weight set by hand always wins.
4. Identity. Rebuilding keeps each interest's row (and its name, pin and
   weight edits) by matching members, so edits survive taste drifting.

Everything here is per profile: only that profile's events, watch state,
settings and subscriptions are read.
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import db, embeddings, subtitles, text, topics
from .db import now

log = logging.getLogger("homelib.interests")

# Signal strength per event type (explicit > implicit).
SIGNALS = {
    "finish": 1.0, "rewatch": 1.5, "watched_mostly": 0.5,
    "skip": -0.6, "abandoned": -0.4, "delete_unwatched": -0.8,
    "like": 2.0, "dislike": -2.0, "more": 2.5, "less": -2.5, "not_interested": -3.0,
    "search": 0.3,
}
SUB_UPLOAD_SIGNAL = 0.5    # per recent upload of a subscribed channel (up to SUB_UPLOADS)
SUB_UPLOADS = 4
NEGATIVE_EXPLICIT = ("dislike", "less", "not_interested")
POSITIVE_EXPLICIT = ("like", "more", "rewatch")
MAX_LEARNED = 12
MIN_EVIDENCE = 0.25
MANUAL_DEFAULT = 2.0       # weight of a typed topic (same as a 1.4 boost)
WEIGHT_RANGE = (-5.0, 5.0)
ABANDON_AFTER = 2 * 86400  # a barely-started video counts as abandoned after this long
QUERY_MODIFIERS = ("explained", "documentary", "for beginners", "deep dive", "history of", "how it works")


@dataclass
class Item:
    key: str
    text: str
    channel: str = ""
    title: str = ""
    video_id: str | None = None
    signal: float = 0.0
    counts: Counter = field(default_factory=Counter)
    ts: float = 0.0


def decay(age_seconds: float, half_life_days: float) -> float:
    return 0.5 ** (max(0.0, age_seconds) / 86400.0 / max(half_life_days, 0.01))


def effective_weight(row: dict) -> float:
    if row.get("manual_weight") is not None:
        return float(row["manual_weight"])
    return round(math.log1p(max(0.0, row.get("evidence") or 0.0)), 4)


# ---- signals ------------------------------------------------------------------------

def _transcript_snippet(vid: str, words: int = 60) -> str:
    r = db.q1("SELECT path FROM subtitles WHERE video_id=? ORDER BY CASE kind WHEN 'manual' THEN 0 "
              "WHEN 'uploaded' THEN 1 ELSE 2 END, id LIMIT 1", (vid,))
    if not r or not Path(r["path"]).is_file():
        return ""
    try:
        cues = subtitles.parse(Path(r["path"]).read_text(encoding="utf-8"))
    except Exception:
        return ""
    out: list[str] = []
    for c in cues:
        out += re.sub(r"\s+", " ", c.get("text", "")).split()
        if len(out) >= words:
            break
    return " ".join(out[:words])


def video_text(v: dict, with_transcript: bool = True) -> str:
    """What gets embedded for a video: title, description, tags, transcript start."""
    kws = v.get("keywords") or []
    if isinstance(kws, str):
        kws = json.loads(kws or "[]")
    parts = [v.get("title") or "", (v.get("description") or "")[:300], " ".join(kws)]
    if with_transcript and v.get("id"):
        parts.append(_transcript_snippet(v["id"]))
    return ". ".join(p for p in parts if p)


def collect_items(profile_id: int, settings: dict, now_ts: float | None = None) -> dict[str, Item]:
    t = now_ts or now()
    hl = settings.get("half_life_days", 30)
    forgotten = set(settings.get("forgotten", []))
    items: dict[str, Item] = {}
    events = db.q("SELECT e.id, e.video_id, e.type, e.keywords, e.channel, e.data, e.ts, v.title, v.description, "
                  "v.keywords AS vkw FROM events e LEFT JOIN videos v ON v.id=e.video_id "
                  "WHERE e.profile_id=? AND e.type<>'deleted' AND e.type<>'subscribe'", (profile_id,))
    for e in events:
        sig = SIGNALS.get(e["type"])
        if sig is None:
            continue
        kws = [k for k in json.loads(e["keywords"] or "[]") if f"kw:{k}" not in forgotten]
        if e["type"] == "search":
            q = json.loads(e["data"] or "{}").get("query") or " ".join(kws)
            key, txt, title, ch = "q:" + q.strip().lower(), q, q, ""
        elif e["video_id"]:
            key, ch = e["video_id"], e["channel"] or ""
            title = e["title"] or json.loads(e["data"] or "{}").get("title", "")
            txt = ""  # filled in only if the vector isn't cached yet
        else:  # an event without a video (only made by tests and very old rows)
            ch = e["channel"] or ""
            key, txt, title = "e:" + "|".join(kws) + "|" + ch, " ".join(kws), " ".join(kws)
        if ch and f"ch:{ch}" in forgotten:
            continue
        it = items.get(key) or Item(key=key, text=txt, channel=ch, title=title,
                                    video_id=e["video_id"] if e["type"] != "search" else None)
        it.signal += sig * decay(t - e["ts"], hl)
        it.counts[e["type"]] += 1
        it.ts = max(it.ts, e["ts"])
        items[key] = it
    # Implicit signals that live in the watch state, not in events.
    for w in db.q("SELECT w.video_id, w.position, w.duration, w.updated_at, v.title, v.channel, v.status "
                  "FROM watch_state w JOIN videos v ON v.id=w.video_id WHERE w.profile_id=? AND w.watched=0 "
                  "AND w.rating>=0 AND w.position>=2", (profile_id,)):
        dur = w["duration"] or 0
        if not dur or (w["channel"] and f"ch:{w['channel']}" in forgotten):
            continue
        frac = w["position"] / dur
        if frac >= 0.5:
            kind = "watched_mostly"
        elif frac < 0.15 and t - w["updated_at"] >= ABANDON_AFTER:
            kind = "abandoned"
        else:
            continue
        it = items.get(w["video_id"]) or Item(key=w["video_id"], text="", channel=w["channel"] or "",
                                              title=w["title"] or "", video_id=w["video_id"])
        it.signal += SIGNALS[kind] * decay(t - w["updated_at"], hl)
        it.counts[kind] += 1
        it.ts = max(it.ts, w["updated_at"])
        items[it.key] = it
    # Subscribing is a strong positive for the channel's topics.
    for s in db.q("SELECT channel_id, channel, created_at FROM subscriptions WHERE profile_id=?", (profile_id,)):
        for u in db.q("SELECT video_id, meta FROM channel_uploads WHERE channel_id=? ORDER BY "
                      "COALESCE(upload_ts, first_seen) DESC LIMIT ?", (s["channel_id"], SUB_UPLOADS)):
            m = json.loads(u["meta"])
            it = items.get(u["video_id"]) or Item(key=u["video_id"], text="", channel=s["channel"],
                                                  title=m.get("title", ""), video_id=u["video_id"])
            it.signal += SUB_UPLOAD_SIGNAL * decay(t - s["created_at"], hl)
            it.counts["subscribed"] += 1
            items[it.key] = it
    return items


def item_vectors(items: dict[str, Item], compute: bool) -> dict[str, list]:
    have = embeddings.cached(list(items))
    req = {}
    for k, it in items.items():
        if k in have:
            continue
        if not it.text and it.video_id:
            v = db.q1("SELECT * FROM videos WHERE id=?", (it.video_id,))
            if v:
                it.text = video_text(v)
            else:
                u = db.q1("SELECT meta FROM channel_uploads WHERE video_id=?", (it.video_id,))
                it.text = video_text(json.loads(u["meta"]), with_transcript=False) if u else it.title
        req[k] = (it.text or it.title, it.channel)
    if req:
        have.update(embeddings.vectors(req, compute=compute))
    return have


# ---- clustering ------------------------------------------------------------------------

@dataclass
class _Cluster:
    row: dict | None
    fixed: bool                    # manual topic: the centroid doesn't move
    centroid: list
    acc: list | None = None
    members: list = field(default_factory=list)   # (item, sim)
    pos: float = 0.0
    neg: float = 0.0

    def add(self, it: Item, v: list, sim: float) -> None:
        self.members.append((it, sim))
        self.pos += it.signal
        if not self.fixed:
            self.acc = [a + it.signal * x for a, x in zip(self.acc, v)] if self.acc else [it.signal * x for x in v]
            self.centroid = embeddings.normalize(self.acc)


def rows(profile_id: int, include_hidden: bool = False) -> list[dict]:
    rs = db.q("SELECT * FROM interests WHERE profile_id=?" + ("" if include_hidden else " AND hidden=0")
              + " ORDER BY id", (profile_id,))
    for r in rs:
        for k in ("seeds", "members"):
            r[k] = json.loads(r[k] or "[]")
        r["stats"] = json.loads(r["stats"] or "{}")
        r["vec"] = embeddings.unpack(r["centroid"]) if r.get("centroid") else None
    return rs


def manual_text(row: dict) -> str:
    return ", ".join(dict.fromkeys([row["label"], *row.get("seeds", [])]))


def _label_for(members: list[tuple[Item, float]], taken: set[str]) -> str:
    weighted = [(it.title or it.text, it.signal) for it, _ in members]
    key = topics.dominant(weighted)
    if key and topics.label(key) not in taken:
        return topics.label(key)
    counts: Counter = Counter()
    for it, _ in members:
        for k in text.topic_words(it.title or it.text, limit=8):
            counts[k] += max(0.1, it.signal)
    top = [k for k, _ in counts.most_common(3)]
    lab = " & ".join(w.capitalize() for w in top[:2]) or (members[0][0].title[:40] if members else "Interest")
    if lab in taken and len(top) > 2:
        lab = f"{lab} & {top[2].capitalize()}"
    return lab


def rebuild(profile_id: int, settings: dict, compute: bool = True, now_ts: float | None = None) -> list[dict]:
    """Recompute this profile's interests from its signals and save them."""
    th = embeddings.thresholds()
    model = embeddings.model_name()
    existing = rows(profile_id, include_hidden=True)
    hidden_members = {k for r in existing if r["hidden"] for k in r["stats"].get("hidden_members", [])}
    mutes = set(settings.get("mutes", []))
    items = collect_items(profile_id, settings, now_ts)
    vecs = item_vectors(items, compute)

    clusters: list[_Cluster] = []
    for r in existing:
        if r["kind"] != "manual" or r["hidden"]:
            continue
        v = embeddings.embed_text(manual_text(r), compute=compute)
        if v is None:
            v = r["vec"] if r["model"] == model else None
        if v is not None:
            clusters.append(_Cluster(row=r, fixed=True, centroid=v))
    pos = sorted((it for it in items.values() if it.signal > 0.05 and it.key in vecs and it.key not in hidden_members
                  and not (it.channel and f"ch:{it.channel}" in mutes)), key=lambda it: (-it.signal, it.key))
    for it in pos:
        v = vecs[it.key]
        best, sim = None, -1.0
        for c in clusters:
            s = embeddings.dot(v, c.centroid)
            if s > sim:
                best, sim = c, s
        if best is not None and sim >= th["join"]:
            best.add(it, v, sim)
        else:
            c = _Cluster(row=None, fixed=False, centroid=v)
            c.add(it, v, 1.0)
            clusters.append(c)
    # Merge learned clusters that ended up describing the same thing.
    merged = True
    while merged:
        merged = False
        learned = [c for c in clusters if not c.fixed]
        for i, a in enumerate(learned):
            for b in learned[i + 1:]:
                if embeddings.dot(a.centroid, b.centroid) >= th["merge"]:
                    for it, s in b.members:
                        a.add(it, vecs[it.key], s)
                    clusters.remove(b)
                    merged = True
                    break
            if merged:
                break
    for it in items.values():
        if it.signal < -0.05 and it.key in vecs:
            v = vecs[it.key]
            best = max(clusters, key=lambda c: embeddings.dot(v, c.centroid), default=None)
            if best is not None and embeddings.dot(v, best.centroid) >= th["join"]:
                best.neg += it.signal
    learned = sorted((c for c in clusters if not c.fixed), key=lambda c: -(c.pos + 0.5 * c.neg))
    learned = [c for c in learned if c.pos + 0.5 * c.neg >= MIN_EVIDENCE][:MAX_LEARNED]
    keep = [c for c in clusters if c.fixed] + learned

    # Match learned clusters to existing rows so names, pins and weights stick.
    t = now()
    used: set[int] = set()
    taken = {c.row["label"] for c in keep if c.fixed}
    out_ids = []
    with db.tx():
        for c in keep:
            members = sorted(c.members, key=lambda m: -m[0].signal)
            keys = [it.key for it, _ in members]
            counts: Counter = Counter()
            for it, _ in members:
                counts.update(it.counts)
            kw: Counter = Counter()
            for it, _ in members:
                for k in text.topic_words(it.title or it.text, limit=8):
                    kw[k] += max(0.1, it.signal)
            stats = {"counts": dict(counts), "n": len(members), "neg": round(c.neg, 3),
                     "examples": [it.title for it, _ in members[:3] if it.title],
                     "keywords": [k for k, _ in kw.most_common(8)],
                     "channels": [ch for ch, _ in Counter(it.channel for it, _ in members if it.channel).most_common(3)]}
            evidence = round(max(0.0, c.pos + 0.5 * c.neg), 4)
            row = c.row
            if row is None:
                row = _match(existing, keys, c.centroid, model, used)
            if row is not None and row["kind"] == "learned" and row["hidden"]:
                used.add(row["id"])
                prev = row["stats"]
                prev.update(hidden_members=sorted(set(prev.get("hidden_members", [])) | set(keys)))
                db.x("UPDATE interests SET stats=? WHERE id=?", (json.dumps(prev), row["id"]))
                continue
            if row is None:
                label = _label_for(members, taken)
                iid = db.x("INSERT INTO interests(profile_id, kind, label, model, centroid, evidence, members, stats, "
                           "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (profile_id, "learned", label, model, embeddings.pack(c.centroid), evidence,
                            json.dumps(keys[:50]), json.dumps(stats), t, t)).lastrowid
            else:
                used.add(row["id"])
                iid = row["id"]
                label = row["label"]
                if row["kind"] == "learned" and not row["renamed"]:
                    label = _label_for(members, taken)
                if row["kind"] == "manual":
                    stats["hidden_members"] = row["stats"].get("hidden_members", [])
                db.x("UPDATE interests SET label=?, model=?, centroid=?, evidence=?, members=?, stats=?, updated_at=? "
                     "WHERE id=?", (label, model, embeddings.pack(c.centroid), evidence, json.dumps(keys[:50]),
                                    json.dumps(stats), t, iid))
            taken.add(label)
            out_ids.append(iid)
        for r in existing:
            if r["id"] in used or r["kind"] == "manual" or r["hidden"]:
                continue
            if r["renamed"] or r["pinned"] or r["manual_weight"] is not None:
                # Edited by hand: keep it (it no longer has fresh evidence).
                db.x("UPDATE interests SET evidence=0, updated_at=? WHERE id=?", (t, r["id"]))
            else:
                db.x("DELETE FROM interests WHERE id=?", (r["id"],))
    db.set_setting(f"profile:{profile_id}", "interests_built", {"at": t, "model": model,
                                                                "sig": signature(profile_id)})
    return rows(profile_id)


def _match(existing: list[dict], keys: list[str], centroid: list, model: str, used: set[int]) -> dict | None:
    best, score = None, 0.0
    ks = set(keys)
    for r in existing:
        if r["kind"] != "learned" or r["id"] in used:
            continue
        old = set(r["members"]) | set(r["stats"].get("hidden_members", []))
        overlap = len(ks & old) / max(1, min(len(ks), len(old))) if old else 0.0
        sim = embeddings.dot(centroid, r["vec"]) if (not r["hidden"] and r["vec"] and r["model"] == model) else 0.0
        s = max(overlap, sim if sim >= 0.8 else 0.0)
        if s >= 0.5 and s > score:
            best, score = r, s
    return best


def signature(profile_id: int) -> list:
    """Changes whenever anything the interests are built from changes."""
    e = db.q1("SELECT COUNT(*) n, COALESCE(MAX(id), 0) m FROM events WHERE profile_id=?", (profile_id,))
    w = db.q1("SELECT COALESCE(MAX(updated_at), 0) u FROM watch_state WHERE profile_id=?", (profile_id,))
    s = db.q1("SELECT COUNT(*) n FROM subscriptions WHERE profile_id=?", (profile_id,))
    i = db.q1("SELECT COALESCE(MAX(updated_at), 0) u, COUNT(*) n FROM interests WHERE profile_id=? AND kind='manual'",
              (profile_id,))
    a = db.get_setting(f"profile:{profile_id}", "algo", {}) or {}
    return [e["n"], e["m"], round(w["u"]), s["n"], i["n"], round(i["u"], 3), a.get("half_life_days"),
            len(a.get("forgotten", [])), len(a.get("mutes", [])), embeddings.model_name()]


def ensure(profile_id: int, settings: dict, compute: bool | None = None) -> list[dict]:
    """The profile's interests, rebuilt if anything changed since the last build."""
    built = db.get_setting(f"profile:{profile_id}", "interests_built", None) or {}
    if built.get("sig") == signature(profile_id) and now() - built.get("at", 0) < 86400:
        return rows(profile_id)
    return rebuild(profile_id, settings, compute=embeddings.ready() if compute is None else compute)


# ---- views and queries -------------------------------------------------------------------

def view(r: dict) -> dict:
    st = r["stats"]
    return {"id": r["id"], "label": r["label"], "kind": r["kind"], "weight": round(effective_weight(r), 3),
            "auto_weight": round(math.log1p(max(0.0, r["evidence"] or 0)), 3), "manual_weight": r["manual_weight"],
            "pinned": bool(r["pinned"]), "evidence": round(r["evidence"] or 0, 3), "videos": st.get("n", 0),
            "counts": st.get("counts", {}), "examples": st.get("examples", []), "keywords": st.get("keywords", []),
            "channels": st.get("channels", []), "seeds": r["seeds"], "renamed": bool(r["renamed"]),
            "migrated": bool(st.get("migrated"))}


def query_phrases(r: dict, rotation: int = 0) -> list[str]:
    """Several different searches for one interest, rotated on every reload."""
    st = r["stats"]
    kws = st.get("keywords", [])[:6]
    label = r["label"]
    key = topics.dominant([(label, 1.0)] + [(e, 1.0) for e in st.get("examples", [])], min_share=0.5)
    lex = topics.TOPICS.get(key, ("", "", "", ()))[3] if key else ()
    first: list[str] = [*r.get("seeds", [])]
    if r["kind"] == "manual" or r["renamed"] or label in {v[0] for v in topics.TOPICS.values()}:
        first.append(label)
    pairs = [f"{a} {b}" for i, a in enumerate(kws[:4]) for b in kws[i + 1:4]]
    titles = [" ".join(text.topic_words(e, limit=4)) for e in st.get("examples", [])]
    base = (first or [label if "&" not in label else (pairs[0] if pairs else label)])[0]
    mods = [f"{m} {base}" if m == "history of" else f"{base} {m}" for m in QUERY_MODIFIERS]
    order: list[str] = []
    for group in zip_longest_all(first, pairs, titles, list(lex), mods):
        order += group
    seen, out = set(), []
    for q in order:
        q = re.sub(r"\s+", " ", q.replace("&", " ")).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    if not out:
        return []
    r0 = rotation % len(out)
    return out[r0:] + out[:r0]


def zip_longest_all(*lists: list[str]) -> list[list[str]]:
    n = max((len(x) for x in lists), default=0)
    return [[x[i] for x in lists if i < len(x)] for i in range(n)]


# ---- editing ----------------------------------------------------------------------------

def _clean_label(label: str) -> str:
    label = re.sub(r"\s+", " ", (label or "").strip())[:60]
    if not label:
        raise ValueError("Type a topic.")
    return label


def _clamp(w: float) -> float:
    return max(WEIGHT_RANGE[0], min(WEIGHT_RANGE[1], float(w)))


def get_row(profile_id: int, iid: int) -> dict:
    r = next((r for r in rows(profile_id, include_hidden=True) if r["id"] == iid), None)
    if not r:
        from .library import NotFound

        raise NotFound(f"interest {iid}")
    return r


def add_topic(profile_id: int, label: str, weight: float = MANUAL_DEFAULT, pinned: bool = False,
              seeds: list[str] | None = None, migrated: bool = False) -> dict:
    """A topic typed by hand. Works with no history: it's embedded and searched for."""
    label = _clean_label(label)
    same = db.q1("SELECT id FROM interests WHERE profile_id=? AND kind='manual' AND lower(label)=lower(?)",
                 (profile_id, label))
    t = now()
    if same:
        db.x("UPDATE interests SET manual_weight=?, hidden=0, updated_at=? WHERE id=?", (_clamp(weight), t, same["id"]))
        iid = same["id"]
    else:
        iid = db.x("INSERT INTO interests(profile_id, kind, label, manual_weight, pinned, seeds, stats, created_at, "
                   "updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (profile_id, "manual", label, _clamp(weight), 1 if pinned else 0,
                    json.dumps([s for s in (seeds or []) if s and s.lower() != label.lower()][:5]),
                    json.dumps({"migrated": migrated} if migrated else {}), t, t)).lastrowid
    if embeddings.ready():
        embeddings.embed_text(manual_text(get_row(profile_id, iid)))
    return view(get_row(profile_id, iid))


def update(profile_id: int, iid: int, label: str | None = None, weight: float | None = None,
           auto: bool = False, pinned: bool | None = None) -> dict:
    r = get_row(profile_id, iid)
    sets, args = [], []
    if label is not None:
        sets += ["label=?", "renamed=1"]
        args.append(_clean_label(label))
    if auto and r["kind"] == "learned":
        sets.append("manual_weight=NULL")
    elif weight is not None:
        sets.append("manual_weight=?")
        args.append(_clamp(weight))
    if pinned is not None:
        sets.append("pinned=?")
        args.append(1 if pinned else 0)
    if sets:
        db.x(f"UPDATE interests SET {', '.join(sets)}, updated_at=? WHERE id=?", (*args, now(), iid))
    return view(get_row(profile_id, iid))


def delete(profile_id: int, iid: int) -> None:
    """Manual topics go away; a learned interest is hidden so it doesn't come straight back."""
    r = get_row(profile_id, iid)
    if r["kind"] == "manual":
        db.x("DELETE FROM interests WHERE id=?", (iid,))
        return
    st = r["stats"]
    st["hidden_members"] = sorted(set(st.get("hidden_members", [])) | set(r["members"]))
    db.x("UPDATE interests SET hidden=1, pinned=0, stats=?, updated_at=? WHERE id=?", (json.dumps(st), now(), iid))


def restore(profile_id: int, iid: int) -> dict:
    r = get_row(profile_id, iid)
    st = r["stats"]
    st.pop("hidden_members", None)
    db.x("UPDATE interests SET hidden=0, stats=?, updated_at=? WHERE id=?", (json.dumps(st), now(), iid))
    return view(get_row(profile_id, iid))


def block_key(profile_id: int, mute_key: str) -> str:
    return f"block:{profile_id}:{mute_key}"


def manual_boosts(profile_id: int) -> dict[str, float]:
    """Manual topics as 1.4-style keyword boosts ({'kw:pottery': 2.0})."""
    return {f"kw:{r['label'].lower()}": float(r["manual_weight"]) for r in db.q(
        "SELECT label, manual_weight FROM interests WHERE profile_id=? AND kind='manual' AND hidden=0 "
        "AND manual_weight IS NOT NULL", (profile_id,))}


def set_manual_topic(profile_id: int, term: str, amount: float | None) -> None:
    """The 1.4 'boost a keyword' call: a manual topic with that weight (0/None removes it)."""
    term = _clean_label(term)
    if amount:
        add_topic(profile_id, term, amount)
    else:
        db.x("DELETE FROM interests WHERE profile_id=? AND kind='manual' AND lower(label)=lower(?)",
             (profile_id, term))


def migrate(profile_id: int) -> bool:
    """1.4 -> 1.5: keyword boosts become manual topics; nothing else is lost.

    Mutes stay mutes (they're the blocked topics and channels), channel boosts
    stay channel boosts, forgotten terms stay forgotten. "home cooking" is no
    longer a default starter topic (it was searched on every reload)."""
    scope = f"profile:{profile_id}"
    if (db.get_setting(scope, "algo_version", 1) or 1) >= 2:
        return False
    raw = db.get_setting(scope, "algo", {}) or {}
    boosts = raw.get("boosts") or {}
    for key, amount in list(boosts.items()):
        if key.startswith("kw:") and amount:
            add_topic(profile_id, key[3:], float(amount), migrated=True)
            boosts.pop(key)
    if raw:
        raw["boosts"] = boosts
        old = ["home cooking", "woodworking", "japan travel", "space science", "guitar lessons"]
        if raw.get("starter_topics") == old:
            raw["starter_topics"] = [t for t in old if t != "home cooking"]
        db.set_setting(scope, "algo", raw)
    db.set_setting(scope, "algo_version", 2)
    return True


def reset(profile_id: int) -> None:
    db.x("DELETE FROM interests WHERE profile_id=?", (profile_id,))
    db.x("DELETE FROM settings WHERE scope=? AND key='interests_built'", (f"profile:{profile_id}",))


# ---- similarity helpers for ranking ---------------------------------------------------------

def feedback_vectors(profile_id: int, settings: dict, kinds: tuple[str, ...], limit: int = 150,
                     compute: bool = False) -> list[tuple[list, float, str]]:
    """(vector, strength, video id) of the videos this profile gave this kind of feedback."""
    ph = ",".join("?" * len(kinds))
    evs = db.q(f"SELECT e.video_id, e.type, e.ts, e.channel, v.title FROM events e LEFT JOIN videos v ON "
               f"v.id=e.video_id WHERE e.profile_id=? AND e.type IN ({ph}) AND e.video_id IS NOT NULL "
               f"ORDER BY e.ts DESC LIMIT ?", (profile_id, *kinds, limit))
    by_vid: dict[str, float] = defaultdict(float)
    items: dict[str, Item] = {}
    for e in evs:
        by_vid[e["video_id"]] += abs(SIGNALS.get(e["type"], 1.0)) * decay(now() - e["ts"],
                                                                          settings.get("half_life_days", 30))
        items.setdefault(e["video_id"], Item(key=e["video_id"], text="", channel=e["channel"] or "",
                                             title=e["title"] or "", video_id=e["video_id"]))
    vecs = item_vectors(items, compute)
    return [(vecs[k], s, k) for k, s in by_vid.items() if k in vecs]
