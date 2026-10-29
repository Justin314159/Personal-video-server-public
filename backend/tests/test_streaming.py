import pytest

from homelib import db, library
from homelib.db import now
from homelib.streaming import parse_range

DATA = bytes(range(256)) * 4096  # 1 MiB


@pytest.mark.parametrize("h,expected", [
    (None, None), ("bytes=0-1", (0, 1)), ("bytes=100-", (100, 999)), ("bytes=-10", (990, 999)),
    ("bytes=990-5000", (990, 999)), ("bytes=1000-", "invalid"), ("bytes=5-2", "invalid"),
    ("bytes=-0", "invalid"), ("items=0-1", None), ("bytes=0-1,5-6", (0, 1)), ("bytes=-", None),
])
def test_parse_range(h, expected):
    assert parse_range(h, 1000) == expected


@pytest.fixture
def ready_video(client, cfg):
    library.upsert_video({"id": "stream00001", "title": "Stream test", "channel": "C"})
    f = cfg.library_dir / "stream00001.mp4"
    f.write_bytes(DATA)
    db.x("UPDATE videos SET status='ready', file_path=?, downloaded_at=? WHERE id=?", (str(f), now(), "stream00001"))
    return "/api/stream/stream00001"


def test_full_get(client, ready_video):
    r = client.get(ready_video)
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4"
    assert int(r.headers["content-length"]) == len(DATA)
    assert r.content == DATA


def test_safari_probe(client, ready_video):
    r = client.get(ready_video, headers={"Range": "bytes=0-1"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-1/{len(DATA)}"
    assert r.content == DATA[:2]


def test_middle_and_open_ranges(client, ready_video):
    r = client.get(ready_video, headers={"Range": "bytes=500000-600000"})
    assert r.status_code == 206 and r.content == DATA[500000:600001]
    r = client.get(ready_video, headers={"Range": "bytes=1048000-"})
    assert r.status_code == 206 and r.content == DATA[1048000:]
    r = client.get(ready_video, headers={"Range": "bytes=-100"})
    assert r.content == DATA[-100:]


def test_unsatisfiable(client, ready_video):
    r = client.get(ready_video, headers={"Range": f"bytes={len(DATA)}-"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(DATA)}"


def test_head(client, ready_video):
    r = client.head(ready_video, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.headers["content-length"] == "100" and r.content == b""


def test_if_range_mismatch_sends_full(client, ready_video):
    r = client.get(ready_video, headers={"Range": "bytes=0-1", "If-Range": '"stale"'})
    assert r.status_code == 200 and len(r.content) == len(DATA)


def test_not_downloaded_404(client):
    library.upsert_video({"id": "notready001", "title": "x"})
    assert client.get("/api/stream/notready001").status_code == 404
    assert client.get("/api/stream/nonexist001").status_code == 404
