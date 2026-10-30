"""SQLite storage (WAL mode). One connection per thread."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    accent      TEXT NOT NULL,
    avatar_at   REAL,                         -- when the profile photo was last set (NULL = initials)
    created_at  REAL NOT NULL
);

-- One row per YouTube video we have ever seen as a download target.
CREATE TABLE IF NOT EXISTS videos (
    id            TEXT PRIMARY KEY,           -- YouTube video id
    title         TEXT NOT NULL DEFAULT '',
    channel       TEXT NOT NULL DEFAULT '',
    channel_id    TEXT NOT NULL DEFAULT '',
    duration      REAL,                       -- seconds
    url           TEXT NOT NULL DEFAULT '',
    thumb_url     TEXT NOT NULL DEFAULT '',
    keywords      TEXT NOT NULL DEFAULT '[]', -- JSON list, used by the recommender
    description   TEXT NOT NULL DEFAULT '',   -- search snippet (recommender)
    is_short      INTEGER NOT NULL DEFAULT 0,
    media         TEXT NOT NULL DEFAULT 'video', -- 'video' | 'audio' (music)
    width         INTEGER,
    height        INTEGER,
    vcodec        TEXT,
    acodec        TEXT,
    quality       TEXT,                       -- 'best' | 'compat'
    file_path     TEXT,
    file_size     INTEGER,
    status        TEXT NOT NULL DEFAULT 'new',-- new|queued|downloading|ready|deleted|error
    audience      TEXT NOT NULL DEFAULT 'all',-- 'all' or 'p:<profile id>' (who must watch before delete)
    keep          INTEGER NOT NULL DEFAULT 0,
    added_by      INTEGER,
    created_at    REAL NOT NULL,
    downloaded_at REAL,
    deleted_at    REAL,
    delete_reason TEXT
);
CREATE INDEX IF NOT EXISTS videos_status ON videos(status);

CREATE TABLE IF NOT EXISTS queue_items (
    id          INTEGER PRIMARY KEY,
    video_id    TEXT NOT NULL REFERENCES videos(id),
    scope       TEXT NOT NULL,                -- 'shared' | 'personal'
    profile_id  INTEGER,                      -- owner for personal, NULL for shared
    position    INTEGER NOT NULL,
    added_by    INTEGER,
    added_at    REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS queue_unique ON queue_items(video_id, scope, IFNULL(profile_id, 0));

CREATE TABLE IF NOT EXISTS watch_state (
    profile_id  INTEGER NOT NULL,
    video_id    TEXT NOT NULL,
    position    REAL NOT NULL DEFAULT 0,
    duration    REAL,
    watched     INTEGER NOT NULL DEFAULT 0,
    rating      INTEGER NOT NULL DEFAULT 0,   -- 1 like, -1 dislike, 0 none
    speed       REAL,                         -- remembered playback rate (NULL = 1x)
    updated_at  REAL NOT NULL,
    PRIMARY KEY (profile_id, video_id)
);

CREATE TABLE IF NOT EXISTS downloads (
    id               INTEGER PRIMARY KEY,
    video_id         TEXT NOT NULL,
    quality          TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'user', -- 'user' | 'background'
    status           TEXT NOT NULL,                -- queued|running|paused|done|error|cancelled
    progress         REAL NOT NULL DEFAULT 0,      -- 0..100
    speed            REAL,                         -- bytes/s
    eta              REAL,                         -- seconds
    downloaded_bytes INTEGER,
    total_bytes      INTEGER,
    phase            TEXT,                         -- 'video', 'audio', 'merging'
    error            TEXT,
    requested_by     INTEGER,
    created_at       REAL NOT NULL,
    started_at       REAL,
    finished_at      REAL
);

-- Everything the recommender learns from. Users can view and delete rows.
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    profile_id  INTEGER,
    video_id    TEXT,
    type        TEXT NOT NULL,   -- finish|like|dislike|delete_unwatched|skip|search|deleted
                                 -- 1.5: rewatch|more|less|not_interested|subscribe
    weight      REAL NOT NULL DEFAULT 0,
    keywords    TEXT NOT NULL DEFAULT '[]',
    channel     TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL DEFAULT '{}',
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_profile ON events(profile_id, ts);

-- Candidate pool + cached scores. profile_id 0 = "Both of us".
CREATE TABLE IF NOT EXISTS reco_cache (
    profile_id  INTEGER NOT NULL,
    video_id    TEXT NOT NULL,
    meta        TEXT NOT NULL,     -- JSON: title, channel, duration, url, thumb, keywords, is_short
    source      TEXT NOT NULL DEFAULT '',
    score       REAL NOT NULL DEFAULT 0,
    reasons     TEXT NOT NULL DEFAULT '[]',
    fetched_at  REAL NOT NULL,
    scored_at   REAL,
    PRIMARY KEY (profile_id, video_id)
);

-- Suggestions a profile has already been shown and moved past with a feed
-- reload (manual or nightly). profile_id 0 = "Both of us".
CREATE TABLE IF NOT EXISTS reco_seen (
    profile_id  INTEGER NOT NULL,
    video_id    TEXT NOT NULL,
    ts          REAL NOT NULL,
    PRIMARY KEY (profile_id, video_id)
);

-- Named playlists: 'shared' (profile_id NULL, both see them) or 'personal'.
CREATE TABLE IF NOT EXISTS playlists (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    scope       TEXT NOT NULL,                -- 'shared' | 'personal'
    profile_id  INTEGER,                      -- owner for personal, NULL for shared
    created_by  INTEGER,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_items (
    id          INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    video_id    TEXT NOT NULL,
    position    INTEGER NOT NULL,
    added_by    INTEGER,
    added_at    REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS playlist_items_unique ON playlist_items(playlist_id, video_id);

-- Caption files (cleaned WebVTT) stored next to the library.
CREATE TABLE IF NOT EXISTS subtitles (
    id          INTEGER PRIMARY KEY,
    video_id    TEXT NOT NULL,
    lang        TEXT NOT NULL,
    label       TEXT NOT NULL,
    kind        TEXT NOT NULL,                -- 'manual' | 'auto' (YouTube) | 'uploaded'
    path        TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS subtitles_video ON subtitles(video_id);

-- "Delete after watching" votes on videos that are for more than one person.
CREATE TABLE IF NOT EXISTS delete_votes (
    video_id    TEXT NOT NULL,
    profile_id  INTEGER NOT NULL,
    ts          REAL NOT NULL,
    PRIMARY KEY (video_id, profile_id)
);

-- 1.5.0 recommender ---------------------------------------------------------

-- Sentence embeddings (L2-normalised float32), per model. key is a video id,
-- or 't:<sha1>' for a short text (a typed topic, a search), or
-- 'block:<profile>:<mute key>' for a blocked interest's centroid.
CREATE TABLE IF NOT EXISTS embeddings (
    key         TEXT NOT NULL,
    model       TEXT NOT NULL,
    vec         BLOB NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (key, model)
);

-- Interest clusters per profile: learned from history, or added by hand.
-- Keyword boosts from 1.4 became 'manual' interests.
CREATE TABLE IF NOT EXISTS interests (
    id            INTEGER PRIMARY KEY,
    profile_id    INTEGER NOT NULL,
    kind          TEXT NOT NULL,              -- 'learned' | 'manual'
    label         TEXT NOT NULL,
    renamed       INTEGER NOT NULL DEFAULT 0, -- label set by the user (learned)
    model         TEXT,
    centroid      BLOB,
    evidence      REAL NOT NULL DEFAULT 0,    -- decayed signal behind it
    manual_weight REAL,                       -- set by the user: wins over the learned weight
    pinned        INTEGER NOT NULL DEFAULT 0,
    hidden        INTEGER NOT NULL DEFAULT 0, -- a deleted learned interest (kept so it stays gone)
    seeds         TEXT NOT NULL DEFAULT '[]', -- search phrases (manual topics)
    members       TEXT NOT NULL DEFAULT '[]', -- item keys, strongest first
    stats         TEXT NOT NULL DEFAULT '{}', -- counts per signal, example titles, keywords
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS interests_profile ON interests(profile_id);

-- Channel subscriptions per profile.
CREATE TABLE IF NOT EXISTS subscriptions (
    profile_id   INTEGER NOT NULL,
    channel_id   TEXT NOT NULL,
    channel      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (profile_id, channel_id)
);

-- Latest uploads of subscribed channels: metadata only (never downloaded).
CREATE TABLE IF NOT EXISTS channel_uploads (
    channel_id   TEXT NOT NULL,
    video_id     TEXT NOT NULL,
    meta         TEXT NOT NULL,              -- JSON: title, channel, duration, url, thumb_url, ...
    upload_ts    REAL,                       -- when YouTube says it was published (approximate)
    first_seen   REAL NOT NULL,
    PRIMARY KEY (channel_id, video_id)
);
CREATE TABLE IF NOT EXISTS channel_checks (
    channel_id   TEXT PRIMARY KEY,
    checked_at   REAL NOT NULL,
    ok           INTEGER NOT NULL,
    error        TEXT
);
-- Uploads a profile has already seen in the Subscriptions tab (the "new" marker).
CREATE TABLE IF NOT EXISTS upload_seen (
    profile_id   INTEGER NOT NULL,
    video_id     TEXT NOT NULL,
    ts           REAL NOT NULL,
    PRIMARY KEY (profile_id, video_id)
);

-- yt-dlp search results, so the same search isn't repeated within a few hours.
CREATE TABLE IF NOT EXISTS search_cache (
    query        TEXT NOT NULL,
    kind         TEXT NOT NULL,              -- 'search:<n>' | 'shorts:<n>' | 'channel:<n>'
    results      TEXT NOT NULL,
    fetched_at   REAL NOT NULL,
    PRIMARY KEY (query, kind)
);

CREATE TABLE IF NOT EXISTS settings (
    scope  TEXT NOT NULL,     -- 'global' or 'profile:<id>'
    key    TEXT NOT NULL,
    value  TEXT NOT NULL,     -- JSON
    PRIMARY KEY (scope, key)
);
"""

_local = threading.local()
_db_path: Path | None = None
_write_lock = threading.RLock()


def init(path: Path) -> None:
    global _db_path
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    _local.__dict__.clear()
    c = conn()
    c.executescript(SCHEMA)
    _migrate(c)
    c.commit()


def _migrate(c: sqlite3.Connection) -> None:
    """Additive schema changes for databases created by older versions."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(videos)")}
    if "media" not in cols:
        c.execute("ALTER TABLE videos ADD COLUMN media TEXT NOT NULL DEFAULT 'video'")
    ws_cols = {r[1] for r in c.execute("PRAGMA table_info(watch_state)")}
    if "speed" not in ws_cols:  # per video, per profile playback speed (1.3.0)
        c.execute("ALTER TABLE watch_state ADD COLUMN speed REAL")
    p_cols = {r[1] for r in c.execute("PRAGMA table_info(profiles)")}
    if "avatar_at" not in p_cols:  # profile photos (1.4.0)
        c.execute("ALTER TABLE profiles ADD COLUMN avatar_at REAL")
    if "description" not in cols:  # snippet for the recommender's embeddings (1.5.0)
        c.execute("ALTER TABLE videos ADD COLUMN description TEXT NOT NULL DEFAULT ''")


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != _db_path:
        assert _db_path is not None, "db.init() not called"
        c = sqlite3.connect(_db_path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
        _local.path = _db_path
    return c


def q(sql: str, args: tuple | list = ()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql: str, args: tuple | list = ()) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def x(sql: str, args: tuple | list = ()) -> sqlite3.Cursor:
    with _write_lock:
        return conn().execute(sql, args)


class tx:
    """`with db.tx():` – an explicit write transaction (serialised in-process)."""

    def __enter__(self):
        _write_lock.acquire()
        conn().execute("BEGIN IMMEDIATE")
        return conn()

    def __exit__(self, et, ev, tb):
        try:
            conn().execute("ROLLBACK" if et else "COMMIT")
        finally:
            _write_lock.release()
        return False


# ---- settings helpers -----------------------------------------------------

def get_setting(scope: str, key: str, default: Any = None) -> Any:
    r = q1("SELECT value FROM settings WHERE scope=? AND key=?", (scope, key))
    return json.loads(r["value"]) if r else default


def set_setting(scope: str, key: str, value: Any) -> None:
    x(
        "INSERT INTO settings(scope,key,value) VALUES(?,?,?) "
        "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value",
        (scope, key, json.dumps(value)),
    )


def now() -> float:
    return time.time()
