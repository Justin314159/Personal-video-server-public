"""Direct calls to YouTube's own search API ("innertube"), for the two searches
yt-dlp handles poorly: Shorts (yt-dlp's ytsearch ignores "#shorts") and
YouTube Music songs (yt-dlp's music search currently returns empty sections).

These are best-effort: YouTube changes this JSON without notice, so every
parser tolerates missing fields and callers fall back to yt-dlp search when
nothing comes back. Verified against live YouTube on 2026-10-28.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request

log = logging.getLogger("homelib.innertube")

WEB = ("WEB", "2.20251020.00.00", "www.youtube.com")
WEB_REMIX = ("WEB_REMIX", "1.20251020.01.00", "music.youtube.com")
# Search filter params (protobuf, base64) as used by youtube.com / music.youtube.com.
SHORTS_FILTER = "EgIQCQ=="                          # Type: Shorts
SONGS_FILTER = "EgWKAQIIAWoKEAoQAxAEEAkQBQ=="       # YouTube Music: Songs
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _search(client: tuple[str, str, str], query: str, params: str | None, timeout: float = 20) -> dict:
    name, version, host = client
    body = {"context": {"client": {"clientName": name, "clientVersion": version, "hl": "en", "gl": "US"}},
            "query": query}
    if params:
        body["params"] = params
    req = urllib.request.Request(
        f"https://{host}/youtubei/v1/search?prettyPrint=false", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA, "Origin": f"https://{host}",
                 "Referer": f"https://{host}/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _walk(o, key: str, out: list) -> list:
    """Collect every value stored under `key`, anywhere in the JSON tree."""
    if isinstance(o, dict):
        if key in o:
            out.append(o[key])
        for v in o.values():
            _walk(v, key, out)
    elif isinstance(o, list):
        for v in o:
            _walk(v, key, out)
    return out


def _views(text: str) -> int | None:
    m = re.search(r"([\d.,]+)\s*([KMB]?)\s*views", text or "", re.I)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(n * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()])


def _duration(text: str) -> float | None:
    m = re.search(r"\b(?:(\d+):)?(\d{1,2}):(\d{2})\b", text or "")
    if not m:
        return None
    h, mm, ss = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return float(h * 3600 + mm * 60 + ss)


def search_shorts(query: str, n: int = 20) -> list[dict]:
    data = _search(WEB, query, SHORTS_FILTER)
    out, seen = [], set()
    for vm in _walk(data, "shortsLockupViewModel", []) + _walk(data, "reelItemRenderer", []):
        try:
            vid = (_walk(vm, "reelWatchEndpoint", []) or [{}])[0].get("videoId") or vm.get("videoId")
            if not vid or vid in seen:
                continue
            meta = vm.get("overlayMetadata") or {}
            title = ((meta.get("primaryText") or {}).get("content")
                     or "".join(r.get("text", "") for r in (vm.get("headline") or {}).get("runs", [])))
            views = (meta.get("secondaryText") or {}).get("content", "")
            seen.add(vid)
            out.append({"id": vid, "title": title or "", "channel": "", "channel_id": "", "duration": None,
                        "url": f"https://www.youtube.com/shorts/{vid}", "is_short": True,
                        "view_count": _views(views)})
        except Exception:  # one odd item shouldn't sink the search
            continue
        if len(out) >= n:
            break
    return out


def _music_rows(data: dict) -> list[dict]:
    out = []
    for r in _walk(data, "musicResponsiveListItemRenderer", []):
        try:
            vid = (r.get("playlistItemData") or {}).get("videoId")
            if not vid:
                continue  # artists, albums, podcasts, profiles
            cols = ["".join(x.get("text", "") for x in
                            (c["musicResponsiveListItemFlexColumnRenderer"]["text"].get("runs") or []))
                    for c in r.get("flexColumns", [])]
            # Song length usually sits in a separate right-hand column.
            cols += ["".join(x.get("text", "") for x in
                             (c.get("musicResponsiveListItemFixedColumnRenderer", {}).get("text", {}).get("runs") or []))
                     for c in r.get("fixedColumns", [])]
            title = cols[0] if cols else ""
            info = " • ".join(cols[1:])
            parts = [p.strip() for p in info.split("•")]
            kind = parts[0] if parts else ""
            # "Song • Artist • Album • 4:08" or "Video • Channel • 2.1M views • 4:08"
            artist = parts[1] if kind in ("Song", "Video") and len(parts) > 1 else (parts[0] if parts else "")
            out.append({"id": vid, "title": title, "channel": artist, "channel_id": "",
                        "duration": _duration(info), "url": f"https://music.youtube.com/watch?v={vid}",
                        "is_short": False, "view_count": _views(info), "kind": kind.lower()})
        except Exception:
            continue
    return out


def search_music(query: str, n: int = 15) -> list[dict]:
    """Songs first; if YouTube Music returns no songs (it hides them from some
    networks), use the videos from its unfiltered results instead."""
    songs = _music_rows(_search(WEB_REMIX, query, SONGS_FILTER))
    if songs:
        return songs[:n]
    rows = _music_rows(_search(WEB_REMIX, query, None))
    seen: set[str] = set()
    rows = [r for r in rows if not (r["id"] in seen or seen.add(r["id"]))]
    rows.sort(key=lambda r: 0 if r["kind"] == "song" else 1)
    return rows[:n]
