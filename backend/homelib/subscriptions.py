"""Channel subscriptions (per profile) and their latest uploads.

Checking a channel is one yt-dlp flat extraction of its Videos tab. Only the
metadata and a 320px thumbnail are stored (`channel_uploads`, data/thumbs/);
nothing is downloaded until someone taps a video. Each channel keeps its
newest KEEP_PER_CHANNEL uploads; older rows and their thumbnails are pruned.
Uploads are stored once per channel and shown only to the profiles that
subscribe to it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import db, library
from .db import now

log = logging.getLogger("homelib.subscriptions")

PER_CHECK = 15            # uploads asked for per channel check
KEEP_PER_CHANNEL = 30
NEW_DAYS = 7              # an upload counts as "new" for this long
RECHECK_AFTER = 20 * 60   # a manual reload doesn't re-check a channel checked this recently
THUMBS_PER_CHECK = 8


def is_channel_id(cid: str) -> bool:
    return bool(cid) and cid.startswith("UC") and len(cid) == 24


def subscribe(profile_id: int, channel_id: str, channel: str) -> dict:
    if not is_channel_id(channel_id):
        raise ValueError("That channel can't be subscribed to (no channel id).")
    channel = (channel or "").strip()[:120] or channel_id
    library.get_profile(profile_id)
    fresh = not db.q1("SELECT 1 FROM subscriptions WHERE profile_id=? AND channel_id=?", (profile_id, channel_id))
    db.x("INSERT INTO subscriptions(profile_id, channel_id, channel, created_at) VALUES(?,?,?,?) "
         "ON CONFLICT(profile_id, channel_id) DO UPDATE SET channel=excluded.channel",
         (profile_id, channel_id, channel, now()))
    if fresh:
        library.add_event(profile_id, "subscribe", {"id": None, "channel": channel, "keywords": []},
                          data={"channel_id": channel_id, "title": channel})
        # What's already there isn't "new" for you.
        db.x("INSERT OR IGNORE INTO upload_seen(profile_id, video_id, ts) "
             "SELECT ?, video_id, ? FROM channel_uploads WHERE channel_id=?", (profile_id, now(), channel_id))
    return get(profile_id, channel_id)


def unsubscribe(profile_id: int, channel_id: str) -> None:
    sub = db.q1("SELECT channel FROM subscriptions WHERE profile_id=? AND channel_id=?", (profile_id, channel_id))
    if not sub:
        raise library.NotFound("subscription")
    with db.tx():
        db.x("DELETE FROM subscriptions WHERE profile_id=? AND channel_id=?", (profile_id, channel_id))
        db.x("DELETE FROM events WHERE profile_id=? AND type='subscribe' AND channel=?", (profile_id, sub["channel"]))


def get(profile_id: int, channel_id: str) -> dict:
    s = db.q1("SELECT * FROM subscriptions WHERE profile_id=? AND channel_id=?", (profile_id, channel_id))
    if not s:
        raise library.NotFound("subscription")
    return s


def channel_ids(profile_id: int) -> set[str]:
    return {r["channel_id"] for r in db.q("SELECT channel_id FROM subscriptions WHERE profile_id=?", (profile_id,))}


def subscribed_names(profile_id: int) -> set[str]:
    return {r["channel"] for r in db.q("SELECT channel FROM subscriptions WHERE profile_id=?", (profile_id,))}


def _upload_view(r: dict, seen: set[str], watched: set[str], local: dict[str, str]) -> dict:
    m = json.loads(r["meta"])
    ts = r["upload_ts"] or r["first_seen"]
    m.update(upload_ts=r["upload_ts"], first_seen=r["first_seen"], thumb=f"/api/thumb/{m['id']}",
             new=m["id"] not in seen and now() - ts <= NEW_DAYS * 86400 and m["id"] not in watched,
             watched=m["id"] in watched, local_status=local.get(m["id"]))
    return m


def uploads_for(profile_id: int, channel_id: str | None = None, limit_per_channel: int = 12) -> list[dict]:
    """Subscriptions with their latest uploads (the Subscriptions tab)."""
    subs = db.q("SELECT * FROM subscriptions WHERE profile_id=? " + ("AND channel_id=? " if channel_id else "")
                + "ORDER BY channel COLLATE NOCASE", (profile_id, channel_id) if channel_id else (profile_id,))
    seen = {r["video_id"] for r in db.q("SELECT video_id FROM upload_seen WHERE profile_id=?", (profile_id,))}
    watched = {r["video_id"] for r in db.q("SELECT video_id FROM watch_state WHERE profile_id=? AND watched=1",
                                           (profile_id,))}
    out = []
    for s in subs:
        rows = db.q("SELECT * FROM channel_uploads WHERE channel_id=? ORDER BY COALESCE(upload_ts, first_seen) DESC "
                    "LIMIT ?", (s["channel_id"], limit_per_channel))
        ids = [json.loads(r["meta"])["id"] for r in rows]
        local = {}
        if ids:
            local = {v["id"]: v["status"] for v in db.q(
                f"SELECT id, status FROM videos WHERE id IN ({','.join('?' * len(ids))})", ids)
                if v["status"] in ("ready", "queued", "downloading") and library.visible_to(v["id"], profile_id)}
        chk = db.q1("SELECT checked_at, ok, error FROM channel_checks WHERE channel_id=?", (s["channel_id"],))
        uploads = [_upload_view(r, seen, watched, local) for r in rows]
        out.append({"channel_id": s["channel_id"], "channel": s["channel"], "subscribed_at": s["created_at"],
                    "checked_at": chk["checked_at"] if chk else None, "check_ok": bool(chk["ok"]) if chk else None,
                    "new_count": sum(1 for u in uploads if u["new"]), "uploads": uploads})
    out.sort(key=lambda c: -max([u["upload_ts"] or u["first_seen"] for u in c["uploads"]] or [0]))
    return out


def new_uploads(profile_id: int, days: int = NEW_DAYS) -> list[dict]:
    """Recent uploads from this profile's subscriptions it hasn't watched (for the feed)."""
    t = now()
    rows = db.q("SELECT u.*, s.channel AS sub_channel FROM channel_uploads u JOIN subscriptions s "
                "ON s.channel_id=u.channel_id AND s.profile_id=? WHERE COALESCE(u.upload_ts, u.first_seen) >= ? "
                "ORDER BY COALESCE(u.upload_ts, u.first_seen) DESC", (profile_id, t - days * 86400))
    out = []
    for r in rows:
        m = json.loads(r["meta"])
        m["upload_ts"] = r["upload_ts"] or r["first_seen"]
        out.append(m)
    return out


def mark_seen(profile_id: int, video_ids: list[str]) -> int:
    t = now()
    with db.tx():
        for vid in video_ids[:500]:
            db.x("INSERT OR IGNORE INTO upload_seen(profile_id, video_id, ts) VALUES(?,?,?)", (profile_id, vid, t))
    return len(video_ids)


def new_count(profile_id: int) -> int:
    return sum(c["new_count"] for c in uploads_for(profile_id))


# ---- checking channels ------------------------------------------------------------

def store_uploads(channel_id: str, results: list[dict]) -> list[str]:
    """Save a channel check. Returns the ids that are new to us."""
    t = now()
    added = []
    with db.tx():
        for r in results:
            if r.get("is_short"):
                continue
            meta = {k: r.get(k) for k in ("id", "title", "channel", "channel_id", "duration", "url", "thumb_url",
                                          "is_short", "view_count", "description")}
            meta["channel_id"] = channel_id
            exists = db.q1("SELECT 1 FROM channel_uploads WHERE channel_id=? AND video_id=?", (channel_id, r["id"]))
            db.x("INSERT INTO channel_uploads(channel_id, video_id, meta, upload_ts, first_seen) VALUES(?,?,?,?,?) "
                 "ON CONFLICT(channel_id, video_id) DO UPDATE SET meta=excluded.meta, "
                 "upload_ts=COALESCE(excluded.upload_ts, upload_ts)",
                 (channel_id, r["id"], json.dumps(meta), r.get("upload_ts"), t))
            if not exists:
                added.append(r["id"])
    return added


def prune(channel_id: str, thumbs_dir: Path | None) -> int:
    rows = db.q("SELECT video_id FROM channel_uploads WHERE channel_id=? ORDER BY COALESCE(upload_ts, first_seen) "
                "DESC LIMIT -1 OFFSET ?", (channel_id, KEEP_PER_CHANNEL))
    for r in rows:
        db.x("DELETE FROM channel_uploads WHERE channel_id=? AND video_id=?", (channel_id, r["video_id"]))
        on_server = db.q1("SELECT 1 FROM videos WHERE id=? AND status IN ('ready','queued','downloading')",
                          (r["video_id"],))
        if thumbs_dir and not on_server:
            for ext in ("jpg", "svg"):
                (thumbs_dir / f"{r['video_id']}.{ext}").unlink(missing_ok=True)
    return len(rows)


def refresh(yt, profile_ids: list[int] | None = None, budget=None, thumbs_dir: Path | None = None,
            force: bool = False) -> dict:
    """Check the subscribed channels of these profiles for new uploads.

    Each channel is checked once even if both profiles subscribe. budget
    (recommender.Budget) limits and paces the yt-dlp requests. A failed
    channel keeps its previous uploads."""
    pids = profile_ids or library.profile_ids()
    ph = ",".join("?" * len(pids))
    chans = db.q(f"SELECT channel_id, MIN(channel) channel FROM subscriptions WHERE profile_id IN ({ph}) "
                 "GROUP BY channel_id", pids)
    checked = {r["channel_id"]: r["checked_at"] for r in db.q("SELECT channel_id, checked_at FROM channel_checks")}
    # Least recently checked first, so a small budget still gets round to every channel.
    chans.sort(key=lambda c: checked.get(c["channel_id"], 0))
    stats = {"channels": len(chans), "checked": 0, "failed": 0, "new": 0, "skipped": 0}
    for c in chans:
        cid = c["channel_id"]
        if not force and now() - checked.get(cid, 0) < RECHECK_AFTER:
            stats["skipped"] += 1
            continue
        if budget is not None and not budget.take():
            stats["skipped"] += 1
            continue
        try:
            results = yt.channel_uploads(cid, PER_CHECK, c["channel"])
        except Exception as e:
            stats["failed"] += 1
            log.info("channel check %s (%s) failed: %s", c["channel"], cid, e)
            db.x("INSERT INTO channel_checks(channel_id, checked_at, ok, error) VALUES(?,?,0,?) "
                 "ON CONFLICT(channel_id) DO UPDATE SET checked_at=excluded.checked_at, ok=0, error=excluded.error",
                 (cid, now(), str(e)[:300]))
            continue
        added = store_uploads(cid, results)
        stats["checked"] += 1
        stats["new"] += len(added)
        db.x("INSERT INTO channel_checks(channel_id, checked_at, ok, error) VALUES(?,?,1,NULL) "
             "ON CONFLICT(channel_id) DO UPDATE SET checked_at=excluded.checked_at, ok=1, error=NULL", (cid, now()))
        if thumbs_dir is not None:
            for r in [r for r in results if r["id"] in added][:THUMBS_PER_CHECK]:
                try:
                    yt.thumbnail(r["id"], r.get("thumb_url") or "", thumbs_dir)
                except Exception as e:
                    log.info("thumbnail for %s failed: %s", r["id"], e)
        prune(cid, thumbs_dir)
    log.info("subscriptions: %s", stats)
    return stats
