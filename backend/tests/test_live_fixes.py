"""Regressions found by live YouTube testing (2026-10-28), checked offline."""
from homelib import innertube
from homelib.ytlib import _entry_to_result


def test_lookup_never_stores_stream_url():
    # A processed yt-dlp info dict: "url" is a temporary googlevideo address.
    info = {"id": "jNQXAC9IVRw", "title": "Me at the zoo", "ie_key": "Youtube", "duration": 19,
            "url": "https://rr3---sn-abc.googlevideo.com/videoplayback?expire=1&itag=18",
            "webpage_url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}
    assert _entry_to_result(info)["url"] == "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    info.pop("webpage_url")
    assert _entry_to_result(info)["url"] == "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    flat = {"id": "abcdefghijk", "ie_key": "Youtube", "url": "https://www.youtube.com/shorts/abcdefghijk"}
    assert _entry_to_result(flat)["url"].endswith("/shorts/abcdefghijk")


def _shorts_response():
    item = lambda vid, title, views: {"shortsLockupViewModel": {
        "onTap": {"innertubeCommand": {"reelWatchEndpoint": {"videoId": vid}}},
        "overlayMetadata": {"primaryText": {"content": title}, "secondaryText": {"content": views}}}}
    return {"contents": {"x": [item("aaaaaaaaaaa", "Cat 1", "1.2M views"), item("bbbbbbbbbbb", "Cat 2", "850K views"),
                               item("aaaaaaaaaaa", "dup", "1 views"), {"shortsLockupViewModel": {"broken": True}}]}}


def test_shorts_parser(monkeypatch):
    monkeypatch.setattr(innertube, "_search", lambda *a, **k: _shorts_response())
    r = innertube.search_shorts("cat")
    assert [x["id"] for x in r] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert r[0]["is_short"] and r[0]["url"].endswith("/shorts/aaaaaaaaaaa") and r[0]["view_count"] == 1_200_000


def _row(vid, title, info, length=None):
    col = lambda t: {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": t}]}}}
    r = {"flexColumns": [col(title), col(info)]}
    if vid:
        r["playlistItemData"] = {"videoId": vid}
    if length:
        r["fixedColumns"] = [{"musicResponsiveListItemFixedColumnRenderer": {"text": {"runs": [{"text": length}]}}}]
    return {"musicResponsiveListItemRenderer": r}


def test_music_parser_songs_then_videos(monkeypatch):
    songs = {"c": [_row("sssssssssss", "Get Lucky", "Song • Daft Punk • Random Access Memories", "6:09"),
                   _row(None, "Daft Punk", "Artist • 68M monthly audience")]}
    monkeypatch.setattr(innertube, "_search", lambda c, q, p, **k: songs)
    r = innertube.search_music("get lucky")
    assert r[0]["id"] == "sssssssssss" and r[0]["channel"] == "Daft Punk" and r[0]["duration"] == 369
    # No songs (as seen from some networks): use the video rows, de-duplicated, artists skipped.
    vids = {"c": [_row(None, "Daft Punk", "Artist • 68M"), _row("vvvvvvvvvvv", "Get Lucky (Radio Edit)", "Video • Someone • 2.1M views"),
                  _row("vvvvvvvvvvv", "dup", "Video • x")]}
    monkeypatch.setattr(innertube, "_search", lambda c, q, p, **k: {} if p else vids)
    r = innertube.search_music("get lucky")
    assert [x["id"] for x in r] == ["vvvvvvvvvvv"] and r[0]["channel"] == "Someone" and r[0]["view_count"] == 2_100_000
