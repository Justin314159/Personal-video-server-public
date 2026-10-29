import pytest

from homelib.shorts import is_short


@pytest.mark.parametrize("kw,expected", [
    (dict(url="https://www.youtube.com/shorts/abcdefghijk"), True),
    (dict(url="https://www.youtube.com/watch?v=abcdefghijk", duration=600), False),
    (dict(duration=180), True),
    (dict(duration=181), False),
    (dict(duration=45), True),
    (dict(width=1080, height=1920, duration=900), True),
    (dict(width=1920, height=1080, duration=900), False),
    (dict(), False),
    (dict(duration=0), False),
    (dict(duration=None, width=None, height=None), False),
])
def test_detection(kw, expected):
    assert is_short(**kw) is expected
