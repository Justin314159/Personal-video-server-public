import pytest

from homelib.formats import FORMAT_BEST, FORMAT_COMPAT, needs_vlc, select_format, ydl_format_opts


def test_format_strings_match_prototype():
    assert FORMAT_BEST == "bv*+ba/b"
    assert FORMAT_COMPAT == "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/b[ext=mp4]/b"


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
