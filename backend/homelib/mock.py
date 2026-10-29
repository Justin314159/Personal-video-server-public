"""Mock YouTube (HOMELIB_MOCK=1): a deterministic fake catalog, fake search and
fake downloads that produce real (tiny) mp4 files. Used by the tests, by the
preview/ build, and for running the whole app with no internet.
"""
from __future__ import annotations

import hashlib
import html
import os
import random
import shutil
import subprocess
import time
from pathlib import Path

from . import text

# (channel, palette, [title templates]) – titles are plausible but invented.
CHANNELS = [
    ("Crumb & Crust", ("#8a5a2b", "#e9c46a"), [
        "Sourdough starter from scratch: day by day", "Open crumb sourdough without a mixer",
        "The easiest focaccia you will ever bake", "Why your sourdough is dense (and how to fix it)",
        "Bagels at home: boiling, baking, the works", "Whole wheat sourdough that actually rises",
        "Cinnamon rolls the night before", "Pizza dough: 24h vs 72h cold ferment"]),
    ("Knife & Fire", ("#7b2d26", "#f4a261"), [
        "Knife skills every home cook needs", "Smash burgers on a cast iron pan",
        "Weeknight ramen in 30 minutes", "Cast iron care myths, tested",
        "Perfect fried rice: the restaurant trick", "Five pasta sauces from one pantry",
        "Dumplings from scratch with the family", "Crispy roast potatoes, three ways"]),
    ("Sawdust Sunday", ("#5c4033", "#d4a373"), [
        "Building a walnut coffee table", "Hand tool woodworking for beginners",
        "Dovetails by hand: slow and steady", "Workbench build on a budget",
        "Sharpening chisels to a mirror finish", "Live edge shelf with hidden brackets",
        "Japanese joinery without nails", "Restoring a rusty hand plane"]),
    ("Slow Rail Japan", ("#264653", "#e76f51"), [
        "Riding the slowest train in Japan", "48 hours in Kyoto on a budget",
        "Hokkaido in winter: snow, ramen, onsen", "Tokyo capsule hotel overnight",
        "Hidden temples of Nara", "Japan rail pass: worth it?",
        "Island hopping in the Seto Inland Sea", "Osaka street food tour"]),
    ("Orbit Notes", ("#1d3557", "#a8dadc"), [
        "How the James Webb telescope sees the past", "What is actually inside a black hole",
        "Why Mars lost its atmosphere", "Building a backyard telescope mount",
        "The strange orbit of Pluto explained", "Rocket engines: a visual guide",
        "Could we live on Europa?", "Astrophotography with a phone"]),
    ("Six String Lab", ("#3d405b", "#f2cc8f"), [
        "Fingerstyle guitar for absolute beginners", "Ten chords, a hundred songs",
        "Travis picking in 15 minutes", "Blues guitar improvisation basics",
        "Restringing and setting up an acoustic", "Learn the CAGED system the easy way",
        "Bossa nova rhythm guitar", "Slide guitar in open G"]),
    ("Morning Mat", ("#2a9d8f", "#e9f5db"), [
        "20 minute morning yoga flow", "Yoga for back pain relief",
        "Mobility routine for desk workers", "Beginner pilates core workout",
        "Bedtime stretch to wind down", "Hip opening yoga sequence",
        "Breathing techniques for stress", "Full body stretch in 10 minutes"]),
    ("Homelab Hour", ("#22223b", "#9a8c98"), [
        "Turning an old mini PC into a home server", "Tailscale explained in plain English",
        "Ubuntu Server install walkthrough", "Self-hosting a media library",
        "Linux command line for beginners", "Backups that actually work",
        "Quiet homelab on a budget", "Docker vs bare metal for a homelab"]),
    ("Green Thumb Plot", ("#386641", "#a7c957"), [
        "Growing tomatoes in containers", "Raised bed garden build",
        "Composting for small spaces", "Pruning fruit trees in winter",
        "Seed starting under lights", "Herb garden on a balcony",
        "No-dig gardening one year later", "Saving seeds from your garden"]),
    ("Whisker Weekly", ("#6d597a", "#ffb4a2"), [
        "Cat tries every toy at the pet store", "Training a cat to high five",
        "Kitten's first week home", "Cat agility course at home",
        "Why cats knock things off tables", "Building a cat tree from scratch",
        "Senior cat care tips", "Cat vs automatic laser"]),
]

SHORT_TITLES = [
    "This sourdough trick changed everything", "One knife cut you need", "60 second focaccia",
    "Dovetail in 30 seconds", "Planer shavings so satisfying", "Tokyo at 5am",
    "Shinkansen pass in slow motion", "Saturn through a backyard scope", "Rocket launch from my roof",
    "Guitar riff you can learn today", "Hardest chord in 15s", "Two minute neck stretch",
    "Desk posture fix", "Mini PC fanless setup", "Tailscale in 45 seconds",
    "Tomato pruning hack", "Seedlings day 1 to 30", "Cat discovers the box",
    "Cat high-five success", "Kitten vs cucumber? No, vs toy mouse", "Crispy potatoes ASMR",
    "Ramen egg perfection", "Japanese saw tips", "Moon timelapse",
]


# (artist, song title) – invented, for the Music page.
SONGS = [
    ("The Night Ferries", "Harbour Lights"), ("The Night Ferries", "Lantern Season"),
    ("Mara Okafor", "Paper Moons"), ("Mara Okafor", "Slow Bloom"), ("Mara Okafor", "Kitchen Radio"),
    ("Lowtide Choir", "Saltwater Hymn"), ("Lowtide Choir", "Weather Vane"),
    ("Juno & the Pines", "Cabin Fever"), ("Juno & the Pines", "Firewood"), ("Juno & the Pines", "North Road"),
    ("DJ Kettle", "Late Train (Lo-fi Mix)"), ("DJ Kettle", "Rainy Window Beats"), ("DJ Kettle", "Study Hall"),
    ("Ines Varga", "Copper Sky"), ("Ines Varga", "Electric Garden"),
    ("Brass Weather", "Sunday Parade"), ("Brass Weather", "Second Line Rain"),
    ("Theo Lark", "Acoustic Mornings"), ("Theo Lark", "Fingerpicked Lullaby"), ("Theo Lark", "Open G Waltz"),
]


def _vid(seed: str) -> str:
    """11-character YouTube-looking id derived from a seed string."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    h = hashlib.sha256(seed.encode()).digest()
    return "".join(alphabet[b % 64] for b in h[:11])


def _build_catalog() -> list[dict]:
    items = []
    for ci, (channel, palette, titles) in enumerate(CHANNELS):
        cid = "UC" + _vid("ch" + channel) + _vid("x" + channel)[:11]
        for ti, title in enumerate(titles):
            vid = _vid(f"{channel}/{title}")
            rnd = random.Random(vid)
            items.append({
                "id": vid, "title": title, "channel": channel, "channel_id": cid,
                "duration": rnd.randint(4 * 60, 32 * 60),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "thumb_url": f"mock://{vid}", "is_short": False,
                "view_count": rnd.randint(2_000, 3_000_000),
                "palette": palette,
            })
    for si, title in enumerate(SHORT_TITLES):
        channel, palette, _ = CHANNELS[si % len(CHANNELS)]
        vid = _vid(f"short/{title}")
        rnd = random.Random(vid)
        items.append({
            "id": vid, "title": title, "channel": channel,
            "channel_id": "UC" + _vid("ch" + channel) + _vid("x" + channel)[:11],
            "duration": rnd.randint(12, 59), "url": f"https://www.youtube.com/shorts/{vid}",
            "thumb_url": f"mock://{vid}", "is_short": True,
            "view_count": rnd.randint(10_000, 9_000_000), "palette": palette,
        })
    return items


def _build_songs() -> list[dict]:
    out = []
    for i, (artist, title) in enumerate(SONGS):
        vid = _vid(f"song/{artist}/{title}")
        rnd = random.Random(vid)
        out.append({
            "id": vid, "title": title, "channel": artist, "channel_id": "UC" + _vid("artist" + artist)[:22],
            "duration": rnd.randint(150, 320), "url": f"https://music.youtube.com/watch?v={vid}",
            "thumb_url": f"mock://{vid}", "is_short": False, "view_count": rnd.randint(5_000, 4_000_000),
            "palette": CHANNELS[i % len(CHANNELS)][1], "song": True,
        })
    return out


CATALOG = _build_catalog()
SONG_CATALOG = _build_songs()
BY_ID = {v["id"]: v for v in CATALOG + SONG_CATALOG}


def public(v: dict) -> dict:
    return {k: v[k] for k in ("id", "title", "channel", "channel_id", "duration", "url",
                              "thumb_url", "is_short", "view_count")}


def search(query: str, n: int = 12, shorts_only: bool = False) -> list[dict]:
    """Rank catalog items by keyword overlap with the query; pad deterministically."""
    qk = set(text.keywords(query, limit=20)) | {w for w in query.lower().split() if len(w) >= 3}
    pool = [v for v in CATALOG if v["is_short"] == shorts_only] if shorts_only else CATALOG
    scored = []
    for v in pool:
        vk = set(text.keywords(v["title"], v["channel"], limit=30))
        s = len(qk & vk) * 2 + sum(1 for w in qk if w in v["title"].lower() or w in v["channel"].lower())
        scored.append((s, v["id"], v))
    scored.sort(key=lambda t: (-t[0], t[1]))
    hits = [v for s, _, v in scored if s > 0]
    if len(hits) < n:
        rest = [v for s, _, v in scored if s == 0]
        random.Random(query).shuffle(rest)
        hits += rest
    return [public(v) for v in hits[:n]]


def search_music(query: str, n: int = 15) -> list[dict]:
    """Fake YouTube Music song search over SONG_CATALOG."""
    words = {w for w in query.lower().split() if len(w) >= 2}
    scored = []
    for v in SONG_CATALOG:
        hay = (v["title"] + " " + v["channel"]).lower()
        scored.append((sum(1 for w in words if w in hay), v["id"], v))
    scored.sort(key=lambda t: (-t[0], t[1]))
    hits = [v for s, _, v in scored if s > 0] or [v for _, _, v in scored]
    return [public(v) for v in hits[:n]]


def thumbnail_svg(video_id: str) -> str:
    v = BY_ID.get(video_id) or {"title": video_id, "channel": "", "palette": ("#333", "#888"), "is_short": False}
    a, b = v["palette"]
    w, h = (180, 320) if v["is_short"] else (240, 240) if v.get("song") else (320, 180)
    title = html.escape(v["title"])
    words, lines, cur = title.split(), [], ""
    maxc = 16 if v["is_short"] else 18 if v.get("song") else 26
    for word in words:
        if len(cur) + len(word) + 1 > maxc:
            lines.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    lines.append(cur)
    tspans = "".join(
        f'<tspan x="16" dy="{0 if i == 0 else 22}">{ln}</tspan>' for i, ln in enumerate(lines[:4]))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient></defs>'
        f'<rect width="{w}" height="{h}" fill="url(#g)"/>'
        f'<circle cx="{w - 40}" cy="40" r="60" fill="#fff" opacity=".10"/>'
        f'<text x="16" y="{h - 16 - 22 * (min(len(lines), 4) - 1) - 22}" font-family="Figtree,Helvetica,Arial,sans-serif" '
        f'font-size="18" font-weight="700" fill="#fff">{tspans}</text>'
        f'<text x="16" y="{h - 14}" font-family="Figtree,Helvetica,Arial,sans-serif" font-size="12" '
        f'fill="#fff" opacity=".85">{html.escape(v["channel"])}</text></svg>'
    )


def subtitles(video_id: str) -> list[dict]:
    """Fake YouTube auto-captions in the "rolling" style the real ones use."""
    v = BY_ID.get(video_id)
    if not v or v["is_short"] or v.get("song"):
        return []
    words = (f"welcome back to {v['channel']} today we are looking at {v['title'].lower()} "
             "let's get started first a quick overview of what you will need then we go step by step "
             "if you enjoyed this one there is more on the channel thanks for watching").split()
    cues, t, prev, line = ["WEBVTT", "Kind: captions", "Language: en", ""], 0.0, "", []

    def ts(x: float) -> str:
        return f"00:{int(x // 60):02d}:{x % 60:06.3f}"

    for i, w in enumerate(words):
        line.append(w)
        if len(line) == 6 or i == len(words) - 1:
            cur = " ".join(line)
            tagged = line[0] + "".join(f"<{ts(t + 0.3 * (k + 1))}><c> {x}</c>" for k, x in enumerate(line[1:]))
            cues += [f"{ts(t)} --> {ts(t + 2.4)} align:start position:0%", prev or " ", tagged, "",
                     f"{ts(t + 2.4)} --> {ts(t + 2.41)} align:start position:0%", cur, " ", ""]
            prev, line, t = cur, [], t + 2.41
    return [{"lang": "en", "kind": "auto", "text": "\n".join(cues)}]


# ---- fake downloads -------------------------------------------------------

def fixture_mp4(cache_dir: Path, vertical: bool, ffmpeg: str | None) -> Path | None:
    """A tiny real H.264/AAC mp4 made with ffmpeg once, then reused."""
    name = "vertical.mp4" if vertical else "landscape.mp4"
    out = cache_dir / name
    if out.is_file():
        return out
    if not ffmpeg:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    size = "360x640" if vertical else "640x360"
    tmp = out.with_suffix(".tmp.mp4")
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24:duration=6",
           "-f", "lavfi", "-i", "sine=frequency=330:duration=6",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-b:v", "300k",
           "-c:a", "aac", "-b:a", "64k", "-shortest", "-movflags", "+faststart", str(tmp)]
    try:
        subprocess.run(cmd, check=True, timeout=120)
        tmp.replace(out)
        return out
    except Exception:
        return None


def fixture_audio(cache_dir: Path, fmt: str, ffmpeg: str | None) -> Path | None:
    """A short real mp3 or m4a, made once with ffmpeg."""
    out = cache_dir / f"song.{fmt}"
    if out.is_file():
        return out
    if not ffmpeg:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    codec = ["-c:a", "libmp3lame", "-b:a", "128k"] if fmt == "mp3" else ["-c:a", "aac", "-b:a", "96k"]
    tmp = out.with_name(f"song.tmp.{fmt}")
    try:
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=8", *codec, str(tmp)], check=True, timeout=60)
        tmp.replace(out)
        return out
    except Exception:
        return None


class MockStop(Exception):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


def download(video_id: str, out_dir: Path, fixture_dir: Path, ffmpeg: str | None,
             progress, should_stop, audio_fmt: str | None = None) -> dict:
    """Simulate a download with progress, then copy a fixture mp4 into place."""
    v = BY_ID.get(video_id)
    if v is None:
        raise RuntimeError(f"mock: unknown video {video_id}")
    seconds = float(os.environ.get("HOMELIB_MOCK_DL_SECONDS", "6"))
    total = 6_000_000 if (v["is_short"] or audio_fmt) else 40_000_000
    steps = max(1, int(seconds / 0.25))
    for i in range(1, steps + 1):
        stop = should_stop()
        if stop:
            raise MockStop(stop)
        done = int(total * i / steps)
        progress({"downloaded_bytes": done, "total_bytes": total,
                  "speed": total / max(seconds, 0.01), "eta": seconds * (steps - i) / steps,
                  "phase": "video" if i < steps else "merging"})
        if seconds > 0:
            time.sleep(seconds / steps)
    out_dir.mkdir(parents=True, exist_ok=True)
    if audio_fmt:
        dest = out_dir / f"{video_id}.{audio_fmt}"
        fx = fixture_audio(fixture_dir, audio_fmt, ffmpeg)
        if fx:
            shutil.copyfile(fx, dest)
        else:
            dest.write_bytes(b"ID3" + bytes(range(256)) * 100)
        return {"file_path": str(dest), "width": None, "height": None, "vcodec": "none",
                "acodec": "mp3" if audio_fmt == "mp3" else "mp4a.40.2", "title": v["title"],
                "channel": v["channel"], "channel_id": v["channel_id"], "duration": v["duration"],
                "tags": [], "file_size": dest.stat().st_size}
    dest = out_dir / f"{video_id}.mp4"
    fx = fixture_mp4(fixture_dir, v["is_short"], ffmpeg)
    if fx:
        shutil.copyfile(fx, dest)
    else:  # no ffmpeg: write a recognisable placeholder so streaming tests still work
        dest.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 400)
    w, h = (360, 640) if v["is_short"] else (640, 360)
    return {"file_path": str(dest), "width": w, "height": h, "vcodec": "avc1.64001e", "subtitles": subtitles(video_id),
            "acodec": "mp4a.40.2", "title": v["title"], "channel": v["channel"],
            "channel_id": v["channel_id"], "duration": v["duration"], "tags": [],
            "file_size": dest.stat().st_size}
