# Decisions

Choices made where the brief was ambiguous or left room. ★ marks the ones
most worth a second look.

## Product behaviour

1. ★ **Auto-delete reads literally: watched-by-all OR older than N hours.** A video
   you haven't watched is still deleted after 24 h (default) unless you tap Keep.
   12/24/48 are the only options, as specified. If that's too aggressive, the
   simplest change is to measure age from "first person watched" instead; it's one
   line in `cleaner.decide()`.
2. **10-minute grace.** Nothing is auto-deleted while someone touched it in the last
   10 minutes, so a video can't vanish mid-watch when it crosses the age limit.
3. ★ **"Relevant profiles".** Shared queue, or downloaded with "Just download" →
   both people must watch. "My queue" → only that person. If the other person later
   adds it to their own queue, or anyone puts it in Shared, it widens to both (never
   narrows).
4. **Watched = 90 % or the end.** Finishing removes it from your personal queue; it
   stays in Shared (with watched-by avatars) until deleted.
5. **Only manual deletes count as "delete-unwatched −1".** Auto-deletes by age are
   not held against you (you might just have been busy).
6. **Skip (<2 s, −1)** applies to Shorts swiped away within 2 s and to the
   player closed within 2 s of starting playback. A skipped Short counts as seen.
7. **Likes/dislikes replace each other.** Re-rating removes the previous like/dislike
   event rather than stacking them.
8. **"Not interested" in For you** stores the video as a dislike (−2), which also
   teaches the algorithm about its keywords and channel.
9. **Shorts are always downloaded in the Compatible format** so muted autoplay works
   in Safari, whatever the default quality is.
10. **Shorts detection includes "≤180 s"** as specified, so short normal videos (e.g.
    a 2-minute clip) count as Shorts and appear in the Shorts feed.
11. **Shorts prefetch target is shared** (≥10 Shorts not yet seen by *both*), ranked with
    the blended profile; each person's feed then hides what they've seen.
12. **Search "+0.2"** only adds keywords (no channel), since a search has no channel.

## Recommender

13. ★ **Third slider = "Channel loyalty"** (topics ↔ channels). Chosen because it's
    easy to understand and changes results visibly. Also added: an "Include Shorts in
    For you" toggle and editable "Starter topics" (cold start + exploration seeds).
14. **Keyword share = weight / √(#keywords)** so a long title doesn't count more than a
    short one. Keywords are title words minus stopwords (plus yt-dlp tags when a video
    is downloaded); no stemming.
15. **Boost = +2 added to the term's weight** (term shows "Boost +2"); **Mute = hard
    filter** (never shown); **Forget = term ignored** in the profile. All are
    reversible in "Your algorithm".
16. **"Both of us" = equal average** of both profiles' term weights and sliders; mutes
    from either person apply.
17. **Ranking is live, fetching is nightly.** The 03:30 job only refreshes the
    candidate pool; scores are recomputed on each request (cheap at a few hundred
    candidates) so slider changes apply instantly. The pool is also filled on first
    start and by "Fetch suggestions now".
18. **Embeddings**: `embeddings.py` + `[recommender] embeddings = true` adds a
    "similar titles" term if `sentence-transformers` is installed. Not vendored,
    off by default: PyTorch + model would take 1–2 GB of the 4 GB RAM.

## Server and platform

19. **Seed code was not present.** The repo had only a README, so the CLI prototype
    (`ytlib.py`, `requirements.txt`) was re-implemented from the brief; the two format
    strings are used verbatim.
20. **Concurrency = 1 foreground + 1 background worker** (`max_concurrent = 2`). The
    background slot downloads Shorts. The nightly *candidate fetch* is metadata-only
    searches run by the scheduler thread, not a download slot.
21. **ffmpeg priority via wrapper scripts.** yt-dlp's `ffmpeg_location` points at small
    generated shell wrappers that `exec nice -n 10 ionice -c3 ffmpeg …`. The background
    download thread is also reniced (Linux per-thread nice).
22. **ffmpeg on an offline box**: Ubuntu Server doesn't ship ffmpeg and apt needs
    internet, so `imageio-ffmpeg`'s static binary is vendored as the fallback. It has
    no `ffprobe`; merging works without it. `sudo apt install ffmpeg` later is optional.
23. **JS runtime: Deno, vendored as the `deno` PyPI wheel** (`yt-dlp[deno]`), found next to
    the venv's python and passed to yt-dlp via `js_runtimes`. Node is used if present
    and no deno is found.
24. ★ **Ubuntu 22.04 / Python 3.10 gets yt-dlp 2026.10.21**, the last release that
    supports 3.10 (newer ones need ≥3.11). It works today but will fall behind YouTube
    changes. 24.04 is strongly recommended (see OPERATIONS.md for the 22.04 workaround).
25. **No python3-venv dependency.** Fresh Ubuntu lacks `ensurepip`, so the installer
    creates the venv with `--without-pip` and bootstraps pip from the vendored wheel.
26. **Wheels are one shared folder** (pure wheels once, binary wheels per cp310/311/312,
    manylinux x86_64). pip picks the right ones offline.
27. **systemd hardening is moderate** (`ProtectSystem=full`, not `strict`) so a library
    directory on another mount just works without editing the unit. `MemoryMax=2G`
    plus `MemoryHigh=1700M`.
28. **Swap: `/swapfile`, 2 GB, only if no swap exists at all.**
29. **Default bind 127.0.0.1 + `tailscale serve`** for HTTPS (needed for the service
    worker and the best iOS PWA behaviour). `bind = "tailscale"` (direct Tailscale IP,
    plain HTTP) is supported. 0.0.0.0 refused unless `allow_all_interfaces = true`.
30. **The service never installs or configures Tailscale**; it only detects it (status
    page shows "via Tailscale" when requests carry `Tailscale-User-Login` from
    `tailscale serve` or come from a 100.64.0.0/10 address).
31. **Daily database backup cron (04:15)** added by `install.sh`; videos are not backed up.
32. **No authentication** besides the profile picker, as specified. The active profile is
    an `X-Profile` header from `localStorage` (plus a cookie); there's nothing to protect
    between two people in one household.
33. **Thumbnails**: YouTube's `mqdefault.jpg` (already 320×180), normalized through ffmpeg
    to a 320px-wide JPEG and cached in `/var/lib/homelib/thumbs`; served through the app
    so the phone never talks to YouTube directly. Mock mode uses SVGs.
34. **Range handling**: single ranges only (multi-range requests get the first range),
    `If-Range` honoured, 416 for unsatisfiable. Safari only ever sends single ranges.
35. **Downloads are `<id>.mp4`** in one flat folder; partial files in `library/.partial`.
36. **Paste-a-link** in Search resolves YouTube URLs directly (handy from the iOS share
    sheet: copy link → paste).

## Frontend and preview

37. ★ **Preview bundling.** Browsers block ES module imports from `file://`, so
    `preview/` contains the same modules concatenated into classic scripts by
    `scripts/build_preview.py` (a ~40-line regex shim, not a bundler dependency). The
    served app uses the native ES modules with no build step. Fonts are inlined as data
    URIs in the preview CSS for the same `file://` reason.
38. **The preview's fake API is JavaScript** (`preview_src/mockapi.js`), including a port
    of the recommender's scoring so "Why am I seeing this?" is meaningful. The catalog
    and thumbnails are generated from the Python mock, so both stay in sync.
39. **Figtree is vendored** (OFL, latin 400–700 woff2, ~60 KB) so the app doesn't depend
    on Google Fonts; system font fallback.
40. **Shorts entry point**: a Shorts strip at the top of "For you" (the 5 tabs were fixed),
    opening a full-screen feed.
41. **Library view lives in Downloads** ("On the server"), with time-to-delete, Keep and
    Delete per video; Queue holds the My/Shared lists.
42. **Appearance editable colours**: background, cards, borders, text, secondary text,
    accent, progress bar. Picking a preset resets custom colours; changing the accent
    also changes the profile's avatar colour. Contrast warnings follow WCAG ratios
    (4.5:1 text, 3:1 accent/secondary).
43. **Profile names default to "Profile 1" and "Profile 2"**: change `[profiles] names` before
    first start, or rename in Settings any time.

## Music (v1.1)

44. ★ **Music lives under the Search tab** (a Videos | Music switch) instead of a
    sixth tab, to keep the five-tab bar from the brief. Easy to promote to its own tab.
45. **Music is a keep-forever collection**: never auto-deleted (only the Delete button),
    not part of queues, Shorts or the "For you" algorithm, and music searches aren't
    logged as recommender signals.
46. **MP3 by default** (192 kbit/s, the one place the server re-encodes; a 4-minute song
    takes seconds at nice 10). **M4A** keeps YouTube's AAC audio without re-encoding.
    The default is a setting; each download can choose.
47. **"Save to iPhone" is a plain file download** (`Content-Disposition: attachment`,
    named "Artist - Title.mp3"). On iOS it lands in Files → Downloads. Apple's Music app
    can't import files on the iPhone itself (that needs a computer with Finder/iTunes),
    so saved songs play in Files, VLC or similar. In-app playback streams from the server.
48. **Separate ids for songs** (`au_<id>`), so a YouTube id can be both a downloaded
    video and a saved song without clashing.

## Live YouTube testing (v1.1.1, from a cloud test environment)

49. **What was verified live:** video search, Shorts search, YouTube Music search,
    pasted-link lookup, and Deno solving YouTube's JavaScript challenge. Downloads
    were *not* verified live: YouTube served its "confirm you're not a bot" check to
    the cloud network (expected for datacenter addresses). They are covered by the
    real-yt-dlp pipeline tests against a local server, and the first real test is on
    the home box.
50. ★ **Shorts and music search call YouTube's search API directly** (`innertube.py`),
    because yt-dlp's `ytsearch` ignores "#shorts" (1 Short in 8 results live) and its
    YouTube Music search returned empty song sections. Both fall back to yt-dlp search
    if YouTube changes that JSON. Shorts from this search have no channel name until
    downloaded.
51. **YouTube Music hid all "Song" results from the cloud network**, returning only
    videos/artists. Music search therefore uses songs when present, else YouTube Music's
    video results, topped up from a normal search.
52. **Bug fixed:** a pasted link stored YouTube's temporary stream address as the video
    URL, so the later download failed immediately. Only page URLs are stored now.
53. **No bot-check workarounds.** When YouTube asks for verification, the app reports
    the error; the supported fix is `cookies_file` with your own YouTube login.

## iOS share shortcut (v1.2)

54. **Shortcut built by hand, not shipped as a file.** iOS only imports signed shortcuts
    (signed on a Mac or via an iCloud link), which can't be produced here. The app
    shows the six steps with the server address and profile filled in; one built
    shortcut can then be shared to the other phone from the Shortcuts app.
55. **`POST /api/share`** does lookup + download in one call and always answers with a
    human-readable `message` (errors too), so the shortcut is just "POST, then show
    `message`". Profiles are matched by name, case-insensitively, or by id.


## Player, playlists, captions, dark mode (v1.3)

56. **Own player controls instead of Safari's.** Double-tap skip, the scrub time
    bubble, speed and caption menus need a custom overlay. Native `controls` would
    swallow the double-tap (zoom/fill). Apple's full-screen player is still one tap
    away (and is what iPhone full screen always is).
57. **Screen-lock handoff to an `<audio>` element.** iOS pauses `<video>` in the
    background but lets `<audio>` play. The player continues the same stream in an
    audio element (primed by your first tap), then switches back on unlock. If an iOS
    version blocks starting it, the lock-screen play button resumes it. Audio-only
    mode uses the same element from the start. See IOS_PLAYBACK.md.
58. **Lock screen shows ±10 s, not previous/next.** iOS shows only one pair; skipping
    was asked for. Playlists advance on their own, also while locked.
59. **Captions fetched separately after the download.** Letting yt-dlp write subtitles
    makes a subtitle error (YouTube rate-limits them) fail the whole video. The track
    URLs are in the info dict anyway, so they're fetched afterwards and failures only
    log.
60. **Auto-captions are cleaned, not stored raw.** YouTube's rolling format repeats every
    line. Cleaned cues give normal captions and a transcript without duplicates.
61. **Subtitle uploads as JSON text.** Multipart forms need `python-multipart`, a new
    dependency for the offline USB package. Subtitle files are small text.
62. **Delete after watching: personal = instant, shared = everyone done.** "Done" means
    finished *or* tapped delete. Only the people a video is for can vote. The
    old "Delete for both now" stays behind a confirmation in the Delete sheet.
63. **Playlists protect from auto-delete.** Putting a video in a playlist is a deliberate
    "keep this". Without that, the 24 h rule would empty playlists overnight. Explicit
    deletes still work and remove the video from its playlists.
64. **Dark mode lives inside the theme.** Each profile picks a dark preset, a light
    preset and a mode (Dark / Light / Match iPhone). Custom background/text colours
    are per palette; accent and progress colours are the profile's own and shared by
    both. Old single-preset settings are upgraded on first read.
65. **Library tab replaces the Queue tab.** Videos / My queue / Shared / Playlists in
    one place. `#/queue` still works and opens My queue.
66. **Text follows Dynamic Type.** `html { font: -apple-system-body }` and `rem` sizes,
    so the iPhone's Text Size setting scales the app.
