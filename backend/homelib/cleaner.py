"""Auto-delete.

A downloaded video (or Short) is deleted when EITHER
  * every relevant profile has watched it, or
  * it is older than N hours (12/24/48, default 24),
UNLESS its Keep flag is set or it is in a playlist (a playlist is a deliberate
collection). A "delete after watching" vote counts like having watched it.
As a safety margin nothing is deleted while
someone touched it in the last few minutes (e.g. paused mid-way). Every
deletion is written to the events table (type 'deleted') and the log.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import db, library
from .ytlib import youtube_id
from .db import now

log = logging.getLogger("homelib.cleaner")

ACTIVE_GRACE_SECONDS = 10 * 60


def decide(video: dict, watch_states: dict[int, dict], relevant: list[int], now_ts: float,
           max_age_hours: int, votes: set[int] | frozenset = frozenset(),
           in_playlist: bool = False) -> tuple[bool, str]:
    """Pure decision function. Returns (delete?, reason)."""
    if video.get("status") != "ready":
        return False, "not ready"
    if video.get("keep"):
        return False, "keep flag"
    if in_playlist:
        return False, "in a playlist"
    last_touch = max((w.get("updated_at") or 0 for w in watch_states.values()), default=0)
    if now_ts - last_touch < ACTIVE_GRACE_SECONDS:
        return False, "recently active"
    if relevant and all(watch_states.get(p, {}).get("watched") or p in votes for p in relevant):
        return True, "watched by " + ("everyone" if len(relevant) > 1 else f"profile {relevant[0]}")
    age_h = (now_ts - (video.get("downloaded_at") or video.get("created_at") or now_ts)) / 3600
    if age_h >= max_age_hours:
        return True, f"older than {max_age_hours}h"
    return False, "kept for now"


def expires_at(video: dict, max_age_hours: int) -> float | None:
    if video.get("keep") or video.get("in_playlist") or not video.get("downloaded_at"):
        return None
    return video["downloaded_at"] + max_age_hours * 3600


def run(library_dir: Path, thumbs_dir: Path, max_age_hours: int) -> list[dict]:
    deleted = []
    allp = library.profile_ids()
    t = now()
    # Music (media='audio') is a keep-forever collection: only manual deletes.
    for v in db.q("SELECT * FROM videos WHERE status='ready' AND media='video'"):
        ws = {r["profile_id"]: r for r in db.q("SELECT * FROM watch_state WHERE video_id=?", (v["id"],))}
        rel = library.relevant_profiles(v["id"], allp)
        votes = {r["profile_id"] for r in db.q("SELECT profile_id FROM delete_votes WHERE video_id=?", (v["id"],))}
        ok, reason = decide(v, ws, rel, t, max_age_hours, votes, library.in_any_playlist(v["id"]))
        if ok:
            deleted.append(library.delete_video(v["id"], "auto: " + reason, None, library_dir, thumbs_dir))
    # Leftover partial files from cancelled downloads.
    for part_dir in (library_dir / ".partial", library_dir / "music" / ".partial"):
        if not part_dir.is_dir():
            continue
        active = {youtube_id(r["video_id"]) for r in db.q(
            "SELECT video_id FROM downloads WHERE status IN ('queued','running','paused')")}
        for f in part_dir.iterdir():
            if f.is_file() and not any(f.name.startswith(a) for a in active) and t - f.stat().st_mtime > 3600:
                f.unlink(missing_ok=True)
    if deleted:
        log.info("cleaner removed %d video(s)", len(deleted))
    return deleted
