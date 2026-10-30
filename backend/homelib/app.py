"""FastAPI application: JSON API + the static frontend, in one process."""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import logging
import platform
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, cleaner, db, embeddings, interests, library, recommender, scheduler, subscriptions, subtitles
from .config import TAILSCALE_NET, Config, load_config
from .downloader import DownloadManager
from .streaming import stream_file
from .tools import find_ffmpeg, find_js_runtime, make_low_priority_wrappers, tool_version
from .ytlib import AUDIO_PREFIX, YouTube, extract_video_id, looks_like_url, youtube_id

log = logging.getLogger("homelib")
STATIC_DIR = Path(__file__).parent / "static"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# Each preset belongs to a scheme. A profile picks one dark and one light
# preset and a mode (dark, light, or "auto" = follow the iPhone's setting).
PRESETS = {
    "midnight": {"scheme": "dark", "bg": "#0F1115", "surface": "#181B21", "border": "#2A2F39", "text": "#F2F3F5",
                 "muted": "#A3A9B5", "progress": None, "accent": None},
    "oled": {"scheme": "dark", "bg": "#000000", "surface": "#0B0B0D", "border": "#1F2228", "text": "#F5F5F7",
             "muted": "#9AA0AA", "progress": None, "accent": None},
    "warm": {"scheme": "dark", "bg": "#17120E", "surface": "#221A14", "border": "#3A2D22", "text": "#F7EFE6",
             "muted": "#BFAE9C", "progress": None, "accent": None},
    "daylight": {"scheme": "light", "bg": "#F6F7F9", "surface": "#FFFFFF", "border": "#D9DDE3", "text": "#14171C",
                 "muted": "#5A6270", "progress": None, "accent": None},
    "linen": {"scheme": "light", "bg": "#F7F2EB", "surface": "#FFFCF7", "border": "#E2D8CA", "text": "#2A2018",
              "muted": "#6E5E4E", "progress": None, "accent": None},
}
THEME_KEYS = ("bg", "surface", "border", "text", "muted", "accent", "progress")
SHARED_KEYS = ("accent", "progress")  # the profile's own colours, same in dark and light
DEFAULT_PRESET = {"dark": "midnight", "light": "daylight"}


def _theme_state(raw: dict | None) -> dict:
    """Stored theme, upgraded from the pre-1.3 {preset, colors} shape."""
    t = dict(raw or {})
    if "mode" not in t:
        old_preset = t.get("preset", "midnight")
        scheme = PRESETS.get(old_preset, PRESETS["midnight"])["scheme"]
        old = t.get("colors") or {}
        t = {"mode": scheme,
             "dark": {"preset": "midnight", "colors": {}}, "light": {"preset": "daylight", "colors": {}},
             "accent": {k: v for k, v in old.items() if k in SHARED_KEYS}}
        t[scheme] = {"preset": old_preset if old_preset in PRESETS else DEFAULT_PRESET[scheme],
                     "colors": {k: v for k, v in old.items() if k not in SHARED_KEYS}}
    for sc in ("dark", "light"):
        t.setdefault(sc, {"preset": DEFAULT_PRESET[sc], "colors": {}})
    t.setdefault("accent", {})
    return t


# ---- request bodies -----------------------------------------------------------

class SelectBody(BaseModel):
    pass


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class DownloadBody(BaseModel):
    id: str | None = None
    url: str | None = None
    title: str | None = None
    channel: str | None = None
    channel_id: str | None = None
    duration: float | None = None
    thumb_url: str | None = None
    is_short: bool | None = None
    description: str | None = Field(default=None, max_length=2000)
    quality: Literal["best", "compat"] | None = None
    queue: Literal["personal", "shared", "none"] = "personal"


class QueueAddBody(BaseModel):
    video_id: str
    scope: Literal["personal", "shared"]


class ReorderBody(BaseModel):
    scope: Literal["personal", "shared"]
    item_ids: list[int]


class MoveBody(BaseModel):
    direction: int = Field(ge=-1, le=1)


class KeepBody(BaseModel):
    keep: bool


class RateBody(BaseModel):
    value: int = Field(ge=-1, le=1)


class OnBody(BaseModel):
    on: bool = True


class CandidateRateBody(BaseModel):
    video: dict
    value: int = Field(ge=-1, le=1)


class FeedbackBody(BaseModel):
    value: Literal["more", "less"] | None = None


class CandidateBody(BaseModel):
    video: dict


class InterestBody(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    weight: float | None = Field(default=None, ge=-5, le=5)
    pinned: bool = False


class InterestPatchBody(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=60)
    weight: float | None = Field(default=None, ge=-5, le=5)
    auto: bool = False          # back to the learned weight
    pinned: bool | None = None


class SubscribeBody(BaseModel):
    channel_id: str | None = None
    channel: str | None = None
    video_id: str | None = None  # to find the channel when the id isn't known


class SeenIdsBody(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=500)


class WatchBody(BaseModel):
    position: float = Field(ge=0)
    duration: float | None = None
    ended: bool = False
    skipped: bool = False


class SeenBody(BaseModel):
    seconds: float = 0
    duration: float | None = None


class AlgoBody(BaseModel):
    half_life_days: int | None = Field(default=None, ge=7, le=90)
    exploration: float | None = Field(default=None, ge=0, le=1)
    channel_loyalty: float | None = Field(default=None, ge=0, le=1)
    starter_topics: list[str] | None = None
    include_shorts_in_feed: bool | None = None


class TermBody(BaseModel):
    kind: Literal["kw", "ch"]
    term: str = Field(min_length=1, max_length=120)
    amount: float | None = None
    muted: bool | None = None
    forgotten: bool | None = None


class AppearanceBody(BaseModel):
    preset: str | None = None
    colors: dict[str, str | None] = {}
    mode: Literal["dark", "light", "auto"] | None = None
    scheme: Literal["dark", "light"] | None = None  # which palette `colors` edits


class SpeedBody(BaseModel):
    speed: float = Field(ge=0.25, le=3)


class WatchedBody(BaseModel):
    watched: bool


class PlaylistBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scope: Literal["personal", "shared"] = "personal"


class PlaylistRenameBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    scope: Literal["shared"] | None = None  # "Share with both of us" (one way)


class AvatarBody(BaseModel):
    """A profile photo as a data: URL or bare base64 (already resized in the app)."""
    data: str = Field(min_length=1, max_length=3_000_000)


class PlaylistAddBody(BaseModel):
    video_id: str


class ItemOrderBody(BaseModel):
    item_ids: list[int]


class SubtitleUploadBody(BaseModel):
    """A subtitle file read as text in the browser (no multipart dependency)."""
    content: str = Field(min_length=1, max_length=3_000_000)
    filename: str = ""
    lang: str = "en"
    label: str | None = None


class ShareBody(BaseModel):
    """From the iOS share-sheet shortcut (docs/IOS_SHORTCUT.md)."""
    url: str = Field(min_length=1, max_length=2000)
    profile: str | int | None = None   # profile name ("Profile 1") or id; default: first profile
    action: str = "video-personal"     # video-personal | video-shared | video | music | mp3 | m4a


class MusicDownloadBody(BaseModel):
    id: str
    title: str | None = None
    channel: str | None = None
    duration: float | None = None
    thumb_url: str | None = None
    url: str | None = None
    format: Literal["mp3", "m4a"] | None = None


class SettingsBody(BaseModel):
    default_quality: Literal["best", "compat"] | None = None
    music_format: Literal["mp3", "m4a"] | None = None
    auto_delete_hours: Literal[12, 24, 48] | None = None


# ---- app factory ----------------------------------------------------------------

def create_app(cfg: Config | None = None, start_workers: bool = True, start_scheduler: bool = True) -> FastAPI:
    cfg = cfg or load_config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.library_dir.mkdir(parents=True, exist_ok=True)
    cfg.thumbs_dir.mkdir(parents=True, exist_ok=True)
    db.init(cfg.db_path)
    library.ensure_profiles(cfg.profile_names)
    recommender.configure(cfg)

    ffmpeg, ff_src = find_ffmpeg(cfg.ffmpeg_path)
    ff_dir = make_low_priority_wrappers(cfg.data_dir / "bin", ffmpeg, cfg.ffmpeg_nice) if ffmpeg else None
    js = find_js_runtime(cfg.js_runtime_path)
    yt = YouTube(cfg, ffmpeg, ff_dir, js)
    dm = DownloadManager(cfg, yt)
    sched = scheduler.Scheduler(cfg, yt, dm)
    started = time.time()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_workers:
            dm.start()
        if start_scheduler:
            sched.start()
        if not ffmpeg:
            log.error("ffmpeg not found: downloads that need merging will fail")
        if not cfg.mock and not js[0]:
            log.warning("no JavaScript runtime (deno/node) found; YouTube downloads may fail")
        log.info("Home Video Library %s ready (mock=%s, ffmpeg=%s, js=%s)", __version__, cfg.mock, ff_src, js[0])
        yield
        dm.stop()
        sched.stop()

    app = FastAPI(title="Home Video Library", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.cfg, app.state.yt, app.state.dm, app.state.sched = cfg, yt, dm, sched

    @app.exception_handler(library.NotFound)
    async def _nf(_, e):
        return JSONResponse({"detail": f"not found: {e}"}, status_code=404)

    @app.exception_handler(library.DiskFull)
    async def _df(_, e):
        return JSONResponse({"detail": str(e), "code": "disk_full"}, status_code=507)

    @app.exception_handler(subtitles.SubtitleError)
    async def _se(_, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _ve(_, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    def profile_id(request: Request) -> int:
        raw = request.headers.get("x-profile") or request.cookies.get("hl_profile") or "1"
        try:
            pid = int(raw)
        except ValueError:
            pid = 1
        return pid if pid in library.profile_ids() else 1

    def quality_default() -> str:
        return db.get_setting("global", "default_quality", cfg.default_quality)

    # ---- profiles ----------------------------------------------------------------
    @app.get("/api/profiles")
    def get_profiles():
        return [{**p, "theme": theme_for(p["id"])} for p in library.profiles()]

    @app.post("/api/profiles/{pid}/select")
    def select_profile(pid: int, response: Response):
        p = library.get_profile(pid)
        response.set_cookie("hl_profile", str(pid), max_age=10 * 365 * 86400, samesite="lax")
        return {**p, "theme": theme_for(pid)}

    @app.patch("/api/profiles/{pid}")
    def rename(pid: int, body: RenameBody):
        return library.rename_profile(pid, body.name)

    @app.put("/api/profiles/{pid}/avatar")
    def put_avatar(pid: int, body: AvatarBody):
        raw = body.data.split(",", 1)[1] if body.data.startswith("data:") else body.data
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "That photo couldn't be read. Try another one.")
        return library.set_avatar(pid, data, cfg.avatars_dir)

    @app.delete("/api/profiles/{pid}/avatar")
    def delete_avatar(pid: int):
        return library.remove_avatar(pid, cfg.avatars_dir)

    @app.get("/api/profiles/{pid}/avatar")
    def get_avatar(pid: int):
        f = library.avatar_file(cfg.avatars_dir, pid)
        if not f:
            raise HTTPException(404, "no photo")
        media = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[f.suffix[1:]]
        # The URL carries ?v=<time it was set>, so it can be cached for long.
        return FileResponse(f, media_type=media, headers={"Cache-Control": "private, max-age=31536000"})

    # ---- search ------------------------------------------------------------------
    @app.get("/api/search")
    def search(q: str, n: int = 15, pid: int = Depends(profile_id)):
        q = q.strip()
        if not q:
            return {"results": []}
        try:
            if looks_like_url(q):
                r = yt.lookup(q)
                results = [r] if r else []
            else:
                results = yt.search(q, max(1, min(n, 30)))
                library.log_search(pid, q)
        except Exception as e:
            log.warning("search failed: %s", e)
            raise HTTPException(502, f"YouTube search failed: {str(e).splitlines()[0][:200]}")
        ids = [r["id"] for r in results]
        local = {}
        if ids:
            # Someone else's private download counts as "not downloaded" here.
            local = {v["id"]: v["status"] for v in db.q(
                f"SELECT id, status FROM videos WHERE id IN ({','.join('?' * len(ids))})", ids)
                if v["status"] not in ("ready", "queued", "downloading") or library.visible_to(v["id"], pid)}
        for r in results:
            r["thumb"] = f"/api/thumb/{r['id']}"
            r["local_status"] = local.get(r["id"])
            _remember_thumb(r)
        return {"results": results}

    def _remember_thumb(meta: dict) -> None:
        # The thumb endpoint needs the source URL for not-yet-downloaded videos.
        _thumb_urls[meta["id"]] = meta.get("thumb_url") or ""
        if len(_thumb_urls) > 2000:
            _thumb_urls.pop(next(iter(_thumb_urls)))

    _thumb_urls: dict[str, str] = {}

    # ---- downloads ---------------------------------------------------------------
    @app.post("/api/downloads")
    def start_download(body: DownloadBody, pid: int = Depends(profile_id)):
        vid = body.id or (extract_video_id(body.url or "") if body.url else None)
        if not vid:
            raise HTTPException(400, "id or url required")
        meta = body.model_dump(exclude={"quality", "queue"}, exclude_none=True)
        meta["id"] = vid
        if not body.title:
            looked = yt.lookup(body.url or vid)
            if not looked:
                raise HTTPException(404, "video not found")
            meta = {**looked, **meta}
        scope = None if body.queue == "none" else body.queue
        return dm.enqueue(meta, body.quality or quality_default(), "user", pid, scope)

    @app.get("/api/downloads")
    def list_downloads(background: bool = True, pid: int = Depends(profile_id)):
        rows = [d for d in dm.list(include_background=background)
                if d["kind"] == "background" or d["requested_by"] == pid or library.visible_to(d["video_id"], pid)]
        return {"downloads": rows, "counts": dm.counts()}

    @app.post("/api/downloads/{did}/{action}")
    def control(did: int, action: Literal["pause", "resume", "cancel"]):
        return {"pause": dm.pause, "resume": dm.resume, "cancel": dm.cancel}[action](did)

    @app.post("/api/downloads/clear")
    def clear_downloads():
        return {"cleared": dm.clear_finished()}

    # ---- music -------------------------------------------------------------------
    @app.get("/api/music/search")
    def music_search(q: str, n: int = 15):
        q = q.strip()
        if not q:
            return {"results": []}
        try:
            if looks_like_url(q):
                r = yt.lookup(q)
                results = [r] if r else []
            else:
                results = yt.search_music(q, max(1, min(n, 30)))
        except Exception as e:
            log.warning("music search failed: %s", e)
            raise HTTPException(502, f"YouTube Music search failed: {str(e).splitlines()[0][:200]}")
        ids = [AUDIO_PREFIX + r["id"] for r in results]
        local = {}
        if ids:
            local = {v["id"]: v["status"] for v in db.q(
                f"SELECT id, status FROM videos WHERE id IN ({','.join('?' * len(ids))})", ids)}
        for r in results:
            r["thumb"] = f"/api/thumb/{AUDIO_PREFIX}{r['id']}"
            r["local_status"] = local.get(AUDIO_PREFIX + r["id"])
            _thumb_urls[AUDIO_PREFIX + r["id"]] = r.get("thumb_url") or ""
        return {"results": results}

    @app.post("/api/music/download")
    def music_download(body: MusicDownloadBody, pid: int = Depends(profile_id)):
        yid = youtube_id(body.id)
        meta = {"id": AUDIO_PREFIX + yid, "media": "audio", "is_short": False,
                "url": body.url or f"https://music.youtube.com/watch?v={yid}",
                **body.model_dump(exclude={"id", "format", "url"}, exclude_none=True)}
        if not body.title:
            looked = yt.lookup(yid)
            if not looked:
                raise HTTPException(404, "song not found")
            meta = {**{k: v for k, v in looked.items() if k in ("title", "channel", "duration", "thumb_url")}, **meta}
        fmt = body.format or db.get_setting("global", "music_format", "mp3")
        return dm.enqueue(meta, fmt, "user", pid, None)

    @app.get("/api/music")
    def music_library():
        return {"tracks": library.music_list()}

    @app.get("/api/music/{vid}/file")
    def music_file(vid: str, request: Request):
        v = library.get_video(vid)
        if v.get("media") != "audio" or v["status"] != "ready" or not v.get("file_path") \
                or not Path(v["file_path"]).is_file():
            raise HTTPException(404, "not downloaded")
        return stream_file(Path(v["file_path"]), request, download_name=library.download_name(v))

    # ---- iOS share-sheet shortcut --------------------------------------------------
    SHARE_ACTIONS = {"video-personal": ("video", "personal"), "video": ("video", "personal"),
                     "video-shared": ("video", "shared"), "video-none": ("video", "none"),
                     "music": ("music", None), "mp3": ("music", "mp3"), "m4a": ("music", "m4a")}

    def _share_error(status: int, message: str) -> JSONResponse:
        # Shortcuts shows the body even on errors, so always include "message".
        return JSONResponse({"ok": False, "message": message, "detail": message}, status_code=status)

    @app.post("/api/share")
    def share(body: ShareBody, request: Request):
        """One call from the iOS Share Sheet: look up the link and start the download."""
        action = SHARE_ACTIONS.get(body.action.strip().lower())
        if not action:
            return _share_error(400, f"Unknown action '{body.action}'. Use video-personal, video-shared or music.")
        if body.profile in (None, ""):
            pid = profile_id(request)
        else:
            wanted = str(body.profile).strip().lower()
            match = [p for p in library.profiles() if str(p["id"]) == wanted or p["name"].lower() == wanted]
            if not match:
                names = ", ".join(p["name"] for p in library.profiles())
                return _share_error(400, f"No profile called '{body.profile}'. Profiles: {names}.")
            pid = match[0]["id"]
        # The share sheet sometimes passes text around the link ("Check this out https://…").
        m = re.search(r"https?://\S+", body.url)
        link = m.group(0) if m else body.url.strip()
        if not extract_video_id(link):
            return _share_error(400, "That doesn't look like a YouTube video link.")
        try:
            meta = yt.lookup(link)
        except Exception as e:
            return _share_error(502, f"YouTube lookup failed: {str(e).splitlines()[0][:150]}")
        if not meta:
            return _share_error(404, "Video not found.")
        kind, opt = action
        try:
            if kind == "music":
                d = music_download(MusicDownloadBody(id=meta["id"], title=meta["title"], channel=meta["channel"],
                                                     duration=meta.get("duration"), thumb_url=meta.get("thumb_url"),
                                                     format=opt), pid)
                where = f"Music ({d['quality'].upper()})"
            else:
                d = start_download(DownloadBody(**{k: v for k, v in meta.items() if k in DownloadBody.model_fields},
                                                queue=opt), pid)
                where = {"personal": "My queue", "shared": "Shared queue", "none": "the library"}[opt]
        except library.DiskFull as e:
            return _share_error(507, str(e))
        name = library.get_profile(pid)["name"]
        verb = "Already downloaded" if d and d.get("status") == "done" else "Downloading"
        return {"ok": True, "message": f"{verb}: “{meta['title']}” → {where} ({name})",
                "video_id": d["video_id"] if d else meta["id"], "status": d["status"] if d else None}

    # ---- library / videos ----------------------------------------------------------
    @app.get("/api/library")
    def get_library(shorts: bool = False, pid: int = Depends(profile_id)):
        hours = scheduler.current_auto_delete_hours(cfg)
        items = library.library(include_shorts=shorts, profile_id=pid)
        for v in items:
            v["expires_at"] = cleaner.expires_at(v, hours)
        return {"videos": items, "auto_delete_hours": hours}

    @app.get("/api/videos/{vid}")
    def get_video(vid: str, pid: int = Depends(profile_id)):
        v = library.decorate_video(library.get_video(vid))
        v["expires_at"] = cleaner.expires_at(v, scheduler.current_auto_delete_hours(cfg))
        v["feedback"] = library.get_feedback(pid, vid)
        v["subscribed"] = bool(v.get("channel_id")) and v["channel_id"] in subscriptions.channel_ids(pid)
        return v

    @app.post("/api/videos/{vid}/feedback")
    def video_feedback(vid: str, body: FeedbackBody, pid: int = Depends(profile_id)):
        """"More like this" / "Less like this" (the end-of-video prompt)."""
        return library.feedback(pid, vid, body.value)

    @app.delete("/api/videos/{vid}")
    def delete_video(vid: str, pid: int = Depends(profile_id)):
        return library.delete_video(vid, "manual", pid, cfg.library_dir, cfg.thumbs_dir)

    @app.post("/api/videos/{vid}/share")
    def share_video(vid: str, pid: int = Depends(profile_id)):
        v = library.share_video(vid, pid)
        v["expires_at"] = cleaner.expires_at(v, scheduler.current_auto_delete_hours(cfg))
        return v

    @app.post("/api/videos/{vid}/keep")
    def keep(vid: str, body: KeepBody):
        return library.decorate_video(library.set_keep(vid, body.keep))

    @app.post("/api/videos/{vid}/rate")
    def rate(vid: str, body: RateBody, pid: int = Depends(profile_id)):
        return library.rate(pid, vid, body.value)

    @app.post("/api/videos/{vid}/like")
    def like(vid: str, body: OnBody = OnBody(), pid: int = Depends(profile_id)):
        return library.rate(pid, vid, 1 if body.on else 0)

    @app.post("/api/videos/{vid}/dislike")
    def dislike(vid: str, body: OnBody = OnBody(), pid: int = Depends(profile_id)):
        return library.rate(pid, vid, -1 if body.on else 0)

    @app.post("/api/candidates/rate")
    def rate_candidate(body: CandidateRateBody, pid: int = Depends(profile_id)):
        if "id" not in body.video:
            raise HTTPException(400, "video.id required")
        library.rate_candidate(pid, body.video, body.value)
        return {"ok": True}

    @app.post("/api/candidates/not-interested")
    def candidate_not_interested(body: CandidateBody, pid: int = Depends(profile_id)):
        if "id" not in body.video:
            raise HTTPException(400, "video.id required")
        library.not_interested(pid, body.video)
        return {"ok": True}

    @app.delete("/api/not-interested/{vid}")
    def undo_not_interested(vid: str, pid: int = Depends(profile_id)):
        library.undo_not_interested(pid, vid)
        return {"ok": True}

    # ---- queue ---------------------------------------------------------------------
    @app.get("/api/queue")
    def get_queue(scope: Literal["personal", "shared"] = "personal", pid: int = Depends(profile_id)):
        return {"scope": scope, "items": library.queue_list(scope, pid)}

    @app.post("/api/queue")
    def add_queue(body: QueueAddBody, pid: int = Depends(profile_id)):
        return library.queue_add(body.video_id, body.scope, pid)

    @app.post("/api/queue/reorder")
    def reorder(body: ReorderBody, pid: int = Depends(profile_id)):
        return {"items": library.queue_reorder(body.scope, pid, body.item_ids)}

    @app.post("/api/queue/{item_id}/move")
    def move(item_id: int, body: MoveBody, pid: int = Depends(profile_id)):
        library.queue_move(item_id, body.direction, pid)
        return {"ok": True}

    @app.delete("/api/queue/{item_id}")
    def remove(item_id: int):
        library.queue_remove(item_id)
        return {"ok": True}

    # ---- continue watching & playlists ---------------------------------------------
    @app.get("/api/continue")
    def continue_watching(pid: int = Depends(profile_id)):
        return {"items": library.continue_watching(pid)}

    @app.get("/api/playlists")
    def list_playlists(pid: int = Depends(profile_id)):
        return {"playlists": library.playlists(pid)}

    @app.post("/api/playlists")
    def create_playlist(body: PlaylistBody, pid: int = Depends(profile_id)):
        return library.create_playlist(body.name, body.scope, pid)

    @app.get("/api/playlists/{plid}")
    def get_playlist(plid: int, pid: int = Depends(profile_id)):
        return library.get_playlist(plid, pid)

    @app.patch("/api/playlists/{plid}")
    def rename_playlist(plid: int, body: PlaylistRenameBody, pid: int = Depends(profile_id)):
        pl = None
        if body.name is not None:
            pl = library.rename_playlist(plid, body.name, pid)
        if body.scope == "shared":
            pl = library.share_playlist(plid, pid)
        return pl or library.get_playlist(plid, pid)

    @app.delete("/api/playlists/{plid}")
    def delete_playlist(plid: int, pid: int = Depends(profile_id)):
        library.delete_playlist(plid, pid)
        return {"ok": True}

    @app.post("/api/playlists/{plid}/items")
    def playlist_add(plid: int, body: PlaylistAddBody, pid: int = Depends(profile_id)):
        return library.playlist_add(plid, body.video_id, pid)

    @app.delete("/api/playlists/{plid}/items/{item_id}")
    def playlist_remove(plid: int, item_id: int, pid: int = Depends(profile_id)):
        return library.playlist_remove(plid, item_id, pid)

    @app.post("/api/playlists/{plid}/reorder")
    def playlist_reorder(plid: int, body: ItemOrderBody, pid: int = Depends(profile_id)):
        return library.playlist_reorder(plid, body.item_ids, pid)

    @app.get("/api/videos/{vid}/playlists")
    def video_playlists(vid: str, pid: int = Depends(profile_id)):
        return {"playlist_ids": library.playlists_containing(vid, pid)}

    # ---- delete after watching -----------------------------------------------------
    @app.get("/api/videos/{vid}/delete-status")
    def delete_status(vid: str):
        library.get_video(vid)
        return library.delete_status(vid)

    @app.post("/api/videos/{vid}/delete-vote")
    def delete_vote(vid: str, pid: int = Depends(profile_id)):
        return library.vote_delete(pid, vid, cfg.library_dir, cfg.thumbs_dir)

    @app.delete("/api/videos/{vid}/delete-vote")
    def delete_unvote(vid: str, pid: int = Depends(profile_id)):
        return library.unvote_delete(pid, vid)

    # ---- captions & transcripts ------------------------------------------------------
    @app.get("/api/videos/{vid}/subtitles")
    def list_subtitles(vid: str):
        return {"subtitles": library.list_subtitles(vid)}

    @app.post("/api/videos/{vid}/subtitles")
    def upload_subtitle(vid: str, body: SubtitleUploadBody):
        library.get_video(vid)
        raw = subtitles.decode_upload(body.content)
        stem = Path(body.filename).stem[:40] if body.filename else ""
        # "movie.srt" -> "movie"; a bare language code ("es.srt") -> "Spanish".
        label = body.label or (stem if len(stem) > 3 and stem.lower() != body.lang.lower() else None)
        return library.save_subtitle(vid, body.lang, "uploaded", raw, cfg.subs_dir, label)

    @app.post("/api/videos/{vid}/subtitles/fetch")
    def fetch_subtitles(vid: str):
        v = library.get_video(vid)
        if v["status"] != "ready" or v.get("media") != "video":
            raise HTTPException(404, "not downloaded")
        try:
            tracks = yt.fetch_subtitles(vid, v["url"])
        except Exception as e:
            log.info("caption fetch for %s failed: %s", vid, e)
            raise HTTPException(502, "Couldn't reach YouTube for captions. Try again later.")
        n = library.save_downloaded_subtitles(vid, tracks, cfg.subs_dir)
        return {"added": n, "subtitles": library.list_subtitles(vid)}

    @app.get("/api/subtitles/{name}")
    def subtitle_file(name: str):
        m = re.fullmatch(r"(\d+)\.vtt", name)
        if not m:
            raise HTTPException(404, "not found")
        r = library.get_subtitle(int(m.group(1)))
        return FileResponse(r["path"], media_type="text/vtt; charset=utf-8",
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/subtitles/{sid}/transcript")
    def get_transcript(sid: int):
        return library.transcript(sid)

    @app.delete("/api/subtitles/{sid}")
    def delete_subtitle(sid: int):
        library.delete_subtitle(sid)
        return {"ok": True}

    # ---- streaming & thumbnails --------------------------------------------------
    @app.api_route("/api/stream/{vid}", methods=["GET", "HEAD"])
    def stream(vid: str, request: Request):
        v = library.get_video(vid)
        if v["status"] != "ready" or not v.get("file_path") or not Path(v["file_path"]).is_file():
            raise HTTPException(404, "not downloaded")
        return stream_file(Path(v["file_path"]), request)

    @app.get("/api/thumb/{vid}")
    def thumb(vid: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid):
            raise HTTPException(400, "bad id")
        src = _thumb_urls.get(vid)
        row = db.q1("SELECT thumb_url, file_path, status, duration, media FROM videos WHERE id=?", (vid,))
        if src is None:
            src = row["thumb_url"] if row else ""
        local = row["file_path"] if row and row["status"] == "ready" and row["media"] == "video" else None
        p = yt.thumbnail(vid, src or "", cfg.thumbs_dir, local_file=local,
                         at=((row or {}).get("duration") or 0) * 0.1)
        if not p:
            raise HTTPException(404, "no thumbnail")
        media = "image/svg+xml" if p.suffix == ".svg" else "image/jpeg"
        return FileResponse(p, media_type=media, headers={"Cache-Control": "public, max-age=604800"})

    # ---- watch state ---------------------------------------------------------------
    @app.get("/api/watch/{vid}")
    def get_watch(vid: str, pid: int = Depends(profile_id)):
        return library.get_watch(pid, vid)

    @app.post("/api/watch/{vid}")
    def post_watch(vid: str, body: WatchBody, pid: int = Depends(profile_id)):
        if body.skipped:
            library.mark_skip(pid, vid)
            return library.get_watch(pid, vid)
        return library.update_progress(pid, vid, body.position, body.duration, body.ended)

    @app.put("/api/watch/{vid}/speed")
    def put_speed(vid: str, body: SpeedBody, pid: int = Depends(profile_id)):
        return library.set_speed(pid, vid, body.speed)

    @app.post("/api/watch/{vid}/watched")
    def post_watched(vid: str, body: WatchedBody, pid: int = Depends(profile_id)):
        return library.set_watched(pid, vid, body.watched)

    # ---- For you & Shorts ----------------------------------------------------------
    @app.get("/api/foryou")
    def foryou(mode: Literal["me", "both"] = "me", limit: int = 40, pid: int = Depends(profile_id)):
        include_shorts = recommender.algo_settings(pid).get("include_shorts_in_feed")
        items = recommender.rank(pid, mode, limit=limit, shorts=None if include_shorts else False)
        recommender.remember_shown(pid, mode, [c["id"] for c in items])
        for c in items:
            _remember_thumb(c)
        return {"mode": mode, "items": items,
                "last_fetch": db.get_setting("global", "last_nightly", None),
                "fetching": sched.running_job == "candidate fetch" or recommender.job_status()["running"]}

    @app.get("/api/foryou/why/{vid}")
    def why(vid: str, mode: Literal["me", "both"] = "me", pid: int = Depends(profile_id)):
        return recommender.explain(pid, vid, mode)

    @app.post("/api/foryou/refresh")
    def refresh(mode: Literal["me", "both"] = "me", pid: int = Depends(profile_id)):
        """Runs in the background; answers when done or after ~20 s with
        {"running": true} (the app then polls /api/foryou/status)."""
        return recommender.reload_feed(yt, pid, mode)

    @app.get("/api/foryou/status")
    def refresh_status():
        return recommender.job_status()

    # ---- subscriptions -------------------------------------------------------------
    @app.get("/api/subscriptions")
    def list_subscriptions(pid: int = Depends(profile_id)):
        chans = subscriptions.uploads_for(pid)
        for c in chans:
            for u in c["uploads"]:
                _remember_thumb(u)
        return {"channels": chans, "new_count": sum(c["new_count"] for c in chans),
                "last_check": db.get_setting("global", "last_nightly", None)}

    @app.post("/api/subscriptions")
    def subscribe(body: SubscribeBody, pid: int = Depends(profile_id)):
        cid, name = body.channel_id or "", body.channel or ""
        if not subscriptions.is_channel_id(cid):
            row = db.q1("SELECT channel_id, channel FROM videos WHERE id=? AND channel_id<>''", (body.video_id or "",))
            if row:
                cid, name = row["channel_id"], name or row["channel"]
            elif body.video_id:
                try:
                    looked = yt.lookup(body.video_id)
                except Exception as e:
                    raise HTTPException(502, f"YouTube lookup failed: {str(e).splitlines()[0][:150]}")
                if looked:
                    cid, name = looked.get("channel_id") or "", name or looked.get("channel", "")
        sub = subscriptions.subscribe(pid, cid, name)
        # Fetch its latest uploads now (one request), so the tab isn't empty.
        try:
            subscriptions.refresh(yt, [pid], budget=recommender.Budget(1), thumbs_dir=cfg.thumbs_dir)
        except Exception as e:
            log.info("first check of %s failed: %s", cid, e)
        return {**sub, "subscribed": True}

    @app.delete("/api/subscriptions/{channel_id}")
    def unsubscribe(channel_id: str, pid: int = Depends(profile_id)):
        subscriptions.unsubscribe(pid, channel_id)
        return {"ok": True, "subscribed": False}

    @app.post("/api/subscriptions/seen")
    def subscriptions_seen(body: SeenIdsBody, pid: int = Depends(profile_id)):
        return {"marked": subscriptions.mark_seen(pid, body.ids)}

    @app.get("/api/subscriptions/new-count")
    def subscriptions_new(pid: int = Depends(profile_id)):
        return {"new_count": subscriptions.new_count(pid)}

    @app.get("/api/channels/{channel_id}")
    def channel_view(channel_id: str, name: str = "", pid: int = Depends(profile_id)):
        """A channel's latest uploads (stored ones if someone subscribes, else one flat lookup)."""
        if not subscriptions.is_channel_id(channel_id):
            raise HTTPException(404, "unknown channel")
        subscribed = channel_id in subscriptions.channel_ids(pid)
        stored = subscriptions.uploads_for(pid, channel_id) if subscribed else []
        if stored:
            ch = stored[0]
            return {"channel_id": channel_id, "channel": ch["channel"], "subscribed": True, "videos": ch["uploads"]}
        try:
            vids = recommender.cached_channel(yt, channel_id, name, 15, None, time.time() - recommender.SEARCH_TTL)
        except Exception as e:
            raise HTTPException(502, f"Couldn't load the channel: {str(e).splitlines()[0][:150]}")
        for v in vids or []:
            v["thumb"] = f"/api/thumb/{v['id']}"
            _remember_thumb(v)
        name = name or next((v.get("channel") for v in vids or [] if v.get("channel")), "") or channel_id
        return {"channel_id": channel_id, "channel": name, "subscribed": subscribed, "videos": vids or []}

    @app.get("/api/shorts")
    def shorts(pid: int = Depends(profile_id)):
        s = recommender.algo_settings(pid)
        prof = recommender.build_profile(pid, s)
        rows = db.q("SELECT * FROM videos WHERE is_short=1 AND status='ready' AND id NOT IN "
                    "(SELECT video_id FROM watch_state WHERE profile_id=? AND watched=1) "
                    "ORDER BY downloaded_at", (pid,))
        out = []
        for v in rows:
            v = library.decorate_video(v)
            sc = recommender.score(v, prof, s)
            if sc is None:
                continue
            v["score"], v["reasons"] = sc["score"], sc["reasons"]
            out.append(v)
        out.sort(key=lambda v: -v["score"])
        pending = db.q1("SELECT COUNT(*) n FROM videos WHERE is_short=1 AND status IN ('queued','downloading')")["n"]
        return {"items": out, "downloading": pending, "target": cfg.shorts_prefetch}

    @app.post("/api/shorts/{vid}/seen")
    def short_seen(vid: str, body: SeenBody, pid: int = Depends(profile_id)):
        if body.seconds < 2:
            library.mark_skip(pid, vid)
        else:
            library.update_progress(pid, vid, body.seconds, body.duration, ended=True)
        return {"ok": True}

    @app.post("/api/shorts/prefetch")
    def shorts_prefetch():
        return {"queued": scheduler.prefetch_shorts(cfg, yt, dm)}

    # ---- algorithm ---------------------------------------------------------------
    @app.get("/api/algorithm")
    def algorithm(pid: int = Depends(profile_id)):
        s = recommender.algo_settings(pid)
        prof = recommender.build_profile(pid, s)
        events = db.q("SELECT id, video_id, type, weight, keywords, channel, data, ts FROM events "
                      "WHERE profile_id=? AND type<>'deleted' ORDER BY ts DESC LIMIT 200", (pid,))
        for e in events:
            e["keywords"], e["data"] = json.loads(e["keywords"]), json.loads(e["data"])
        def as_list(d):
            return sorted(({"term": k, **v} for k, v in d.items()), key=lambda t: -abs(t["weight"]))
        return {"settings": s, "keywords": as_list(prof["kw"]), "channels": as_list(prof["ch"]),
                "events": events, "event_weights": library.EVENT_WEIGHTS, "signal_strengths": interests.SIGNALS,
                **interest_state(pid, s), "embeddings": embeddings.status()}

    def interest_state(pid: int, s: dict) -> dict:
        rows = interests.ensure(pid, s)
        hidden = [r for r in interests.rows(pid, include_hidden=True) if r["hidden"]]
        return {"interests": sorted((interests.view(r) for r in rows), key=lambda r: (not r["pinned"], -r["weight"])),
                "hidden_interests": [interests.view(r) for r in hidden],
                "blocked": {"topics": [m[3:] for m in s["mutes"] if m.startswith("kw:")],
                            "channels": [m[3:] for m in s["mutes"] if m.startswith("ch:")]},
                "not_interested": library.not_interested_list(pid),
                "subscriptions": db.q("SELECT channel_id, channel FROM subscriptions WHERE profile_id=? "
                                      "ORDER BY channel COLLATE NOCASE", (pid,))}

    # ---- the interest editor ------------------------------------------------------
    @app.get("/api/interests")
    def get_interests(pid: int = Depends(profile_id)):
        s = recommender.algo_settings(pid)
        return {**interest_state(pid, s), "embeddings": embeddings.status()}

    @app.post("/api/interests")
    def add_interest(body: InterestBody, pid: int = Depends(profile_id)):
        w = interests.MANUAL_DEFAULT if body.weight is None else body.weight
        return interests.add_topic(pid, body.label, w, pinned=body.pinned)

    @app.patch("/api/interests/{iid}")
    def patch_interest(iid: int, body: InterestPatchBody, pid: int = Depends(profile_id)):
        return interests.update(pid, iid, label=body.label, weight=body.weight, auto=body.auto, pinned=body.pinned)

    @app.delete("/api/interests/{iid}")
    def delete_interest(iid: int, pid: int = Depends(profile_id)):
        interests.delete(pid, iid)
        return {"ok": True}

    @app.post("/api/interests/{iid}/restore")
    def restore_interest(iid: int, pid: int = Depends(profile_id)):
        return interests.restore(pid, iid)

    @app.post("/api/interests/{iid}/block")
    def block_interest(iid: int, pid: int = Depends(profile_id)):
        recommender.block_interest(pid, iid)
        return {"ok": True}

    @app.put("/api/algorithm")
    def set_algorithm(body: AlgoBody, pid: int = Depends(profile_id)):
        return recommender.save_algo_settings(pid, body.model_dump(exclude_none=True))

    @app.post("/api/algorithm/boost")
    def boost(body: TermBody, pid: int = Depends(profile_id)):
        amt = recommender.BOOST_DEFAULT if body.amount is None else body.amount
        return recommender.set_boost(pid, body.kind, body.term, amt)

    @app.post("/api/algorithm/mute")
    def mute(body: TermBody, pid: int = Depends(profile_id)):
        return recommender.set_mute(pid, body.kind, body.term, True if body.muted is None else body.muted)

    @app.post("/api/algorithm/forget")
    def forget(body: TermBody, pid: int = Depends(profile_id)):
        return recommender.set_forgotten(pid, body.kind, body.term,
                                         True if body.forgotten is None else body.forgotten)

    @app.delete("/api/algorithm/events/{eid}")
    def delete_event(eid: int, pid: int = Depends(profile_id)):
        n = db.x("DELETE FROM events WHERE id=? AND profile_id=?", (eid, pid)).rowcount
        if not n:
            raise HTTPException(404, "event not found")
        return {"ok": True}

    @app.get("/api/algorithm/export")
    def export(pid: int = Depends(profile_id)):
        data = recommender.export(pid)
        name = re.sub(r"[^A-Za-z0-9]+", "-", library.get_profile(pid)["name"]).strip("-") or f"profile{pid}"
        return JSONResponse(data, headers={
            "Content-Disposition": f'attachment; filename="homelib-algorithm-{name}.json"'})

    @app.post("/api/algorithm/reset")
    def reset(pid: int = Depends(profile_id)):
        recommender.reset(pid)
        return {"ok": True}

    # ---- appearance & settings ---------------------------------------------------
    def theme_for(pid: int) -> dict:
        t = _theme_state(db.get_setting(f"profile:{pid}", "theme", None))
        accent = library.get_profile(pid)["accent"]

        def palette(scheme: str) -> dict:
            preset = t[scheme].get("preset") or DEFAULT_PRESET[scheme]
            if PRESETS.get(preset, {}).get("scheme") != scheme:
                preset = DEFAULT_PRESET[scheme]
            base = {k: v for k, v in PRESETS[preset].items() if k != "scheme"}
            base["accent"] = accent
            base["progress"] = accent
            custom = {**(t[scheme].get("colors") or {}), **t["accent"]}
            base.update({k: v for k, v in custom.items() if v})
            return {"preset": preset, "colors": base, "custom": custom}

        dark, light = palette("dark"), palette("light")
        active = light if t["mode"] == "light" else dark  # "auto": the phone picks
        return {"mode": t["mode"], "preset": active["preset"], "colors": active["colors"],
                "custom": active["custom"], "dark": dark, "light": light}

    @app.get("/api/appearance")
    def get_appearance(pid: int = Depends(profile_id)):
        return {**theme_for(pid), "presets": PRESETS}

    @app.put("/api/appearance")
    def put_appearance(body: AppearanceBody, pid: int = Depends(profile_id)):
        cur = _theme_state(db.get_setting(f"profile:{pid}", "theme", None))
        if body.mode is not None:
            cur["mode"] = body.mode
        if body.preset is not None:
            if body.preset not in PRESETS:
                raise HTTPException(400, "unknown preset")
            scheme = PRESETS[body.preset]["scheme"]
            cur[scheme] = {"preset": body.preset, "colors": {}}  # choosing a preset resets custom colours
            cur["accent"] = {k: v for k, v in cur["accent"].items() if k == "accent"}
            if cur["mode"] != "auto" and body.mode is None:
                cur["mode"] = scheme  # picking a light preset means "I want light"
        target = body.scheme or ("light" if cur["mode"] == "light" else "dark")
        for k, v in body.colors.items():
            if k not in THEME_KEYS:
                raise HTTPException(400, f"unknown colour {k}")
            bucket = cur["accent"] if k in SHARED_KEYS else cur[target].setdefault("colors", {})
            if v is None:
                bucket.pop(k, None)
            elif not HEX.match(v):
                raise HTTPException(400, f"{k} must be #RRGGBB")
            else:
                bucket[k] = v.upper()
        db.set_setting(f"profile:{pid}", "theme", cur)
        if cur["accent"].get("accent"):
            db.x("UPDATE profiles SET accent=? WHERE id=?", (cur["accent"]["accent"], pid))
        return theme_for(pid)

    @app.get("/api/settings")
    def get_settings():
        return {"default_quality": quality_default(),
                "music_format": db.get_setting("global", "music_format", "mp3"),
                "auto_delete_hours": scheduler.current_auto_delete_hours(cfg),
                "min_free_gb": cfg.min_free_gb, "shorts_prefetch": cfg.shorts_prefetch}

    @app.put("/api/settings")
    def put_settings(body: SettingsBody):
        if body.default_quality:
            db.set_setting("global", "default_quality", body.default_quality)
        if body.auto_delete_hours:
            db.set_setting("global", "auto_delete_hours", body.auto_delete_hours)
        if body.music_format:
            db.set_setting("global", "music_format", body.music_format)
        return get_settings()

    # ---- status ------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        db.q1("SELECT 1")
        return {"ok": True}

    @app.get("/api/status")
    def status(request: Request):
        client = request.client.host if request.client else ""
        via_ts = bool(request.headers.get("tailscale-user-login"))
        try:
            via_ts = via_ts or ipaddress.ip_address(client) in TAILSCALE_NET
        except ValueError:
            pass
        return {
            "ok": True, "version": __version__, "mock": cfg.mock, "uptime": time.time() - started,
            "via_tailscale": via_ts, "client": client,
            "disk": library.disk_status(cfg.library_dir, cfg.min_free_gb),
            "downloads": dm.counts(), "job": sched.running_job,
            "ffmpeg": {"source": ff_src, "found": bool(ffmpeg)}, "js_runtime": js[0],
            "python": platform.python_version(), "yt_dlp": _ytdlp_version(),
            "last_candidate_fetch": db.get_setting("global", "last_nightly", None),
        }

    # ---- static frontend ---------------------------------------------------------
    @app.get("/sw.js")
    def sw():
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _ytdlp_version() -> str | None:
    try:
        from yt_dlp.version import __version__ as v

        return v
    except Exception:
        return None


def ffmpeg_info(cfg: Config) -> dict:
    path, src = find_ffmpeg(cfg.ffmpeg_path)
    return {"path": path, "source": src, "version": tool_version(path)}


if __name__ == "__main__":  # pragma: no cover
    print("Run with: python -m homelib", file=sys.stderr)
