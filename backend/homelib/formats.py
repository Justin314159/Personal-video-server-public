"""yt-dlp format strings and download option building.

The two format strings are carried over from the original CLI prototype.
"""
from __future__ import annotations

# Best available streams (up to 4K, VP9/AV1 common), merged into an mp4.
FORMAT_BEST = "bv*+ba/b"
# H.264 video <=1080p + AAC audio, which iOS Safari plays natively.
FORMAT_COMPAT = "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/b[ext=mp4]/b"

QUALITIES = {"best": FORMAT_BEST, "compat": FORMAT_COMPAT}

# Codecs iOS Safari plays natively in an mp4 container (prefixes as yt-dlp reports them).
SAFARI_VCODECS = ("avc1", "h264", "hvc1", "hev1", "hevc")


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


def needs_vlc(vcodec: str | None) -> bool:
    """True when the video codec probably won't play in iOS Safari (VP9, AV1...)."""
    if not vcodec or vcodec == "none":
        return False
    return not vcodec.lower().startswith(SAFARI_VCODECS)
