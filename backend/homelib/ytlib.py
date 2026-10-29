"""YouTube access through yt-dlp used as a library (or the mock layer).

search():    "ytsearch{n}:{query}" with flat extraction (fast, metadata only)
download():  format selection + progress hook + pause/cancel via the hook
thumbnail(): fetch a thumbnail and store a 320px JPEG on disk (or grab a
             frame from the downloaded file when YouTube's image is unreachable)
subtitles:   caption tracks are picked from the info dict after the download
             and fetched separately, so a caption failure never fails a video
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

from . import innertube, mock, subtitles
from .formats import is_audio, ydl_audio_opts, ydl_format_opts
from .shorts import is_short

log = logging.getLogger("homelib.ytlib")

ProgressFn = Callable[[dict], None]
StopFn = Callable[[], "str | None"]  # returns None, 'pause' or 'cancel'


class Stopped(Exception):
    """Raised when a download is paused or cancelled from the UI."""

    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")


def extract_video_id(s: str) -> str | None:
    s = s.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = _ID_RE.search(s)
    return m.group(1) if m else None


def looks_like_url(s: str) -> bool:
    return bool(re.match(r"https?://", s.strip())) and extract_video_id(s) is not None


# Music downloads are stored as separate rows so the same YouTube id can exist
# as both a video and an mp3: their library id is "au_" + the YouTube id.
AUDIO_PREFIX = "au_"


def youtube_id(library_id: str) -> str:
    return library_id[len(AUDIO_PREFIX):] if library_id.startswith(AUDIO_PREFIX) else library_id


def thumb_url_for(video_id: str) -> str:
    # mqdefault is exactly 320x180, so it rarely needs resizing.
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def _entry_to_result(e: dict) -> dict | None:
    vid = e.get("id")
    if not vid or e.get("ie_key") not in (None, "Youtube"):
        return None  # skip channels/playlists that sometimes show up in search
    url = e.get("webpage_url") or e.get("url") or f"https://www.youtube.com/watch?v={vid}"
    # Only keep real page URLs; a processed info dict's "url" is a temporary
    # googlevideo stream address that expires and can't be downloaded later.
    if not re.match(r"https?://(www\.|m\.|music\.)?youtube\.com/", url):
        url = f"https://www.youtube.com/watch?v={vid}"
    dur = e.get("duration")
    return {
        "id": vid,
        "title": e.get("title") or "",
        "channel": e.get("channel") or e.get("uploader") or "",
        "channel_id": e.get("channel_id") or "",
        "duration": float(dur) if dur else None,
        "url": url,
        "thumb_url": thumb_url_for(vid),
        "is_short": is_short(url, dur),
        "view_count": e.get("view_count"),
    }


class _YtLogger:
    """Send yt-dlp's own messages to our log instead of stderr."""

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)


class YouTube:
    def __init__(self, cfg, ffmpeg: str | None, ffmpeg_dir: Path | None,
                 js_runtime: tuple[str | None, str | None]):
        self.cfg = cfg
        self.mock = cfg.mock
        self.ffmpeg = ffmpeg
        self.ffmpeg_dir = ffmpeg_dir
        self.js_name, self.js_path = js_runtime

    # ---- common yt-dlp options -------------------------------------------
    def _base_opts(self) -> dict:
        opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
                "socket_timeout": 30, "logger": _YtLogger()}
        if self.js_name and self.js_path:
            opts["js_runtimes"] = {self.js_name: {"path": self.js_path}}
        if self.cfg.cookies_file and Path(self.cfg.cookies_file).is_file():
            opts["cookiefile"] = self.cfg.cookies_file
        return opts

    def _extract(self, url: str, opts: dict, download: bool) -> dict:
        import yt_dlp

        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=download)

    # ---- search -------------------------------------------------------------
    def search(self, query: str, n: int = 15, shorts: bool = False) -> list[dict]:
        if self.mock:
            return mock.search(query, n, shorts_only=shorts)
        if shorts:
            try:
                found = innertube.search_shorts(query, n)
                for r in found:
                    r["thumb_url"] = thumb_url_for(r["id"])
                if found:
                    return found
            except Exception as e:
                log.info("innertube shorts search failed (%s); using yt-dlp search", e)
        import yt_dlp

        q = f"{query} #shorts" if shorts else query
        opts = self._base_opts() | {"extract_flat": "in_playlist", "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{int(n)}:{q}", download=False)
        out = []
        for e in info.get("entries") or []:
            r = _entry_to_result(e or {})
            if r and (not shorts or r["is_short"]):
                out.append(r)
        return out

    def lookup(self, url_or_id: str) -> dict | None:
        """Metadata for a pasted link or id (no download)."""
        vid = extract_video_id(url_or_id)
        if self.mock:
            v = mock.BY_ID.get(vid or "")
            return mock.public(v) if v else None
        url = url_or_id if url_or_id.startswith("http") else f"https://www.youtube.com/watch?v={vid}"
        info = self._extract(url, self._base_opts() | {"skip_download": True}, download=False)
        info.setdefault("ie_key", "Youtube")
        r = _entry_to_result(info)
        if r and info.get("tags"):
            r["tags"] = info["tags"][:20]
        return r

    def search_music(self, query: str, n: int = 15) -> list[dict]:
        """Song search on YouTube Music (falls back to a plain search)."""
        if self.mock:
            return mock.search_music(query, n)
        out = []
        try:
            out = innertube.search_music(query, n)
            for r in out:
                r.pop("kind", None)
                r["thumb_url"] = thumb_url_for(r["id"])
        except Exception as e:
            log.info("YouTube Music search failed (%s); using a normal search", e)
        if len(out) < 5:  # few/no YouTube Music results: top up from a normal search
            have = {r["id"] for r in out}
            out += [r for r in self.search(query, n) if not r["is_short"] and r["id"] not in have][: n - len(out)]
            return out
        if not out:  # YouTube Music layout changed or no results
            out = [r for r in self.search(f"{query} audio", n) if not r["is_short"]]
        return out

    # ---- download -----------------------------------------------------------
    def download(self, video_id: str, url: str, quality: str, short: bool, out_dir: Path,
                 progress: ProgressFn, should_stop: StopFn) -> dict:
        if self.mock:
            try:
                return mock.download(youtube_id(video_id), out_dir, self.cfg.data_dir / "mock-fixtures",
                                     self.ffmpeg, progress, should_stop,
                                     audio_fmt=quality if is_audio(quality) else None)
            except mock.MockStop as s:
                raise Stopped(s.what) from None
        import yt_dlp
        from yt_dlp.utils import DownloadCancelled

        class _Stop(DownloadCancelled):
            pass

        state = {"phase_idx": 0, "last_file": None}

        def hook(d: dict) -> None:
            stop = should_stop()
            if stop:
                state["stop"] = stop
                raise _Stop(stop)
            if d.get("status") not in ("downloading", "finished"):
                return
            fn = d.get("filename")
            if fn and fn != state["last_file"]:
                if state["last_file"] is not None:
                    state["phase_idx"] += 1
                state["last_file"] = fn
            info = d.get("info_dict") or {}
            is_audio = info.get("vcodec") == "none"
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = (done / total) if total else 0.0
            # "bv+ba" downloads video then audio. Weight video 0-90%, audio 90-99%.
            if is_audio and state["phase_idx"] > 0:
                pct = 90 + 9 * frac
            elif is_audio:
                pct = 99 * frac
            else:
                pct = 90 * frac
            progress({"downloaded_bytes": done, "total_bytes": total or None,
                      "speed": d.get("speed"), "eta": d.get("eta"),
                      "phase": "audio" if is_audio else "video", "percent": pct})

        def pp_hook(d: dict) -> None:
            if d.get("status") == "started" and d.get("postprocessor") in ("Merger", "FFmpegMerger"):
                progress({"phase": "merging", "percent": 99})
            elif d.get("status") == "started" and d.get("postprocessor") == "ExtractAudio":
                progress({"phase": "converting", "percent": 99})

        tmp_dir = out_dir / ".partial"
        fmt_opts = ydl_audio_opts(quality) if is_audio(quality) else ydl_format_opts(quality, short)
        opts = self._base_opts() | fmt_opts | {
            "paths": {"home": str(out_dir), "temp": str(tmp_dir)},
            "outtmpl": {"default": "%(id)s.%(ext)s"},
            "progress_hooks": [hook],
            "postprocessor_hooks": [pp_hook],
            "continuedl": True,
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 1,
            "overwrites": False,
        }
        if self.ffmpeg_dir:
            opts["ffmpeg_location"] = str(self.ffmpeg_dir)
        try:
            info = self._extract(url, opts, download=True)
        except DownloadCancelled:
            raise Stopped(state.get("stop", "cancel")) from None
        rd = (info.get("requested_downloads") or [{}])[0]
        path = rd.get("filepath") or rd.get("_filename")
        if not path or not Path(path).is_file():
            raise RuntimeError("yt-dlp finished but the output file is missing")
        subs = [] if is_audio(quality) or short else self._fetch_tracks(info)
        return {
            "subtitles": subs,
            "file_path": path, "width": info.get("width"), "height": info.get("height"),
            "vcodec": info.get("vcodec"), "acodec": info.get("acodec"),
            "title": info.get("title") or "", "channel": info.get("channel") or info.get("uploader") or "",
            "channel_id": info.get("channel_id") or "", "duration": info.get("duration"),
            "tags": (info.get("tags") or [])[:20], "file_size": Path(path).stat().st_size,
        }

    # ---- subtitles ----------------------------------------------------------
    def _fetch_tracks(self, info: dict) -> list[dict]:
        """Download the chosen caption tracks. Never raises: captions are a bonus."""
        if not self.cfg.subtitles:
            return []
        out = []
        try:
            tracks = subtitles.choose_tracks(info, self.cfg.subtitle_langs)
            if not tracks:
                return []
            import yt_dlp

            with yt_dlp.YoutubeDL(self._base_opts()) as ydl:
                for t in tracks:
                    try:
                        raw = ydl.urlopen(t["url"]).read().decode("utf-8", "replace")
                        out.append({"lang": t["lang"], "kind": t["kind"], "text": raw})
                    except Exception as e:
                        log.info("captions %s for %s failed: %s", t["lang"], info.get("id"), e)
        except Exception as e:
            log.info("captions for %s skipped: %s", info.get("id"), e)
        return out

    def fetch_subtitles(self, video_id: str, url: str) -> list[dict]:
        """Captions for a video that is already on disk (downloaded before 1.3)."""
        if self.mock:
            return mock.subtitles(youtube_id(video_id))
        info = self._extract(url, self._base_opts() | {"skip_download": True}, download=False)
        return self._fetch_tracks(info)

    # ---- thumbnails ---------------------------------------------------------
    def thumbnail(self, video_id: str, thumb_url: str, dest_dir: Path,
                  local_file: str | None = None, at: float | None = None) -> Path | None:
        """Return a cached thumbnail path, fetching/converting it on first use.

        If YouTube's image can't be fetched (offline, removed video) and the
        video is on disk, a frame from the file is used instead."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        jpg = dest_dir / f"{video_id}.jpg"
        svg = dest_dir / f"{video_id}.svg"
        if jpg.is_file():
            return jpg
        if svg.is_file():
            return svg
        if self.mock or thumb_url.startswith("mock://"):
            svg.write_text(mock.thumbnail_svg(youtube_id(video_id)))
            return svg
        url = thumb_url or thumb_url_for(youtube_id(video_id))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r, tempfile.NamedTemporaryFile(
                    dir=dest_dir, suffix=".src", delete=False) as tmp:
                shutil.copyfileobj(r, tmp)
                src = Path(tmp.name)
        except Exception as e:
            log.info("thumbnail fetch failed for %s: %s", video_id, e)
            return self._frame_thumb(local_file, at, jpg)
        try:
            if self.ffmpeg:
                subprocess.run(
                    [self.ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
                     "-vf", "scale=320:-2", "-q:v", "5", "-frames:v", "1", str(jpg)],
                    check=True, timeout=30)
            else:
                src.replace(jpg)
        except Exception:
            src.replace(jpg)  # keep the original rather than nothing
        finally:
            src.unlink(missing_ok=True)
        return jpg if jpg.is_file() else None

    def _frame_thumb(self, local_file: str | None, at: float | None, jpg: Path) -> Path | None:
        if not (self.ffmpeg and local_file and Path(local_file).is_file()):
            return None
        try:
            subprocess.run(
                [self.ffmpeg, "-y", "-loglevel", "error", "-ss", f"{max(0.0, at or 0):.1f}", "-i", local_file,
                 "-vf", "scale=320:-2", "-q:v", "5", "-frames:v", "1", str(jpg)],
                check=True, timeout=30)
        except Exception as e:
            log.info("frame thumbnail failed for %s: %s", jpg.stem, e)
        return jpg if jpg.is_file() and jpg.stat().st_size else None
