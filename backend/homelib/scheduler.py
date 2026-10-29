"""Background jobs: auto-delete, Shorts prefetch, nightly candidate fetch."""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading

from . import cleaner, db, library, recommender
from .db import now

log = logging.getLogger("homelib.scheduler")

CLEAN_EVERY = 10 * 60
SHORTS_EVERY = 15 * 60


def current_auto_delete_hours(cfg) -> int:
    h = db.get_setting("global", "auto_delete_hours", cfg.auto_delete_hours)
    return h if h in (12, 24, 48) else 24


def pending_shorts_count() -> int:
    """Shorts on disk (or on their way) that at least one person hasn't seen."""
    pids = library.profile_ids()
    n = 0
    for v in db.q("SELECT id FROM videos WHERE is_short=1 AND status IN ('ready','queued','downloading')"):
        seen = {r["profile_id"] for r in db.q(
            "SELECT profile_id FROM watch_state WHERE video_id=? AND watched=1", (v["id"],))}
        if not set(pids) <= seen:
            n += 1
    return n


def prefetch_shorts(cfg, yt, dm) -> int:
    """Keep at least cfg.shorts_prefetch unseen Shorts downloaded ahead."""
    need = cfg.shorts_prefetch - pending_shorts_count()
    if need <= 0:
        return 0
    pids = library.profile_ids()
    cands = recommender.rank(pids[0], mode="both", shorts=True, limit=need * 2)
    if len(cands) < need:
        # Not enough Shorts in the candidate pool yet: search some directly.
        for pid in pids:
            for q, src, shorts in recommender.candidate_queries(pid):
                if not shorts:
                    continue
                for r in yt.search(q, 20, shorts=True):
                    r["keywords"] = library.video_keywords(r.get("title", ""))
                    db.x("INSERT OR IGNORE INTO reco_cache(profile_id, video_id, meta, source, fetched_at) "
                         "VALUES(?,?,?,?,?)", (pid, r["id"], json.dumps(r), src, now()))
        cands = recommender.rank(pids[0], mode="both", shorts=True, limit=need * 2)
    queued = 0
    for c in cands[:need]:
        try:
            dm.enqueue(c, "compat", kind="background")
            queued += 1
        except library.DiskFull as e:
            log.info("shorts prefetch stopped: %s", e)
            break
    return queued


def nightly(cfg, yt) -> None:
    recommender.nightly_reload(yt)
    for pid in library.profile_ids():
        recommender.rank(pid, "me")
    recommender.rank(library.profile_ids()[0], "both")
    db.set_setting("global", "last_nightly", now())


class Scheduler:
    def __init__(self, cfg, yt, dm):
        self.cfg, self.yt, self.dm = cfg, yt, dm
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last = {"clean": 0.0, "shorts": 0.0, "fetch_attempt": 0.0}
        self.running_job: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _due_nightly(self) -> bool:
        hh, mm = (int(x) for x in self.cfg.nightly_time.split(":"))
        t = dt.datetime.now()
        target = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t < target:
            target -= dt.timedelta(days=1)
        last = db.get_setting("global", "last_nightly", 0) or 0
        return last < target.timestamp()

    def _run(self, name: str, fn) -> None:
        self.running_job = name
        try:
            fn()
        except Exception:
            log.exception("job %s failed", name)
        finally:
            self.running_job = None

    def _loop(self) -> None:
        self._stop.wait(5)  # let the server finish starting
        while not self._stop.is_set():
            t = now()
            if t - self.last["clean"] >= CLEAN_EVERY:
                self.last["clean"] = t
                self._run("cleaner", lambda: cleaner.run(self.cfg.library_dir, self.cfg.thumbs_dir,
                                                          current_auto_delete_hours(self.cfg)))
            empty_pool = not db.q1("SELECT 1 FROM reco_cache LIMIT 1")
            # Empty pool (first start, or no internet): retry at most every 30 minutes.
            retry_empty = empty_pool and t - self.last["fetch_attempt"] >= 30 * 60
            if self._due_nightly() or retry_empty:
                self.last["fetch_attempt"] = t
                self._run("candidate fetch", lambda: nightly(self.cfg, self.yt))
            if t - self.last["shorts"] >= SHORTS_EVERY:
                self.last["shorts"] = t
                self._run("shorts prefetch", lambda: prefetch_shorts(self.cfg, self.yt, self.dm))
            self._stop.wait(30)
