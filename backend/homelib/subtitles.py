"""Captions and transcripts: parse SRT/WebVTT, clean YouTube auto-captions,
write tidy WebVTT that Safari renders, and pick which tracks to download.

YouTube's automatic captions come as "rolling" VTT: every cue repeats the
previous line and adds word-level timestamps (<00:00:01.200><c> word</c>).
Shown as-is they flicker and a transcript would list every sentence twice, so
clean_cues() keeps only the text each cue adds.
"""
from __future__ import annotations

import html
import re

MAX_UPLOAD_BYTES = 2 * 1024 * 1024

_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_CUE_TIME = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}")
_TAG = re.compile(r"<[^>]*>")


class SubtitleError(ValueError):
    pass


def _secs(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse(text: str) -> list[dict]:
    """SRT or WebVTT text -> [{start, end, text}] (text keeps line breaks, tags removed)."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[dict] = []
    # Only truly empty lines end a cue: YouTube's auto-captions use lines holding
    # a single space inside cues.
    for block in re.split(r"\n\n+", text):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            m = _CUE_TIME.match(line)
            if not m:
                continue
            g = m.groups()
            start, end = _secs(*g[0:4]), _secs(*g[4:8])
            body = [html.unescape(_TAG.sub("", ln)).strip() for ln in lines[i + 1:]]
            body = [b for b in body if b]
            if body and end > start:
                cues.append({"start": round(start, 3), "end": round(end, 3), "text": "\n".join(body)})
            break
    cues.sort(key=lambda c: c["start"])
    return cues


def looks_rolling(raw: str) -> bool:
    """YouTube auto-captions carry inline word timestamps."""
    return bool(re.search(r"<\d{2}:\d{2}:\d{2}\.\d{3}>|<c>", raw))


def clean_cues(cues: list[dict]) -> list[dict]:
    """Collapse rolling captions: keep only the lines each cue adds."""
    out: list[dict] = []
    prev: list[str] = []
    for c in cues:
        lines = [ln for ln in c["text"].split("\n") if ln.strip()]
        new = [ln for ln in lines if ln not in prev]
        prev = lines
        if not new or c["end"] - c["start"] < 0.05:
            continue
        out.append({"start": c["start"], "end": c["end"], "text": " ".join(new)})
    # A rolling cue stays on screen while the next line appears; trim overlaps.
    for a, b in zip(out, out[1:]):
        if a["end"] > b["start"]:
            a["end"] = max(a["start"] + 0.05, b["start"])
    return out


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def to_vtt(cues: list[dict]) -> str:
    parts = ["WEBVTT", ""]
    for c in cues:
        body = c["text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts += [f"{_ts(c['start'])} --> {_ts(c['end'])}", body, ""]
    return "\n".join(parts)


def normalize(raw: str) -> list[dict]:
    """Any SRT/VTT text -> clean cue list. Raises SubtitleError if nothing usable."""
    cues = parse(raw)
    if looks_rolling(raw):
        cues = clean_cues(cues)
    if not cues:
        raise SubtitleError("That file doesn't contain any subtitles we could read (SRT or VTT).")
    return cues


def decode_upload(data: str) -> str:
    if len(data.encode("utf-8", "ignore")) > MAX_UPLOAD_BYTES:
        raise SubtitleError("That subtitle file is too big (2 MB max).")
    return data


# ---- choosing tracks from a yt-dlp info dict ---------------------------------

LANG_NAMES = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
              "pt": "Portuguese", "nl": "Dutch", "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
              "ru": "Russian", "pl": "Polish", "sv": "Swedish", "tr": "Turkish", "hi": "Hindi"}


def lang_label(lang: str, kind: str) -> str:
    base = lang.split("-")[0].lower()
    name = LANG_NAMES.get(base, lang)
    return f"{name} (auto)" if kind == "auto" else name if kind != "uploaded" or name != lang else lang.upper()


def _vtt_url(formats: list[dict]) -> str | None:
    for f in formats or []:
        if f.get("ext") == "vtt" and f.get("url"):
            return f["url"]
    return None


def choose_tracks(info: dict, wanted: list[str], max_tracks: int = 2) -> list[dict]:
    """Pick caption tracks: per wanted language, uploaded subs first, else
    YouTube's automatic ones. If none match, take the auto-captions in the
    video's original language. Returns [{lang, kind, url}] (vtt only)."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    out: list[dict] = []
    seen: set[str] = set()

    def add(lang: str, kind: str, formats) -> bool:
        url = _vtt_url(formats)
        base = lang.split("-")[0]
        if not url or base in seen or lang == "live_chat":
            return False
        seen.add(base)
        out.append({"lang": base, "kind": kind, "url": url})
        return True

    for w in wanted:
        w = w.lower()
        match = [k for k in manual if k.lower() == w or k.lower().startswith(w + "-")]
        if match and add(match[0], "manual", manual[match[0]]):
            continue
        # "en-orig" is the speech recogniser's own output; plain "en" may be a translation.
        for key in (f"{w}-orig", w):
            if key in auto and add(key, "auto", auto[key]):
                break
        if len(out) >= max_tracks:
            return out
    if not out:
        orig = [k for k in auto if k.endswith("-orig")]
        if orig:
            add(orig[0], "auto", auto[orig[0]])
    return out[:max_tracks]
