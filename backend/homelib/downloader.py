"""Download queue and worker threads.

Two small pools keep memory and CPU predictable on a 4 GB box:
  * foreground: user-requested downloads (default 1 thread)
  * background: Shorts prefetch (default 1 thread, niced)
The total never exceeds config.max_concurrent.

Pause/cancel work by setting a flag the yt-dlp progress hook checks; the hook
raises and yt-dlp stops. Paused downloads keep their .part file so resume
continues where it left off.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from . import db, library
from .db import now
from .formats import is_audio
from .shorts import is_short
from .tools import set_thread_nice
from .ytlib import Stopped, youtube_id

log = logging.getLogger("homelib.downloader")

ACTIVE = ("queued", "running", "paused")


class DownloadManager:
    def __init__(self, cfg, yt):
        self.cfg = cfg
        self.yt = yt
        self._wake = threading.Condition()
        self._control: dict[int, str] = {}  # download id -> 'pause' | 'cancel'
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ---- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        # Crash recovery: anything that was mid-download goes back to the queue.
        db.x("UPDATE downloads SET status='queued' WHERE status='running'")
        for i in range(self.cfg.foreground_workers):
            self._spawn(f"dl-fg-{i}", ("user", "background"), nice=0)
        for i in range(self.cfg.background_workers):
            self._spawn(f"dl-bg-{i}", ("background",), nice=10)

    def _spawn(self, name: str, kinds: tuple[str, ...], nice: int) -> None:
        t = threading.Thread(target=self._worker, args=(kinds, nice), name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for did in [r["id"] for r in db.q("SELECT id FROM downloads WHERE status='running'")]:
            self._control[did] = "pause"
        with self._wake:
            self._wake.notify_all()

    def wake(self) -> None:
        with self._wake:
            self._wake.notify_all()

    # ---- public API -----------------------------------------------------------
    def enqueue(self, meta: dict, quality: str, kind: str = "user", requested_by: int | None = None,
                queue_scope: str | None = None) -> dict:
        """Create (or reuse) a download for a video and optionally queue it.

        queue_scope: 'personal', 'shared' or None. It also decides who must
        watch the video before auto-delete removes it.
        """
        if quality not in ("best", "compat", "mp3", "m4a"):
            raise ValueError("quality must be 'best', 'compat', 'mp3' or 'm4a'")
        library.ensure_space(self.cfg.library_dir, self.cfg.min_free_gb)
        vid = meta["id"]
        existing_video = db.q1("SELECT status FROM videos WHERE id=?", (vid,))
        v = library.upsert_video(meta, added_by=requested_by)
        fresh = not existing_video or existing_video["status"] in ("new", "deleted", "error")
        if fresh:
            audience = f"p:{requested_by}" if (queue_scope == "personal" and requested_by) else "all"
            library.set_audience(vid, audience)
        if queue_scope in ("personal", "shared") and requested_by:
            library.queue_add(vid, queue_scope, requested_by)
        if v["status"] == "ready":
            return self.get_for_video(vid)
        active = db.q1(f"SELECT * FROM downloads WHERE video_id=? AND status IN {ACTIVE} ORDER BY id DESC",
                       (vid,))
        if active:
            if active["kind"] == "background" and kind == "user":
                db.x("UPDATE downloads SET kind='user', quality=? WHERE id=? AND status<>'running'",
                     (quality, active["id"]))
            if active["status"] == "paused" and kind == "user":
                self.resume(active["id"])
            return self.get(active["id"])
        with db.tx():
            did = db.x("INSERT INTO downloads(video_id, quality, kind, status, requested_by, created_at) "
                       "VALUES(?,?,?,?,?,?)", (vid, quality, kind, "queued", requested_by, now())).lastrowid
            db.x("UPDATE videos SET status='queued', quality=?, deleted_at=NULL, delete_reason=NULL WHERE id=?",
                 (quality, vid))
        self.wake()
        return self.get(did)

    def get(self, did: int) -> dict:
        d = db.q1("SELECT d.*, v.title, v.channel, v.is_short, v.duration FROM downloads d "
                  "JOIN videos v ON v.id=d.video_id WHERE d.id=?", (did,))
        if not d:
            raise library.NotFound(f"download {did}")
        d["thumb"] = f"/api/thumb/{d['video_id']}"
        return d

    def get_for_video(self, vid: str) -> dict | None:
        r = db.q1("SELECT id FROM downloads WHERE video_id=? ORDER BY id DESC LIMIT 1", (vid,))
        return self.get(r["id"]) if r else None

    def list(self, include_background: bool = True, limit: int = 100) -> list[dict]:
        where = "" if include_background else "WHERE d.kind='user'"
        rows = db.q(
            "SELECT d.*, v.title, v.channel, v.is_short, v.duration FROM downloads d JOIN videos v "
            f"ON v.id=d.video_id {where} ORDER BY CASE d.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 "
            "WHEN 'paused' THEN 2 ELSE 3 END, d.id DESC LIMIT ?", (limit,))
        for r in rows:
            r["thumb"] = f"/api/thumb/{r['video_id']}"
        return rows

    def pause(self, did: int) -> dict:
        d = self.get(did)
        if d["status"] == "running":
            self._control[did] = "pause"
        elif d["status"] == "queued":
            db.x("UPDATE downloads SET status='paused' WHERE id=?", (did,))
        return self.get(did)

    def resume(self, did: int) -> dict:
        d = self.get(did)
        if d["status"] in ("paused", "error"):
            library.ensure_space(self.cfg.library_dir, self.cfg.min_free_gb)
            db.x("UPDATE downloads SET status='queued', error=NULL WHERE id=?", (did,))
            db.x("UPDATE videos SET status='queued' WHERE id=?", (d["video_id"],))
            self.wake()
        return self.get(did)

    def cancel(self, did: int) -> dict:
        d = self.get(did)
        if d["status"] == "running":
            self._control[did] = "cancel"
        elif d["status"] in ("queued", "paused", "error"):
            self._finish_cancel(did, d["video_id"])
        return self.get(did)

    def clear_finished(self) -> int:
        return db.x("DELETE FROM downloads WHERE status IN ('done','cancelled','error')").rowcount

    def counts(self) -> dict:
        return {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM downloads GROUP BY status")}

    # ---- worker ---------------------------------------------------------------
    def _claim(self, kinds: tuple[str, ...]) -> dict | None:
        placeholders = ",".join("?" * len(kinds))
        with db.tx():
            d = db.q1(f"SELECT * FROM downloads WHERE status='queued' AND kind IN ({placeholders}) "
                      "ORDER BY CASE kind WHEN 'user' THEN 0 ELSE 1 END, id LIMIT 1", kinds)
            if d:
                db.x("UPDATE downloads SET status='running', started_at=?, error=NULL WHERE id=?", (now(), d["id"]))
                db.x("UPDATE videos SET status='downloading' WHERE id=?", (d["video_id"],))
        return d

    def _worker(self, kinds: tuple[str, ...], nice: int) -> None:
        if nice:
            set_thread_nice(nice)
        while not self._stop.is_set():
            d = None
            try:
                d = self._claim(kinds)
            except Exception:
                log.exception("claim failed")
            if not d:
                with self._wake:
                    self._wake.wait(timeout=5)
                continue
            self._run(d)

    def _run(self, d: dict) -> None:
        did, vid = d["id"], d["video_id"]
        v = library.get_video(vid)
        last_write = [0.0]

        def progress(p: dict) -> None:
            t = time.monotonic()
            if t - last_write[0] < 0.5 and p.get("phase") != "merging":
                return  # throttle DB writes
            last_write[0] = t
            pct = p.get("percent")
            if pct is None and p.get("total_bytes"):
                pct = 100.0 * (p.get("downloaded_bytes") or 0) / p["total_bytes"]
            db.x("UPDATE downloads SET progress=COALESCE(?, progress), speed=?, eta=?, downloaded_bytes=?, "
                 "total_bytes=COALESCE(?, total_bytes), phase=? WHERE id=?",
                 (min(pct, 99.9) if pct is not None else None, p.get("speed"), p.get("eta"),
                  p.get("downloaded_bytes"), p.get("total_bytes"), p.get("phase"), did))

        def should_stop() -> str | None:
            if self._stop.is_set():
                return "pause"
            return self._control.get(did)

        try:
            library.ensure_space(self.cfg.library_dir, self.cfg.min_free_gb)
            audio = is_audio(d["quality"])
            out_dir = self.cfg.library_dir / "music" if audio else self.cfg.library_dir
            res = self.yt.download(vid, v["url"], d["quality"], bool(v["is_short"]),
                                   out_dir, progress, should_stop)
        except Stopped as s:
            self._control.pop(did, None)
            if s.what == "cancel":
                self._finish_cancel(did, vid)
            else:
                db.x("UPDATE downloads SET status='paused', speed=NULL, eta=NULL WHERE id=?", (did,))
                db.x("UPDATE videos SET status='queued' WHERE id=?", (vid,))
            return
        except Exception as e:
            self._control.pop(did, None)
            msg = str(e).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
            log.warning("download %s (%s) failed: %s", did, vid, msg)
            db.x("UPDATE downloads SET status='error', error=?, finished_at=?, speed=NULL, eta=NULL WHERE id=?",
                 (msg, now(), did))
            db.x("UPDATE videos SET status='error' WHERE id=?", (vid,))
            return
        self._control.pop(did, None)
        short = not audio and (bool(v["is_short"])
                               or is_short(v["url"], res.get("duration"), res.get("width"), res.get("height")))
        meta = {"id": vid, "title": res.get("title") or v["title"], "channel": res.get("channel") or v["channel"],
                "channel_id": res.get("channel_id"), "duration": res.get("duration") or v["duration"],
                "tags": res.get("tags"), "is_short": short}
        if res.get("tags"):
            meta["keywords"] = library.video_keywords(meta["title"], res["tags"])
        library.upsert_video(meta)
        with db.tx():
            db.x("UPDATE videos SET status='ready', file_path=?, file_size=?, width=?, height=?, vcodec=?, "
                 "acodec=?, is_short=?, downloaded_at=? WHERE id=?",
                 (res["file_path"], res.get("file_size"), res.get("width"), res.get("height"),
                  res.get("vcodec"), res.get("acodec"), 1 if short else 0, now(), vid))
            db.x("UPDATE downloads SET status='done', progress=100, speed=NULL, eta=0, phase=NULL, "
                 "finished_at=? WHERE id=?", (now(), did))
        if res.get("subtitles"):
            library.save_downloaded_subtitles(vid, res["subtitles"], self.cfg.subs_dir)
        # Warm the thumbnail cache so lists render instantly on the phone.
        try:
            dur = res.get("duration") or v.get("duration")
            self.yt.thumbnail(vid, v.get("thumb_url") or "", self.cfg.thumbs_dir,
                              local_file=res["file_path"], at=(dur or 0) * 0.1)
        except Exception:
            pass
        log.info("download %s done: %s", did, v["title"])

    def _finish_cancel(self, did: int, vid: str) -> None:
        db.x("UPDATE downloads SET status='cancelled', speed=NULL, eta=NULL, finished_at=? WHERE id=?", (now(), did))
        v = db.q1("SELECT status FROM videos WHERE id=?", (vid,))
        if v and v["status"] != "ready":
            db.x("UPDATE videos SET status='new' WHERE id=?", (vid,))
            db.x("DELETE FROM queue_items WHERE video_id=?", (vid,))
        yid = youtube_id(vid)
        for part_dir in (Path(self.cfg.library_dir) / ".partial", Path(self.cfg.library_dir) / "music" / ".partial"):
            if not part_dir.is_dir():
                continue
            for f in part_dir.glob(f"{yid}*"):
                f.unlink(missing_ok=True)
