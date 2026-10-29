"""Profiles, videos, queues, playlists, watch state, captions, delete votes,
events and the disk-space guard."""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from . import db, subtitles, text
from .db import now

log = logging.getLogger("homelib.library")

DEFAULT_ACCENTS = ["#46D3B8", "#F2B84B"]

# Recommender signal weights per event type (see recommender.py).
EVENT_WEIGHTS = {
    "finish": 1.0,
    "like": 1.5,
    "dislike": -2.0,
    "delete_unwatched": -1.0,
    "skip": -1.0,
    "search": 0.2,
}

WATCHED_FRACTION = 0.9  # a video counts as watched at 90% (or when it ends)


class DiskFull(Exception):
    pass


class NotFound(Exception):
    pass


# ---- profiles ---------------------------------------------------------------

def ensure_profiles(names: list[str]) -> None:
    for i, name in enumerate(names[:2], start=1):
        if not db.q1("SELECT id FROM profiles WHERE id=?", (i,)):
            db.x("INSERT INTO profiles(id,name,accent,created_at) VALUES(?,?,?,?)",
                 (i, name, DEFAULT_ACCENTS[i - 1], now()))


def profiles() -> list[dict]:
    return db.q("SELECT id, name, accent FROM profiles ORDER BY id")


def profile_ids() -> list[int]:
    return [p["id"] for p in profiles()]


def get_profile(pid: int) -> dict:
    p = db.q1("SELECT id, name, accent FROM profiles WHERE id=?", (pid,))
    if not p:
        raise NotFound(f"profile {pid}")
    return p


def rename_profile(pid: int, name: str) -> dict:
    name = name.strip()[:40]
    if not name:
        raise ValueError("name required")
    get_profile(pid)
    db.x("UPDATE profiles SET name=? WHERE id=?", (name, pid))
    return get_profile(pid)


# ---- disk guard -------------------------------------------------------------

def disk_status(library_dir: Path, min_free_gb: float) -> dict:
    library_dir.mkdir(parents=True, exist_ok=True)
    u = shutil.disk_usage(library_dir)
    used_by_lib = sum(f.stat().st_size for f in library_dir.glob("*.mp4") if f.is_file())
    return {"total": u.total, "free": u.free, "used": u.used, "library_bytes": used_by_lib,
            "min_free": int(min_free_gb * 1024 ** 3),
            "ok": u.free >= min_free_gb * 1024 ** 3}


def ensure_space(library_dir: Path, min_free_gb: float, need_bytes: int = 0) -> None:
    st = disk_status(library_dir, min_free_gb)
    if st["free"] - need_bytes < st["min_free"]:
        raise DiskFull(
            f"Only {st['free'] / 1024 ** 3:.1f} GB free; downloads pause below {min_free_gb:g} GB. "
            "Delete something or lower min_free_gb.")


# ---- videos -----------------------------------------------------------------

def video_keywords(title: str, tags: list[str] | None = None) -> list[str]:
    return text.keywords(title, " ".join(tags or []))


def upsert_video(meta: dict, added_by: int | None = None) -> dict:
    """Insert a video or refresh its metadata. Never touches status/file fields."""
    vid = meta["id"]
    kws = meta.get("keywords") or video_keywords(meta.get("title", ""), meta.get("tags"))
    existing = db.q1("SELECT id FROM videos WHERE id=?", (vid,))
    fields = {
        "title": meta.get("title") or "", "channel": meta.get("channel") or "",
        "channel_id": meta.get("channel_id") or "", "duration": meta.get("duration"),
        "url": meta.get("url") or f"https://www.youtube.com/watch?v={vid}",
        "thumb_url": meta.get("thumb_url") or "", "keywords": json.dumps(kws),
        "is_short": 1 if meta.get("is_short") else 0,
    }
    if meta.get("media") in ("video", "audio"):
        fields["media"] = meta["media"]
    if existing:
        sets = ", ".join(f"{k}=?" for k in fields if fields[k] not in ("", None))
        vals = [v for v in fields.values() if v not in ("", None)]
        if sets:
            db.x(f"UPDATE videos SET {sets} WHERE id=?", (*vals, vid))
    else:
        cols = ", ".join(fields)
        db.x(f"INSERT INTO videos(id, {cols}, added_by, created_at) VALUES(?, {', '.join('?' * len(fields))}, ?, ?)",
             (vid, *fields.values(), added_by, now()))
    return get_video(vid)


def get_video(vid: str) -> dict:
    v = db.q1("SELECT * FROM videos WHERE id=?", (vid,))
    if not v:
        raise NotFound(f"video {vid}")
    v["keywords"] = json.loads(v["keywords"] or "[]")
    return v


def set_keep(vid: str, keep: bool) -> dict:
    get_video(vid)
    db.x("UPDATE videos SET keep=? WHERE id=?", (1 if keep else 0, vid))
    return get_video(vid)


def set_audience(vid: str, audience: str) -> None:
    db.x("UPDATE videos SET audience=? WHERE id=?", (audience, vid))


def widen_audience(vid: str, profile_id: int | None, scope: str) -> None:
    """Record who needs to watch a video before auto-delete may remove it.

    Audience only ever widens: 'p:<id>' -> 'all'. New downloads set it
    explicitly (see downloader.enqueue).
    """
    v = db.q1("SELECT audience FROM videos WHERE id=?", (vid,))
    if not v:
        return
    if scope == "shared" or profile_id is None:
        new = "all"
    elif v["audience"] in ("all", f"p:{profile_id}"):
        new = v["audience"]
    else:
        new = "all"  # a second person wants it too
    if new != v["audience"]:
        set_audience(vid, new)


def _has_shared(vid: str) -> bool:
    return bool(db.q1("SELECT 1 FROM queue_items WHERE video_id=? AND scope='shared'", (vid,)))


def relevant_profiles(vid: str, all_profiles: list[int] | None = None) -> list[int]:
    """Profiles that must watch a video before it is auto-deleted.

    Shared queue (or no queue at all) means everyone; a personal-only video
    only waits for the people whose personal queue it was in.
    """
    allp = all_profiles or profile_ids()
    v = db.q1("SELECT audience FROM videos WHERE id=?", (vid,))
    if not v or v["audience"] == "all" or _has_shared(vid):
        return allp
    owners = {r["profile_id"] for r in db.q(
        "SELECT profile_id FROM queue_items WHERE video_id=? AND scope='personal'", (vid,))}
    if v["audience"].startswith("p:"):
        owners.add(int(v["audience"][2:]))
    return sorted(p for p in owners if p in allp) or allp


def delete_video(vid: str, reason: str, by_profile: int | None, library_dir: Path,
                 thumbs_dir: Path) -> dict:
    """Delete the file (and its captions), drop it from queues and playlists and
    log the deletion as an event."""
    v = get_video(vid)
    freed = 0
    if v.get("file_path"):
        p = Path(v["file_path"])
        if p.is_file():
            freed = p.stat().st_size
            p.unlink()
    for ext in ("jpg", "svg"):
        (thumbs_dir / f"{vid}.{ext}").unlink(missing_ok=True)
    for sub in db.q("SELECT path FROM subtitles WHERE video_id=?", (vid,)):
        Path(sub["path"]).unlink(missing_ok=True)
    with db.tx():
        db.x("DELETE FROM queue_items WHERE video_id=?", (vid,))
        db.x("DELETE FROM playlist_items WHERE video_id=?", (vid,))
        db.x("DELETE FROM subtitles WHERE video_id=?", (vid,))
        db.x("DELETE FROM delete_votes WHERE video_id=?", (vid,))
        db.x("UPDATE videos SET status='deleted', file_path=NULL, deleted_at=?, delete_reason=? WHERE id=?",
             (now(), reason, vid))
        db.x("INSERT INTO events(profile_id, video_id, type, weight, keywords, channel, data, ts) "
             "VALUES(?,?,?,?,?,?,?,?)",
             (by_profile, vid, "deleted", 0, json.dumps(v["keywords"]), v["channel"],
              json.dumps({"reason": reason, "title": v["title"], "bytes": freed}), now()))
    if by_profile is not None and v.get("media", "video") == "video":
        ws = db.q1("SELECT watched FROM watch_state WHERE profile_id=? AND video_id=?", (by_profile, vid))
        if not (ws and ws["watched"]):
            add_event(by_profile, "delete_unwatched", v)
    log.info("deleted %s (%s) reason=%s freed=%d", vid, v["title"], reason, freed)
    return {"id": vid, "reason": reason, "freed": freed}


# ---- events -----------------------------------------------------------------

def add_event(profile_id: int, etype: str, video: dict | None = None,
              keywords: list[str] | None = None, data: dict | None = None) -> None:
    weight = EVENT_WEIGHTS.get(etype, 0.0)
    kws = keywords if keywords is not None else (video or {}).get("keywords", [])
    if isinstance(kws, str):
        kws = json.loads(kws)
    if video is not None and "title" in video:
        data = {"title": video.get("title"), **(data or {})}
    db.x("INSERT INTO events(profile_id, video_id, type, weight, keywords, channel, data, ts) "
         "VALUES(?,?,?,?,?,?,?,?)",
         (profile_id, (video or {}).get("id"), etype, weight, json.dumps(kws),
          (video or {}).get("channel", "") if etype != "search" else "", json.dumps(data or {}), now()))


def log_search(profile_id: int, query: str) -> None:
    kws = text.keywords(query)
    if kws:
        add_event(profile_id, "search", None, keywords=kws, data={"query": query[:200]})


# ---- queue ------------------------------------------------------------------

def _scope_where(scope: str, profile_id: int) -> tuple[str, tuple]:
    if scope == "shared":
        return "scope='shared'", ()
    if scope == "personal":
        return "scope='personal' AND profile_id=?", (profile_id,)
    raise ValueError("scope must be 'shared' or 'personal'")


def queue_add(video_id: str, scope: str, profile_id: int) -> dict:
    get_video(video_id)
    where, args = _scope_where(scope, profile_id)
    owner = profile_id if scope == "personal" else None
    with db.tx():
        existing = db.q1(f"SELECT id FROM queue_items WHERE video_id=? AND {where}", (video_id, *args))
        if existing:
            item_id = existing["id"]
        else:
            pos = db.q1(f"SELECT COALESCE(MAX(position), 0) + 1 AS p FROM queue_items WHERE {where}", args)["p"]
            item_id = db.x(
                "INSERT INTO queue_items(video_id, scope, profile_id, position, added_by, added_at) "
                "VALUES(?,?,?,?,?,?)", (video_id, scope, owner, pos, profile_id, now())).lastrowid
    widen_audience(video_id, profile_id, scope)
    return {"id": item_id, "video_id": video_id, "scope": scope}


def queue_list(scope: str, profile_id: int) -> list[dict]:
    where, args = _scope_where(scope, profile_id)
    items = db.q(
        f"SELECT q.id AS item_id, q.position, q.added_by, q.added_at, v.* FROM queue_items q "
        f"JOIN videos v ON v.id=q.video_id WHERE {where.replace('scope', 'q.scope').replace('profile_id', 'q.profile_id')} "
        f"ORDER BY q.position, q.id", args)
    return [decorate_video(i) for i in items]


def queue_reorder(scope: str, profile_id: int, item_ids: list[int]) -> list[dict]:
    where, args = _scope_where(scope, profile_id)
    current = [r["id"] for r in db.q(f"SELECT id FROM queue_items WHERE {where} ORDER BY position, id", args)]
    wanted = [i for i in item_ids if i in current]
    order = wanted + [i for i in current if i not in wanted]  # unknown ids ignored, missing kept at end
    with db.tx():
        for pos, iid in enumerate(order, start=1):
            db.x("UPDATE queue_items SET position=? WHERE id=?", (pos, iid))
    return queue_list(scope, profile_id)


def queue_move(item_id: int, direction: int, profile_id: int) -> None:
    it = db.q1("SELECT * FROM queue_items WHERE id=?", (item_id,))
    if not it:
        raise NotFound("queue item")
    scope = it["scope"]
    where, args = _scope_where(scope, it["profile_id"] or profile_id)
    ids = [r["id"] for r in db.q(f"SELECT id FROM queue_items WHERE {where} ORDER BY position, id", args)]
    i = ids.index(item_id)
    j = max(0, min(len(ids) - 1, i + direction))
    ids[i], ids[j] = ids[j], ids[i]
    queue_reorder(scope, it["profile_id"] or profile_id, ids)


def queue_remove(item_id: int) -> None:
    if not db.q1("SELECT id FROM queue_items WHERE id=?", (item_id,)):
        raise NotFound("queue item")
    db.x("DELETE FROM queue_items WHERE id=?", (item_id,))


# ---- watch state ------------------------------------------------------------

def get_watch(profile_id: int, vid: str) -> dict:
    ws = db.q1("SELECT * FROM watch_state WHERE profile_id=? AND video_id=?", (profile_id, vid))
    return ws or {"profile_id": profile_id, "video_id": vid, "position": 0, "duration": None,
                  "watched": 0, "rating": 0, "speed": None, "updated_at": None}


def update_progress(profile_id: int, vid: str, position: float, duration: float | None,
                    ended: bool = False) -> dict:
    v = get_video(vid)
    prev = get_watch(profile_id, vid)
    dur = duration or v.get("duration") or prev.get("duration")
    watched = bool(prev["watched"]) or ended or bool(dur and position >= WATCHED_FRACTION * dur)
    db.x(
        "INSERT INTO watch_state(profile_id, video_id, position, duration, watched, rating, updated_at) "
        "VALUES(?,?,?,?,?,0,?) ON CONFLICT(profile_id, video_id) DO UPDATE SET "
        "position=excluded.position, duration=COALESCE(excluded.duration, duration), "
        "watched=MAX(watched, excluded.watched), updated_at=excluded.updated_at",
        (profile_id, vid, max(0.0, position), dur, 1 if watched else 0, now()))
    if watched and not prev["watched"]:
        add_event(profile_id, "finish", v)
        # Done with it personally; shared items stay until everyone has watched.
        db.x("DELETE FROM queue_items WHERE video_id=? AND scope='personal' AND profile_id=?", (vid, profile_id))
    return get_watch(profile_id, vid)


def set_watched(profile_id: int, vid: str, watched: bool) -> dict:
    """The "Mark as watched / unwatched" toggle."""
    v = get_video(vid)
    if watched:
        dur = v.get("duration") or get_watch(profile_id, vid).get("duration") or 0
        return update_progress(profile_id, vid, dur, dur or None, ended=True)
    with db.tx():
        db.x("UPDATE watch_state SET watched=0, position=0, updated_at=? WHERE profile_id=? AND video_id=?",
             (now(), profile_id, vid))
        # An explicit "not watched" corrects the algorithm and withdraws a delete vote.
        db.x("DELETE FROM events WHERE id=(SELECT MAX(id) FROM events WHERE profile_id=? AND video_id=? "
             "AND type='finish')", (profile_id, vid))
        db.x("DELETE FROM delete_votes WHERE profile_id=? AND video_id=?", (profile_id, vid))
    return get_watch(profile_id, vid)


SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)


def set_speed(profile_id: int, vid: str, speed: float) -> dict:
    """Remember the playback rate for this video and person (doesn't count as activity)."""
    get_video(vid)
    speed = round(max(0.25, min(3.0, float(speed))), 2)
    db.x("INSERT INTO watch_state(profile_id, video_id, position, watched, rating, speed, updated_at) "
         "VALUES(?,?,0,0,0,?,?) ON CONFLICT(profile_id, video_id) DO UPDATE SET speed=excluded.speed",
         (profile_id, vid, None if speed == 1.0 else speed, now()))
    return get_watch(profile_id, vid)


def continue_watching(profile_id: int, limit: int = 20) -> list[dict]:
    """Started but not finished, most recent first (per profile)."""
    rows = db.q(
        "SELECT v.* FROM watch_state ws JOIN videos v ON v.id=ws.video_id "
        "WHERE ws.profile_id=? AND ws.watched=0 AND ws.position>=5 AND v.status='ready' "
        "AND v.media='video' AND v.is_short=0 AND (v.duration IS NULL OR ws.position < v.duration - 5) "
        "ORDER BY ws.updated_at DESC LIMIT ?", (profile_id, limit))
    return [decorate_video(r) for r in rows]


def mark_skip(profile_id: int, vid: str) -> None:
    """User bailed within 2 seconds. Counts as 'seen' for Shorts, -1 for the algorithm."""
    v = get_video(vid)
    add_event(profile_id, "skip", v)
    if v["is_short"]:
        update_progress(profile_id, vid, 0, v.get("duration"), ended=True)
        # update_progress logged a 'finish'; a skip is not a finish, so undo that.
        db.x("DELETE FROM events WHERE id=(SELECT MAX(id) FROM events WHERE profile_id=? AND video_id=? "
             "AND type='finish')", (profile_id, vid))


def rate(profile_id: int, vid: str, value: int) -> dict:
    """value: 1 like, -1 dislike, 0 clear. Replaces any earlier like/dislike event."""
    v = get_video(vid)
    value = max(-1, min(1, int(value)))
    get_watch(profile_id, vid)
    db.x("INSERT INTO watch_state(profile_id, video_id, position, watched, rating, updated_at) "
         "VALUES(?,?,0,0,?,?) ON CONFLICT(profile_id, video_id) DO UPDATE SET rating=excluded.rating",
         (profile_id, vid, value, now()))
    db.x("DELETE FROM events WHERE profile_id=? AND video_id=? AND type IN ('like','dislike')", (profile_id, vid))
    if value:
        add_event(profile_id, "like" if value > 0 else "dislike", v)
    return get_watch(profile_id, vid)


def rate_candidate(profile_id: int, meta: dict, value: int) -> None:
    """Like/dislike something that was never downloaded (e.g. 'Not interested' in For you)."""
    upsert_video(meta)
    rate(profile_id, meta["id"], value)


# ---- delete after watching ----------------------------------------------------

def delete_status(vid: str) -> dict:
    """Who still has to finish (or tap delete) before a video is removed.

    A video that is only for one person is theirs to delete right away; a
    shared one goes once every relevant profile has watched it or voted."""
    rel = relevant_profiles(vid)
    votes = {r["profile_id"] for r in db.q("SELECT profile_id FROM delete_votes WHERE video_id=?", (vid,))}
    watched = {r["profile_id"] for r in db.q(
        "SELECT profile_id FROM watch_state WHERE video_id=? AND watched=1", (vid,))}
    state = {p: "voted" if p in votes else "watched" if p in watched else "pending" for p in rel}
    return {"video_id": vid, "relevant": rel, "state": {str(p): s for p, s in state.items()},
            "pending": [p for p, s in state.items() if s == "pending"], "votes": sorted(votes & set(rel)),
            "solo": len(rel) == 1}


def vote_delete(profile_id: int, vid: str, library_dir: Path, thumbs_dir: Path) -> dict:
    v = get_video(vid)
    if v["status"] != "ready":
        raise NotFound(f"video {vid}")
    if profile_id not in relevant_profiles(vid):
        owner = ", ".join(get_profile(p)["name"] for p in relevant_profiles(vid))
        raise ValueError(f"This one is {owner}'s video, so it's theirs to delete.")
    db.x("INSERT OR IGNORE INTO delete_votes(video_id, profile_id, ts) VALUES(?,?,?)", (vid, profile_id, now()))
    st = delete_status(vid)
    if not st["pending"]:
        reason = "deleted after watching" if st["solo"] else "both done with it"
        delete_video(vid, reason, profile_id, library_dir, thumbs_dir)
        return {**st, "deleted": True}
    return {**st, "deleted": False}


def unvote_delete(profile_id: int, vid: str) -> dict:
    db.x("DELETE FROM delete_votes WHERE video_id=? AND profile_id=?", (vid, profile_id))
    return {**delete_status(vid), "deleted": False}


# ---- captions -------------------------------------------------------------------

def _sub_view(r: dict) -> dict:
    return {"id": r["id"], "video_id": r["video_id"], "lang": r["lang"], "label": r["label"],
            "kind": r["kind"], "url": f"/api/subtitles/{r['id']}.vtt"}


def save_subtitle(vid: str, lang: str, kind: str, raw: str, subs_dir: Path, label: str | None = None) -> dict:
    """Normalise SRT/VTT to clean WebVTT on disk. YouTube tracks replace an older
    copy of the same language; uploads are always added."""
    cues = subtitles.normalize(raw)
    lang = (re.sub(r"[^A-Za-z0-9-]", "", lang or "und")[:12] or "und").lower()
    label = (label or subtitles.lang_label(lang, kind)).strip()[:40] or lang
    subs_dir.mkdir(parents=True, exist_ok=True)
    if kind != "uploaded":
        for old in db.q("SELECT id, path FROM subtitles WHERE video_id=? AND lang=? AND kind=?", (vid, lang, kind)):
            Path(old["path"]).unlink(missing_ok=True)
            db.x("DELETE FROM subtitles WHERE id=?", (old["id"],))
    sid = db.x("INSERT INTO subtitles(video_id, lang, label, kind, path, created_at) VALUES(?,?,?,?,?,?)",
               (vid, lang, label, kind, "", now())).lastrowid
    path = subs_dir / f"{vid}.{sid}.{lang}.vtt"
    path.write_text(subtitles.to_vtt(cues), encoding="utf-8")
    db.x("UPDATE subtitles SET path=? WHERE id=?", (str(path), sid))
    return _sub_view(db.q1("SELECT * FROM subtitles WHERE id=?", (sid,)))


def save_downloaded_subtitles(vid: str, tracks: list[dict], subs_dir: Path) -> int:
    n = 0
    for t in tracks or []:
        try:
            save_subtitle(vid, t["lang"], t["kind"], t["text"], subs_dir)
            n += 1
        except Exception as e:
            log.info("captions %s for %s unusable: %s", t.get("lang"), vid, e)
    return n


def list_subtitles(vid: str) -> list[dict]:
    rows = db.q("SELECT * FROM subtitles WHERE video_id=? ORDER BY CASE kind WHEN 'uploaded' THEN 0 "
                "WHEN 'manual' THEN 1 ELSE 2 END, id", (vid,))
    return [_sub_view(r) for r in rows if Path(r["path"]).is_file()]


def get_subtitle(sid: int) -> dict:
    r = db.q1("SELECT * FROM subtitles WHERE id=?", (sid,))
    if not r or not Path(r["path"]).is_file():
        raise NotFound(f"subtitle {sid}")
    return r


def delete_subtitle(sid: int) -> None:
    r = get_subtitle(sid)
    Path(r["path"]).unlink(missing_ok=True)
    db.x("DELETE FROM subtitles WHERE id=?", (sid,))


def transcript(sid: int) -> dict:
    r = get_subtitle(sid)
    cues = subtitles.parse(Path(r["path"]).read_text(encoding="utf-8"))
    return {**_sub_view(r), "cues": cues}


# ---- playlists --------------------------------------------------------------------

def _visible(pl: dict | None, profile_id: int) -> dict:
    if not pl or (pl["scope"] == "personal" and pl["profile_id"] != profile_id):
        raise NotFound("playlist")
    return pl


def _playlist_summary(pl: dict) -> dict:
    agg = db.q1("SELECT COUNT(*) n, COALESCE(SUM(v.duration), 0) dur FROM playlist_items i "
                "JOIN videos v ON v.id=i.video_id WHERE i.playlist_id=?", (pl["id"],))
    covers = [r["video_id"] for r in db.q("SELECT video_id FROM playlist_items WHERE playlist_id=? "
                                           "ORDER BY position, id LIMIT 4", (pl["id"],))]
    return {**pl, "count": agg["n"], "duration": agg["dur"], "thumbs": [f"/api/thumb/{c}" for c in covers]}


def playlists(profile_id: int) -> list[dict]:
    rows = db.q("SELECT * FROM playlists WHERE scope='shared' OR (scope='personal' AND profile_id=?) "
                "ORDER BY scope DESC, updated_at DESC", (profile_id,))
    return [_playlist_summary(r) for r in rows]


def get_playlist(plid: int, profile_id: int) -> dict:
    pl = _visible(db.q1("SELECT * FROM playlists WHERE id=?", (plid,)), profile_id)
    items = db.q("SELECT i.id AS item_id, i.position, i.added_by, i.added_at, v.* FROM playlist_items i "
                 "JOIN videos v ON v.id=i.video_id WHERE i.playlist_id=? ORDER BY i.position, i.id", (plid,))
    return {**_playlist_summary(pl), "items": [decorate_video(i) for i in items]}


def _clean_name(name: str) -> str:
    name = (name or "").strip()[:60]
    if not name:
        raise ValueError("Give the playlist a name.")
    return name


def create_playlist(name: str, scope: str, profile_id: int) -> dict:
    if scope not in ("shared", "personal"):
        raise ValueError("scope must be 'shared' or 'personal'")
    t = now()
    plid = db.x("INSERT INTO playlists(name, scope, profile_id, created_by, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?)", (_clean_name(name), scope, profile_id if scope == "personal" else None,
                                        profile_id, t, t)).lastrowid
    return get_playlist(plid, profile_id)


def rename_playlist(plid: int, name: str, profile_id: int) -> dict:
    _visible(db.q1("SELECT * FROM playlists WHERE id=?", (plid,)), profile_id)
    db.x("UPDATE playlists SET name=?, updated_at=? WHERE id=?", (_clean_name(name), now(), plid))
    return get_playlist(plid, profile_id)


def delete_playlist(plid: int, profile_id: int) -> None:
    _visible(db.q1("SELECT * FROM playlists WHERE id=?", (plid,)), profile_id)
    with db.tx():
        db.x("DELETE FROM playlist_items WHERE playlist_id=?", (plid,))
        db.x("DELETE FROM playlists WHERE id=?", (plid,))


def playlist_add(plid: int, vid: str, profile_id: int) -> dict:
    pl = _visible(db.q1("SELECT * FROM playlists WHERE id=?", (plid,)), profile_id)
    v = get_video(vid)
    if v.get("media") != "video" or v["status"] not in ("ready", "queued", "downloading"):
        raise ValueError("Only videos in the library can go in a playlist.")
    with db.tx():
        if not db.q1("SELECT 1 FROM playlist_items WHERE playlist_id=? AND video_id=?", (plid, vid)):
            pos = db.q1("SELECT COALESCE(MAX(position), 0) + 1 p FROM playlist_items WHERE playlist_id=?",
                        (plid,))["p"]
            db.x("INSERT INTO playlist_items(playlist_id, video_id, position, added_by, added_at) "
                 "VALUES(?,?,?,?,?)", (plid, vid, pos, profile_id, now()))
        db.x("UPDATE playlists SET updated_at=? WHERE id=?", (now(), plid))
    # A shared playlist makes the video everyone's (for the delete-after-watching vote).
    widen_audience(vid, profile_id, pl["scope"])
    return get_playlist(plid, profile_id)


def playlist_remove(plid: int, item_id: int, profile_id: int) -> dict:
    _visible(db.q1("SELECT * FROM playlists WHERE id=?", (plid,)), profile_id)
    if not db.x("DELETE FROM playlist_items WHERE id=? AND playlist_id=?", (item_id, plid)).rowcount:
        raise NotFound("playlist item")
    db.x("UPDATE playlists SET updated_at=? WHERE id=?", (now(), plid))
    return get_playlist(plid, profile_id)


def playlist_reorder(plid: int, item_ids: list[int], profile_id: int) -> dict:
    _visible(db.q1("SELECT * FROM playlists WHERE id=?", (plid,)), profile_id)
    current = [r["id"] for r in db.q("SELECT id FROM playlist_items WHERE playlist_id=? ORDER BY position, id",
                                     (plid,))]
    wanted = [i for i in item_ids if i in current]
    order = wanted + [i for i in current if i not in wanted]
    with db.tx():
        for pos, iid in enumerate(order, start=1):
            db.x("UPDATE playlist_items SET position=? WHERE id=?", (pos, iid))
    return get_playlist(plid, profile_id)


def playlists_containing(vid: str, profile_id: int) -> list[int]:
    return [r["playlist_id"] for r in db.q(
        "SELECT i.playlist_id FROM playlist_items i JOIN playlists p ON p.id=i.playlist_id WHERE i.video_id=? "
        "AND (p.scope='shared' OR p.profile_id=?)", (vid, profile_id))]


def in_any_playlist(vid: str) -> bool:
    return bool(db.q1("SELECT 1 FROM playlist_items WHERE video_id=? LIMIT 1", (vid,)))


# ---- presentation -----------------------------------------------------------

def decorate_video(v: dict) -> dict:
    """Attach watch state for every profile and the latest download row."""
    from .formats import needs_vlc

    if isinstance(v.get("keywords"), str):
        v["keywords"] = json.loads(v["keywords"] or "[]")
    vid = v["id"]
    v["watch"] = {str(r["profile_id"]): {"position": r["position"], "watched": bool(r["watched"]),
                                         "rating": r["rating"], "speed": r.get("speed")}
                  for r in db.q("SELECT * FROM watch_state WHERE video_id=?", (vid,))}
    v["delete_votes"] = [r["profile_id"] for r in db.q("SELECT profile_id FROM delete_votes WHERE video_id=?", (vid,))]
    v["in_playlist"] = in_any_playlist(vid)
    d = db.q1("SELECT id, status, progress, speed, eta, phase, error FROM downloads WHERE video_id=? "
              "ORDER BY id DESC LIMIT 1", (vid,))
    v["download"] = d
    v["needs_vlc"] = needs_vlc(v.get("vcodec"))
    v["relevant"] = relevant_profiles(vid) if v.get("status") == "ready" else []
    v["thumb"] = f"/api/thumb/{vid}"
    v["stream"] = f"/api/stream/{vid}" if v.get("status") == "ready" else None
    if v.get("media") == "audio":
        v["file"] = f"/api/music/{vid}/file" if v.get("status") == "ready" else None
        v["format"] = v.get("quality")
    v.pop("file_path", None)
    return v


def library(include_shorts: bool = False) -> list[dict]:
    rows = db.q("SELECT * FROM videos WHERE status IN ('ready','downloading','queued') "
                + "AND media='video' " + ("" if include_shorts else "AND is_short=0 ")
                + "ORDER BY COALESCE(downloaded_at, created_at) DESC")
    return [decorate_video(r) for r in rows]


# ---- music ------------------------------------------------------------------

def music_list() -> list[dict]:
    """Downloaded (or downloading) songs, newest first. Music is never auto-deleted."""
    rows = db.q("SELECT * FROM videos WHERE media='audio' AND status IN ('ready','downloading','queued','error') "
                "ORDER BY COALESCE(downloaded_at, created_at) DESC")
    return [decorate_video(r) for r in rows]


def download_name(v: dict) -> str:
    """'Artist - Title.mp3' with characters iOS/Files dislike removed."""
    import re

    ext = Path(v["file_path"]).suffix if v.get("file_path") else ".mp3"
    base = f"{v['channel']} - {v['title']}" if v.get("channel") else v["title"]
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", base).strip()[:120] or v["id"]
    return base + ext
