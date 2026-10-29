"""Locating ffmpeg and a JavaScript runtime, and wrapping ffmpeg at low priority."""
from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("homelib.tools")


def find_ffmpeg(configured: str = "") -> tuple[str | None, str]:
    """Return (path, source). Prefers config, then system ffmpeg, then imageio-ffmpeg."""
    if configured and Path(configured).is_file():
        return configured, "config"
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff, "system"
    try:
        import imageio_ffmpeg  # bundled static binary, used when apt ffmpeg is absent

        return imageio_ffmpeg.get_ffmpeg_exe(), "imageio-ffmpeg"
    except Exception:
        return None, "missing"


def find_ffprobe(ffmpeg: str | None) -> str | None:
    if ffmpeg:
        cand = Path(ffmpeg).with_name("ffprobe")
        if cand.is_file():
            return str(cand)
    return shutil.which("ffprobe")


def find_js_runtime(configured: str = "") -> tuple[str | None, str | None]:
    """Return (runtime name, path). yt-dlp needs one to solve YouTube's JS challenges."""
    if configured and Path(configured).is_file():
        name = "node" if "node" in Path(configured).name else "deno"
        return name, configured
    # The deno PyPI wheel puts a `deno` binary next to the venv's python.
    venv_deno = Path(sys.executable).with_name("deno")
    if venv_deno.is_file():
        return "deno", str(venv_deno)
    for name in ("deno", "node"):
        p = shutil.which(name)
        if p:
            return name, p
    return None, None


def tool_version(path: str | None, arg: str = "-version") -> str | None:
    if not path:
        return None
    try:
        out = subprocess.run([path, arg], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).splitlines()[0].strip()
    except Exception:
        return None


def make_low_priority_wrappers(bin_dir: Path, ffmpeg: str, nice: int = 10) -> Path:
    """Create ffmpeg/ffprobe shell wrappers that run at `nice` and idle IO priority.

    yt-dlp spawns ffmpeg for merging; pointing its ffmpeg_location at these
    wrappers keeps merges from starving the web server on a small CPU.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    ionice = shutil.which("ionice")
    nice_bin = shutil.which("nice")
    prefix = ""
    if nice_bin:
        prefix += f'"{nice_bin}" -n {int(nice)} '
    if ionice:
        prefix += f'"{ionice}" -c 3 '
    targets = {"ffmpeg": ffmpeg, "ffprobe": find_ffprobe(ffmpeg)}
    for name, real in targets.items():
        if not real:
            continue
        w = bin_dir / name
        w.write_text(f'#!/bin/sh\nexec {prefix}"{real}" "$@"\n')
        w.chmod(w.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def set_thread_nice(n: int) -> None:
    """Lower the priority of the *calling thread* only (Linux per-thread nice)."""
    try:
        import threading

        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), n)
    except Exception as e:  # not fatal (e.g. non-Linux)
        log.debug("could not renice thread: %s", e)
