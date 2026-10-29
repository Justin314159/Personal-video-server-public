from homelib import cleaner, db, library
from homelib.db import now

H = 3600


def video(**kw):
    v = {"id": "v1", "status": "ready", "keep": 0, "downloaded_at": now() - 2 * H}
    v.update(kw)
    return v


def ws(watched, ago=2 * H):
    return {"watched": watched, "updated_at": now() - ago}


def test_deletes_when_everyone_watched():
    ok, reason = cleaner.decide(video(), {1: ws(1), 2: ws(1)}, [1, 2], now(), 24)
    assert ok and "everyone" in reason


def test_waits_for_second_person():
    ok, _ = cleaner.decide(video(), {1: ws(1)}, [1, 2], now(), 24)
    assert not ok


def test_personal_video_only_needs_owner():
    ok, _ = cleaner.decide(video(), {2: ws(1)}, [2], now(), 24)
    assert ok


def test_age_limit():
    for hours in (12, 24, 48):
        assert not cleaner.decide(video(downloaded_at=now() - (hours - 1) * H), {}, [1, 2], now(), hours)[0]
        ok, reason = cleaner.decide(video(downloaded_at=now() - (hours + 1) * H), {}, [1, 2], now(), hours)
        assert ok and f"{hours}h" in reason


def test_keep_flag_blocks_everything():
    assert not cleaner.decide(video(keep=1, downloaded_at=now() - 100 * H), {1: ws(1), 2: ws(1)},
                              [1, 2], now(), 24)[0]


def test_recent_activity_grace():
    ok, reason = cleaner.decide(video(downloaded_at=now() - 30 * H), {1: ws(0, ago=60)}, [1, 2], now(), 24)
    assert not ok and reason == "recently active"


def test_not_ready_is_ignored():
    assert not cleaner.decide(video(status="downloading", downloaded_at=0), {}, [1, 2], now(), 24)[0]


def _ready(cfg, vid, audience="all", hours_old=1):
    library.upsert_video({"id": vid, "title": f"Video {vid}", "channel": "C"})
    f = cfg.library_dir / f"{vid}.mp4"
    cfg.library_dir.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x" * 100)
    db.x("UPDATE videos SET status='ready', file_path=?, downloaded_at=?, audience=? WHERE id=?",
         (str(f), now() - hours_old * H, audience, vid))
    return f


def _watched(pid, vid):
    db.x("INSERT INTO watch_state(profile_id, video_id, position, watched, updated_at) VALUES(?,?,0,1,?)",
         (pid, vid, now() - 2 * H))


def test_run_deletes_and_logs(fresh_db):
    cfg = fresh_db
    f_both = _ready(cfg, "both0000001")
    _watched(1, "both0000001"); _watched(2, "both0000001")
    f_half = _ready(cfg, "half0000001")
    _watched(1, "half0000001")
    f_old = _ready(cfg, "old00000001", hours_old=30)
    f_keep = _ready(cfg, "keep0000001", hours_old=30)
    library.set_keep("keep0000001", True)
    f_pers = _ready(cfg, "pers0000001", audience="p:1")
    _watched(1, "pers0000001")

    deleted = {d["id"] for d in cleaner.run(cfg.library_dir, cfg.thumbs_dir, 24)}
    assert deleted == {"both0000001", "old00000001", "pers0000001"}
    assert not f_both.exists() and not f_old.exists() and not f_pers.exists()
    assert f_half.exists() and f_keep.exists()
    logs = db.q("SELECT video_id, data FROM events WHERE type='deleted'")
    assert {r["video_id"] for r in logs} == deleted
    # auto-deletes are not held against anyone's algorithm
    assert not db.q("SELECT 1 FROM events WHERE type='delete_unwatched'")


def test_manual_delete_unwatched_is_negative_signal(fresh_db):
    cfg = fresh_db
    _ready(cfg, "manual00001")
    library.delete_video("manual00001", "manual", 2, cfg.library_dir, cfg.thumbs_dir)
    ev = db.q("SELECT * FROM events WHERE type='delete_unwatched'")
    assert len(ev) == 1 and ev[0]["profile_id"] == 2 and ev[0]["weight"] == -1


def test_shared_queue_makes_everyone_relevant(fresh_db):
    cfg = fresh_db
    _ready(cfg, "shared00001", audience="p:1")
    assert library.relevant_profiles("shared00001") == [1]
    library.queue_add("shared00001", "shared", 1)
    assert library.relevant_profiles("shared00001") == [1, 2]
