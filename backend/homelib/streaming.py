"""HTTP Range streaming straight from disk (never loads a file into memory).

iOS Safari probes with "Range: bytes=0-1" before playing and then asks for
large ranges while seeking, so correct 206/416 handling matters.
"""
from __future__ import annotations

import os
import re
from email.utils import formatdate
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

CHUNK = 256 * 1024
_RANGE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None | str:
    """Return (start, end) inclusive, None for "no/ignored range", or 'invalid' (-> 416)."""
    if not header:
        return None
    first = header.split(",")[0]  # multi-range: serve the first one only
    m = _RANGE.match(first)
    if not m:
        return None  # malformed header: RFC 9110 says ignore it and send 200
    a, b = m.group(1), m.group(2)
    if a == "" and b == "":
        return None
    if a == "":  # suffix range: last N bytes
        n = int(b)
        if n == 0:
            return "invalid"
        return max(0, size - n), size - 1
    start = int(a)
    end = int(b) if b else size - 1
    if start >= size or start > end:
        return "invalid"
    return start, min(end, size - 1)


def _iter_file(path: Path, start: int, length: int):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


MIME = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".webm": "video/webm"}


def stream_file(path: Path, request: Request, content_type: str | None = None,
                download_name: str | None = None) -> Response:
    content_type = content_type or MIME.get(path.suffix.lower(), "application/octet-stream")
    st = os.stat(path)
    size = st.st_size
    etag = f'"{st.st_ino:x}-{size:x}-{int(st.st_mtime):x}"'
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
        "ETag": etag,
        "Last-Modified": formatdate(st.st_mtime, usegmt=True),
        "Cache-Control": "private, max-age=3600",
    }
    if download_name:
        # RFC 6266: ASCII fallback + UTF-8 name, so iOS saves "Artist - Title.mp3".
        from urllib.parse import quote

        ascii_name = download_name.encode("ascii", "replace").decode().replace('"', "'")
        headers["Content-Disposition"] = (f'attachment; filename="{ascii_name}"; '
                                          f"filename*=UTF-8''{quote(download_name)}")
    rng = parse_range(request.headers.get("range"), size)
    # If-Range: only honour the range when the validator still matches.
    if_range = request.headers.get("if-range")
    if rng not in (None, "invalid") and if_range and if_range != etag and if_range != headers["Last-Modified"]:
        rng = None
    if rng == "invalid":
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
    if rng is None:
        start, end, status = 0, size - 1, 200
    else:
        start, end = rng
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = end - start + 1 if size else 0
    headers["Content-Length"] = str(length)
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers)
    return StreamingResponse(_iter_file(path, start, length), status_code=status, headers=headers,
                             media_type=content_type)
