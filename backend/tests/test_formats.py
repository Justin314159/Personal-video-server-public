import pytest

from homelib.formats import (FORMAT_BEST, FORMAT_COMPAT, ios_playable, needs_vlc, select_format,
                             ydl_format_opts)


def test_format_strings_are_iphone_only():
    assert FORMAT_BEST == ("bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[vcodec^=avc1][acodec^=mp4a]"
                           "/b[ext=mp4][vcodec^=?avc1][acodec^=?mp4a]")
    assert FORMAT_COMPAT == ("bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]"
                             "/b[vcodec^=avc1][acodec^=mp4a][height<=?1080]"
                             "/b[ext=mp4][vcodec^=?avc1][acodec^=?mp4a][height<=?1080]")
    for f in (FORMAT_BEST, FORMAT_COMPAT):
        assert not f.endswith("/b")  # no catch-all that could pick VP9/AV1/Opus


def _pick(fmt, formats):
    """Run yt-dlp's real format selector over a fake format list."""
    from yt_dlp import YoutubeDL

    info = {"id": "a", "title": "t", "extractor": "x", "extractor_key": "X", "webpage_url": "https://e.invalid",
            "formats": [{"url": f"https://e.invalid/{f[0]}", "protocol": "https", "format_id": f[0],
                         "vcodec": f[1], "acodec": f[2], "ext": f[3], "height": f[4],
                         "tbr": f[5]} for f in formats]}
    with YoutubeDL({"quiet": True, "no_warnings": True, "simulate": True, "format": fmt}) as y:
        return y.process_ie_result(info, download=False)["format_id"]


YT = [("137", "avc1.640028", "none", "mp4", 1080, 4000), ("313", "vp09.00.51.08", "none", "webm", 2160, 12000),
      ("401", "av01.0.12M.08", "none", "mp4", 2160, 10000), ("251", "none", "opus", "webm", None, 160),
      ("140", "none", "mp4a.40.2", "m4a", None, 128), ("18", "avc1.42001E", "mp4a.40.2", "mp4", 360, 500)]


@pytest.mark.parametrize("fmt", [FORMAT_BEST, FORMAT_COMPAT])
def test_selection_picks_h264_aac(fmt):
    assert _pick(fmt, YT) == "137+140"
    assert _pick(fmt, [YT[1], YT[3], YT[5]]) == "18"  # only VP9/Opus streams + the 360p mp4
    with pytest.raises(Exception, match="Requested format is not available"):
        _pick(fmt, [YT[1], YT[2], YT[3], ("43", "vp8", "vorbis", "webm", 360, 600)])


@pytest.mark.parametrize("v,a,ok", [("avc1.640028", "mp4a.40.2", True), (None, None, True), ("h264", "aac", True),
                                    ("vp09.00.40.08", "mp4a.40.2", False), ("avc1.4d401f", "opus", False),
                                    ("av01.0.08M.08", "opus", False)])
def test_ios_playable(v, a, ok):
    assert ios_playable(v, a) is ok


def test_select_format():
    assert select_format("best") == FORMAT_BEST
    assert select_format("compat") == FORMAT_COMPAT


def test_shorts_always_compat():
    assert select_format("best", is_short=True) == FORMAT_COMPAT


def test_unknown_quality_rejected():
    with pytest.raises(ValueError):
        select_format("8k")


def test_opts_merge_to_mp4():
    o = ydl_format_opts("best")
    assert o["merge_output_format"] == "mp4"
    assert o["format"] == FORMAT_BEST


@pytest.mark.parametrize("codec,vlc", [("avc1.640028", False), ("hvc1", False), ("vp09.00.40.08", True),
                                       ("vp9", True), ("av01.0.08M.08", True), (None, False), ("none", False)])
def test_needs_vlc(codec, vlc):
    assert needs_vlc(codec) is vlc


def test_needs_vlc_for_opus_sound():
    assert needs_vlc("avc1.640028", "opus") is True
    assert needs_vlc("avc1.640028", "mp4a.40.2") is False
