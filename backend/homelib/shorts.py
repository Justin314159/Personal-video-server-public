"""Shorts detection."""
from __future__ import annotations

SHORTS_MAX_SECONDS = 180


def is_short(url: str | None = None, duration: float | None = None,
             width: int | None = None, height: int | None = None) -> bool:
    """A video counts as a Short if ANY of these hold:

    * its URL is a /shorts/ URL,
    * it is vertical (taller than wide), or
    * it is 180 seconds or shorter.

    Unknown values (None) never make something a Short on their own.
    """
    if url and "/shorts/" in url:
        return True
    if width and height and height > width:
        return True
    if duration is not None and 0 < duration <= SHORTS_MAX_SECONDS:
        return True
    return False
