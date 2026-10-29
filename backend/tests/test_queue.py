from homelib import db, library


def _vids(n):
    for i in range(n):
        library.upsert_video({"id": f"vid{i:08d}", "title": f"Video {i}", "channel": "C"})
    return [f"vid{i:08d}" for i in range(n)]


def ids(items):
    return [i["id"] for i in items]


def test_personal_queues_are_separate(fresh_db):
    a, b, c = _vids(3)
    library.queue_add(a, "personal", 1)
    library.queue_add(b, "personal", 2)
    library.queue_add(c, "shared", 1)
    assert ids(library.queue_list("personal", 1)) == [a]
    assert ids(library.queue_list("personal", 2)) == [b]
    assert ids(library.queue_list("shared", 2)) == [c]


def test_add_is_idempotent_and_appends(fresh_db):
    a, b, c = _vids(3)
    for v in (a, b, a, c):
        library.queue_add(v, "personal", 1)
    assert ids(library.queue_list("personal", 1)) == [a, b, c]


def test_reorder_and_move(fresh_db):
    a, b, c = _vids(3)
    items = [library.queue_add(v, "shared", 1)["id"] for v in (a, b, c)]
    library.queue_reorder("shared", 1, [items[2], items[0]])  # b not mentioned -> kept at end
    assert ids(library.queue_list("shared", 1)) == [c, a, b]
    library.queue_move(items[1], -1, 1)  # move b up
    assert ids(library.queue_list("shared", 1)) == [c, b, a]
    library.queue_move(items[2], -1, 1)  # c already first
    assert ids(library.queue_list("shared", 1)) == [c, b, a]


def test_reorder_ignores_other_scopes(fresh_db):
    a, b = _vids(2)
    mine = library.queue_add(a, "personal", 1)["id"]
    theirs = library.queue_add(b, "personal", 2)["id"]
    library.queue_reorder("personal", 1, [theirs, mine])
    assert ids(library.queue_list("personal", 2)) == [b]


def test_remove(fresh_db):
    a, b = _vids(2)
    i = library.queue_add(a, "shared", 1)["id"]
    library.queue_add(b, "shared", 1)
    library.queue_remove(i)
    assert ids(library.queue_list("shared", 1)) == [b]


def test_finishing_removes_from_my_queue_only(fresh_db):
    (a,) = _vids(1)
    db.x("UPDATE videos SET duration=100 WHERE id=?", (a,))
    library.queue_add(a, "personal", 1)
    library.queue_add(a, "shared", 1)
    library.update_progress(1, a, 95, 100)
    assert library.queue_list("personal", 1) == []
    assert ids(library.queue_list("shared", 1)) == [a]
    assert db.q1("SELECT COUNT(*) n FROM events WHERE type='finish'")["n"] == 1
    library.update_progress(1, a, 99, 100)  # no duplicate finish event
    assert db.q1("SELECT COUNT(*) n FROM events WHERE type='finish'")["n"] == 1


def test_audience_widens_only(fresh_db):
    (a,) = _vids(1)
    library.set_audience(a, "p:1")
    library.queue_add(a, "personal", 1)
    assert library.get_video(a)["audience"] == "p:1"
    library.queue_add(a, "personal", 2)
    assert library.get_video(a)["audience"] == "all"
