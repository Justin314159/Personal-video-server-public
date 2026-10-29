# Architecture

Home Video Library is one Python process (FastAPI under a single uvicorn
worker) that serves a JSON API, the static frontend, and runs a few background
threads. Everything is stored in one SQLite database (WAL mode) plus mp4 files
on disk.

```
 iPhone (Safari / Home Screen app)
        │  HTTPS over Tailscale ("tailscale serve" → 127.0.0.1:8787)
        ▼
 ┌──────────────────────── homelib (systemd, user "homelib", MemoryMax=2G) ─────┐
 │ FastAPI app (app.py)                                                         │
 │   /api/*  JSON routes          /api/stream/{id}  Range streaming from disk   │
 │   /       static frontend (HTML/CSS/ES modules, no build step)               │
 │                                                                              │
 │ DownloadManager (downloader.py)   1 foreground + 1 background thread        │
 │   └─ ytlib.YouTube → yt-dlp (library) → ffmpeg wrapper (nice 10, ionice idle)│
 │ Scheduler (scheduler.py)                                                     │
 │   ├─ cleaner every 10 min          (cleaner.py)                              │
 │   ├─ Shorts prefetch every 15 min  (keeps ≥10 unseen Shorts on disk)        │
 │   └─ nightly 03:30 candidate fetch (recommender.py)                          │
 │ SQLite WAL  /var/lib/homelib/homelib.db                                      │
 └──────────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 /var/lib/homelib/library/*.mp4   /var/lib/homelib/thumbs/*.jpg (320px)
 /var/lib/homelib/library/subs/*.vtt (cleaned captions)
```

## Modules (`backend/homelib/`)

| Module | Job |
|---|---|
| `config.py` | Loads `/etc/homelib/config.toml`; resolves the bind address and refuses `0.0.0.0` unless `allow_all_interfaces = true`. |
| `db.py` | Schema, one connection per thread, WAL, write lock, settings helpers. |
| `formats.py` | `FORMAT_BEST = "bv*+ba/b"`, `FORMAT_COMPAT = "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/b[ext=mp4]/b"`, merge to mp4 with `+faststart`, "needs VLC" codec check. |
| `ytlib.py` | yt-dlp wrapper: `ytsearch{n}:` flat search, URL lookup, download with progress hook (pause/cancel by raising from the hook), 320px thumbnails via ffmpeg. Dispatches to `mock.py` when `HOMELIB_MOCK=1`. |
| `mock.py` | Deterministic fake catalog (≈100 videos + Shorts), fake search, fake downloads that write a real tiny H.264 mp4, SVG thumbnails. |
| `downloader.py` | Download queue in the `downloads` table, worker threads, pause/resume/cancel, crash recovery, disk-space guard. |
| `library.py` | Profiles, videos, queues (shared + personal), watch state, likes, events, deletion. |
| `cleaner.py` | Auto-delete rules (pure `decide()` + `run()`). Keep flag and playlists block it; delete votes count as "watched". |
| `subtitles.py` | Parse SRT/WebVTT, collapse YouTube's rolling auto-captions, write clean WebVTT, pick caption tracks from a yt-dlp info dict. |
| `recommender.py` | Taste profile from events with time decay, scoring with reasons, candidate fetch, boost/mute/forget, export/reset, "Both of us" blend. |
| `shorts.py` | Shorts detection. |
| `formats.py` (music) | `ydl_audio_opts("mp3"/"m4a")`: bestaudio → extract audio, metadata, cover art. |
| `streaming.py` | Single-range HTTP Range responses streamed in 256 KB chunks. |
| `scheduler.py` | Background jobs. |
| `selfcheck.py` | Hardware/tool report (stdlib only so it runs before install). |
| `embeddings.py` | Optional sentence-transformers similarity, off by default. |

## Data model (SQLite)

* `profiles` – the two people (id 1 and 2), name, accent colour.
* `videos` – one row per YouTube id: metadata, keywords, file path, codec, `status`
  (`new|queued|downloading|ready|deleted|error`), `audience` (`all` or `p:<id>`:
  who must watch before auto-delete), `keep`.
* `queue_items` – `scope` `shared` (profile_id NULL) or `personal` (owner), `position`.
* `watch_state` – per profile + video: position, watched flag, rating (−1/0/1).
* `downloads` – each download attempt with progress, speed, ETA, phase, `kind`
  (`user`/`background`).
* `events` – everything the recommender learns from (finish, like, dislike,
  delete_unwatched, skip, search) plus `deleted` log rows. Users can view and
  delete them in "Your algorithm → History".
* `playlists` / `playlist_items` – named playlists, `scope` `shared` (both see
  them) or `personal` (owner only), ordered by `position`.
* `subtitles` – caption files per video: `lang`, `label`, `kind`
  (`manual`/`auto` from YouTube, `uploaded`), path to the cleaned `.vtt`.
* `delete_votes` – "delete after watching" taps (video, profile).
* `watch_state.speed` – remembered playback rate per video and profile.
* `reco_cache` – candidate pool from the nightly fetch, with cached score and reasons.
* `settings` – JSON values: `global` (default quality, auto-delete hours, last
  nightly run) and `profile:<id>` (algorithm settings, theme).

## Key flows

**Download.** `POST /api/downloads` → disk guard → upsert video, set audience,
add to queue → insert `downloads` row (`queued`) → worker claims it → yt-dlp
with the selected format → progress hook writes progress every 0.5 s → merge
via the low-priority ffmpeg wrapper → file `library/<id>.mp4` → status `ready`
→ thumbnail cached.

**Pause / cancel.** The API sets a flag; the next progress-hook call raises
`DownloadCancelled`. Pause keeps the `.part` file in `library/.partial`, and
resume re-queues it (`continuedl`). Cancel deletes partial files and removes the
video from queues.

**Playback.** `<video playsinline>` loads `/api/stream/<id>`. Safari probes with
`Range: bytes=0-1`, then requests ranges. The player posts progress every 5 s and on
pause/hide/end; ≥90 % or `ended` marks it watched (a `finish` event, and removal
from that person's personal queue).

**Captions & transcripts.** After a video downloads, `ytlib._fetch_tracks`
picks tracks from the info dict yt-dlp already returned (uploaded subtitles in
`subtitle_langs` first, else YouTube's automatic ones; `-orig` = the speech
recogniser's own text) and fetches the `.vtt` separately. A caption failure
never fails the download. `subtitles.normalize` collapses the "rolling" auto
format (each cue repeats the last line plus word timestamps) into plain cues,
which are stored as WebVTT in `library/subs/`. The player adds them as
`<track>` elements (CC button). The transcript view gets them as JSON from
`/api/subtitles/<id>/transcript` and searches client-side. Uploads (SRT/VTT)
arrive as JSON text (no multipart dependency). Older videos can fetch captions
later (`POST /api/videos/<id>/subtitles/fetch`).

**Delete after watching.** When a video ends, the player shows
`GET /api/videos/<id>/delete-status`: the relevant profiles (see auto-delete
below) and whether each has `watched`, `voted` or is `pending`. If the video is
only for you, "Delete now" removes it immediately. If it's shared, your tap is
recorded in `delete_votes` and the video goes as soon as nobody is pending
(everyone watched it or tapped delete). Only relevant profiles can vote. The
cleaner treats a vote like "watched", so "A voted, B finished" is removed after
the usual 10-minute grace.

**Playlists.** Shared or personal, ordered, any library video. A video in any
playlist is never auto-deleted (it's a deliberate collection). Deleting the
video removes it from playlists. Adding to a shared playlist widens the video's
audience to everyone. Playing a playlist (`#/player/<id>/list/<playlist>`)
loads each next video into the *same* `<video>`/`<audio>` element (no
navigation), so it can continue on a locked phone.

**Player.** Own controls over `<video playsinline>` (no native `controls`):
scrubber with pointer capture (drag shows a time bubble, release seeks),
double-tap zones (left/right 40 %), speed sheet (saved via
`PUT /api/watch/<id>/speed`), captions sheet, transcript panel, audio-only mode
and the screen-lock handoff to an `<audio>` element, PiP, Media Session
handlers, end card (delete section + "up next in 8 s" for playlists). Details
and iOS limits: [IOS_PLAYBACK.md](IOS_PLAYBACK.md).

**Auto-delete** (every 10 min). A ready video without Keep is deleted when every
*relevant* profile has watched it, or when it is older than N hours (12/24/48).
Relevant = everyone if it is in the shared queue or was downloaded with no queue;
otherwise only the people whose personal queue it was in. Nothing is deleted
within 10 minutes of someone touching it. Each deletion writes an `events` row
(`type='deleted'`, with the reason) and a log line.

**Recommender.** See the docstring in `recommender.py`; summary:

```
event weights: finish +1, like +1.5, dislike −2, delete-unwatched −1, skip(<2 s) −1, search +0.2
term weight   = Σ weight × 0.5^(age_days / half_life) / √(#keywords of that event)   (+ boost)
familiar      = (1 − loyalty) × Σ keyword weights/√n  +  loyalty × channel weight
score         = (1 − explore) × tanh(familiar / 3) + explore × novelty + explore × 0.3 × daily_jitter
```

The nightly job (03:30) builds search queries from each profile's top keywords
and channels, the long tail (exploration) and the user's starter topics, runs
them through yt-dlp flat search, and stores results in `reco_cache`. Ranking
itself is cheap and done live, so slider changes apply immediately.

**Music.** Search tab → Music. Songs come from a YouTube Music search
(`music.youtube.com/search?q=…#songs`, flat extraction; falls back to a normal
search). Downloading a song stores a separate `videos` row with `media='audio'`
and id `au_<youtube id>` (so the same YouTube id can also exist as a video),
using `bestaudio` + `FFmpegExtractAudio` to **mp3** (192 kbit/s, re-encoded at
nice 10) or **m4a** (AAC copied, no re-encode), plus embedded title/artist and
cover art. Files live in `library/music/`. Music is never auto-deleted, never
queued, and doesn't feed the recommender. In the app it plays through one shared
`<audio>` element (mini player above the tab bar, lock-screen controls via the
Media Session API); `GET /api/music/<id>/file` serves it as an attachment named
`Artist - Title.mp3` for "Save to iPhone".

**Shorts.** Detected by `/shorts/` URL, vertical aspect, or ≤180 s. The
prefetcher keeps ≥10 unseen Shorts downloaded (always in the compatible format so
muted autoplay works in Safari). They follow the same auto-delete rules. In the
feed, swiping away after <2 s is a skip (−1 and counts as seen).

## Frontend

`backend/homelib/static/`: `index.html`, `css/app.css`, `js/*.js` ES modules,
one per screen in `js/screens/`. A hash router (`#/search`, `#/library/<segment>`, `#/playlist/<id>`,
`#/player/<id>[/list/<playlist>]`…) with a skeleton placeholder while a screen
loads and a short fade-in. CSS variables for theming: each profile has a dark
and a light palette plus a mode (`dark`, `light`, `auto` = follow
`prefers-color-scheme` live), applied at runtime by `theme.js`. Spacing, radius
and motion tokens live at the top of `app.css`. Font sizes are `rem` on top of
`-apple-system-body`, so Safari's Dynamic Type setting scales the whole UI.
Pull-to-refresh (`ui.pullToRefresh`) on For you, Library, playlists and
Downloads. a service
worker that caches only the app shell. iOS specifics: `viewport-fit=cover` +
`env(safe-area-inset-*)`, 44 px minimum tap targets, `playsinline`, muted
autoplay + tap-to-unmute in Shorts, only the current and adjacent Shorts hold a
`src` (iOS limits simultaneous video decoders), apple-touch-icon, manifest.

The active profile is kept in `localStorage` and sent as `X-Profile`; there is
no login (see DECISIONS.md).

## Preview build

`scripts/build_preview.py` copies the real CSS/fonts/icons, concatenates the ES
modules into classic scripts (browsers block module imports from `file://`),
adds `preview_src/mockapi.js` (an in-browser fake of the whole API, with a JS
port of the recommender) and data generated from `mock.py`, plus small sample
mp4s. The result in `preview/` opens by double-click.

## Memory budget (4 GB box)

uvicorn + FastAPI + yt-dlp loaded: ~120–180 MB RSS. One yt-dlp download: +50–100 MB.
ffmpeg merge (stream copy, no transcoding): ~50–150 MB. deno (YouTube JS
challenge solver, spawned per extraction): ~100–200 MB briefly. Worst case is
well under the `MemoryMax=2G` cap, with a 2 GB swap file as a cushion.
