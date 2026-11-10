"""yt-dlp format strings and download option building.

Every video download is H.264 (avc1) video + AAC (m4a) audio merged into an
mp4, which every iPhone plays natively (video and audio-only mode). There is
deliberately no catch-all "/b" at the end: before 1.6.1 that fallback, and the
old "best" string ("bv*+ba/b"), could store VP9/AV1 video or Opus audio, which
iOS Safari won't play. If YouTube offers no H.264 + AAC version, the download
fails with a clear message instead of storing a file that won't play.
"""
from __future__ import annotations

# H.264 video + AAC audio, merged into mp4. `[x^=?y]` (with the ?) also accepts
# a format whose codec isn't reported; YouTube always reports it, so that only
# matters for the plain-mp4 last resort (and for direct-file URLs in tests).
_IOS_TAIL = "b[vcodec^=avc1][acodec^=mp4a]{cap}/b[ext=mp4][vcodec^=?avc1][acodec^=?mp4a]{cap}"
# Highest H.264 YouTube has (in practice 1080p; it doesn't offer H.264 above that).
FORMAT_BEST = "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/" + _IOS_TAIL.format(cap="")
# H.264 video <=1080p + AAC audio.
FORMAT_COMPAT = "bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]/" + _IOS_TAIL.format(cap="[height<=?1080]")

QUALITIES = {"best": FORMAT_BEST, "compat": FORMAT_COMPAT}

# Codecs iOS Safari plays natively in an mp4 container (prefixes as yt-dlp reports them).
SAFARI_VCODECS = ("avc1", "h264", "hvc1", "hev1", "hevc")
SAFARI_ACODECS = ("mp4a", "aac")
NO_COMPAT_FORMAT = "YouTube offers no iPhone-compatible (H.264 + AAC) version of this video"


# Music downloads: best audio stream, converted to mp3 (plays everywhere) or
# kept as m4a/AAC (no re-encode when YouTube already has AAC).
AUDIO_FORMATS = {"mp3", "m4a"}
FORMAT_AUDIO = "bestaudio/best"


def is_audio(quality: str) -> bool:
    return quality in AUDIO_FORMATS


def ydl_audio_opts(fmt: str) -> dict:
    """yt-dlp options for a music download (fmt: 'mp3' or 'm4a')."""
    if fmt not in AUDIO_FORMATS:
        raise ValueError(f"unknown audio format {fmt!r} (expected 'mp3' or 'm4a')")
    return {
        "format": "bestaudio[ext=m4a]/bestaudio/best" if fmt == "m4a" else FORMAT_AUDIO,
        "writethumbnail": True,
        "postprocessors": [
            # mp3 at 192 kbit/s is plenty for phone listening; m4a is copied when possible.
            {"key": "FFmpegExtractAudio", "preferredcodec": fmt, "preferredquality": "192"},
            {"key": "FFmpegMetadata", "add_metadata": True},
            # Cover art inside the file (shows in Files/VLC); converted to jpg first.
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            {"key": "EmbedThumbnail", "already_have_thumbnail": False},
        ],
    }


def select_format(quality: str, is_short: bool = False) -> str:
    """Return the yt-dlp format string for a quality choice.

    Shorts always use the compatible format: the feed relies on muted autoplay
    in Safari, which needs a codec Safari can decode.
    """
    if is_short:
        return FORMAT_COMPAT
    if quality not in QUALITIES:
        raise ValueError(f"unknown quality {quality!r} (expected 'best' or 'compat')")
    return QUALITIES[quality]


def ydl_format_opts(quality: str, is_short: bool = False) -> dict:
    """yt-dlp options related to format selection and container."""
    return {
        "format": select_format(quality, is_short),
        "merge_output_format": "mp4",
        # Put the moov atom at the start so Safari can start playback quickly.
        "postprocessor_args": {"merger": ["-movflags", "+faststart"]},
    }


def needs_vlc(vcodec: str | None, acodec: str | None = None) -> bool:
    """True when the file probably won't play in iOS Safari (VP9/AV1 video, or
    Opus sound in a video downloaded before 1.6.1)."""
    return not ios_playable(vcodec, acodec)


def ios_playable(vcodec: str | None, acodec: str | None) -> bool:
    """False only when a codec is known and isn't one iOS Safari plays.
    (Unknown codecs pass: yt-dlp can't always tell for direct-file links.)"""
    v = (vcodec or "").lower()
    a = (acodec or "").lower()
    if v and v != "none" and not v.startswith(SAFARI_VCODECS):
        return False
    return not (a and a != "none" and not a.startswith(SAFARI_ACODECS))
