"""Configuration loading.

Config lives in a TOML file (default /etc/homelib/config.toml, override with
HOMELIB_CONFIG). Every key has a default so an empty file is valid.
A few keys can also be set with HOMELIB_* environment variables (see
.env.example); those win over the file.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10 (Ubuntu 22.04)
    import tomli as tomllib

log = logging.getLogger("homelib.config")

DEFAULT_CONFIG_PATH = "/etc/homelib/config.toml"


@dataclass
class Config:
    # [server]
    port: int = 8787
    bind: str = "127.0.0.1"  # "127.0.0.1", "tailscale", or an explicit IP
    allow_all_interfaces: bool = False
    # [library]
    data_dir: Path = Path("/var/lib/homelib")
    library_dir: Path = Path("/var/lib/homelib/library")
    min_free_gb: float = 10.0
    # [downloads]
    max_concurrent: int = 2  # hard cap across both worker pools
    foreground_workers: int = 1  # user-requested downloads
    background_workers: int = 1  # shorts prefetch + candidate fetch
    default_quality: str = "compat"
    ffmpeg_nice: int = 10
    subtitles: bool = True  # fetch captions with each video (for CC + transcript)
    subtitle_langs: list[str] = field(default_factory=lambda: ["en"])
    # [cleanup]
    auto_delete_hours: int = 24
    # [profiles]
    profile_names: list[str] = field(default_factory=lambda: ["Profile 1", "Profile 2"])
    # [recommender]
    nightly_time: str = "03:30"
    embeddings: bool = False
    shorts_prefetch: int = 10
    # [tools]
    ffmpeg_path: str = ""  # empty = auto (system ffmpeg, then imageio-ffmpeg)
    js_runtime_path: str = ""  # empty = auto (deno in venv/PATH, then node)
    cookies_file: str = ""  # optional cookies.txt exported from your own signed-in YouTube session
    # runtime
    mock: bool = False
    source_path: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "homelib.db"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def subs_dir(self) -> Path:
        return self.library_dir / "subs"


def _get(d: dict, section: str, key: str, default):
    return d.get(section, {}).get(key, default)


def load_config(path: str | None = None) -> Config:
    path = path or os.environ.get("HOMELIB_CONFIG") or DEFAULT_CONFIG_PATH
    raw: dict = {}
    if Path(path).is_file():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    else:
        log.warning("config file %s not found, using defaults", path)
    c = Config()
    c.source_path = str(path)
    c.port = int(_get(raw, "server", "port", c.port))
    c.bind = str(_get(raw, "server", "bind", c.bind))
    c.allow_all_interfaces = bool(_get(raw, "server", "allow_all_interfaces", False))
    c.data_dir = Path(_get(raw, "library", "data_dir", str(c.data_dir)))
    c.library_dir = Path(_get(raw, "library", "dir", str(c.data_dir / "library")))
    c.min_free_gb = float(_get(raw, "library", "min_free_gb", c.min_free_gb))
    c.max_concurrent = max(1, min(int(_get(raw, "downloads", "max_concurrent", c.max_concurrent)), 4))
    c.foreground_workers = max(1, int(_get(raw, "downloads", "foreground_workers", 1)))
    c.background_workers = max(0, int(_get(raw, "downloads", "background_workers", 1)))
    # Never exceed max_concurrent in total; the foreground pool wins.
    c.foreground_workers = min(c.foreground_workers, c.max_concurrent)
    c.background_workers = min(c.background_workers, c.max_concurrent - c.foreground_workers)
    q = str(_get(raw, "downloads", "default_quality", c.default_quality))
    c.default_quality = q if q in ("best", "compat") else "compat"
    c.ffmpeg_nice = int(_get(raw, "downloads", "ffmpeg_nice", c.ffmpeg_nice))
    c.subtitles = bool(_get(raw, "downloads", "subtitles", True))
    langs = _get(raw, "downloads", "subtitle_langs", c.subtitle_langs)
    if isinstance(langs, list) and langs:
        c.subtitle_langs = [str(x).strip().lower() for x in langs if str(x).strip()][:4] or ["en"]
    h = int(_get(raw, "cleanup", "auto_delete_hours", c.auto_delete_hours))
    c.auto_delete_hours = h if h in (12, 24, 48) else 24
    names = _get(raw, "profiles", "names", c.profile_names)
    if isinstance(names, list) and len(names) >= 2:
        c.profile_names = [str(n) for n in names[:2]]
    c.nightly_time = str(_get(raw, "recommender", "nightly_time", c.nightly_time))
    c.embeddings = bool(_get(raw, "recommender", "embeddings", False))
    c.shorts_prefetch = max(10, int(_get(raw, "recommender", "shorts_prefetch", 10)))
    c.ffmpeg_path = str(_get(raw, "tools", "ffmpeg", ""))
    c.js_runtime_path = str(_get(raw, "tools", "js_runtime", ""))
    c.cookies_file = str(_get(raw, "tools", "cookies_file", ""))
    c.mock = os.environ.get("HOMELIB_MOCK", "") == "1" or bool(_get(raw, "server", "mock", False))
    # Environment overrides used by run.sh and tests.
    if os.environ.get("HOMELIB_DATA_DIR"):
        c.data_dir = Path(os.environ["HOMELIB_DATA_DIR"])
        c.library_dir = Path(os.environ.get("HOMELIB_LIBRARY_DIR", c.data_dir / "library"))
    if os.environ.get("HOMELIB_PORT"):
        c.port = int(os.environ["HOMELIB_PORT"])
    if os.environ.get("HOMELIB_BIND"):
        c.bind = os.environ["HOMELIB_BIND"]
    env_names = [n.strip() for n in os.environ.get("HOMELIB_PROFILE_NAMES", "").split(",") if n.strip()]
    if len(env_names) >= 2:
        c.profile_names = env_names[:2]
    if os.environ.get("HOMELIB_COOKIES_FILE"):
        c.cookies_file = os.environ["HOMELIB_COOKIES_FILE"]
    return c


# --------------------------------------------------------------------------
# Bind address resolution. The rule: never listen on all interfaces unless the
# config explicitly says allow_all_interfaces = true.
# --------------------------------------------------------------------------
ALL_INTERFACES = {"0.0.0.0", "::", "", "*"}
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


class BindError(RuntimeError):
    pass


def detect_tailscale_ip() -> str | None:
    """Find this machine's Tailscale IPv4 (100.64.0.0/10), without needing root."""
    if shutil.which("tailscale"):
        try:
            out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
            for line in out.stdout.split():
                if ipaddress.ip_address(line.strip()) in TAILSCALE_NET:
                    return line.strip()
        except Exception:
            pass
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5)
        for line in out.stdout.splitlines():
            parts = line.split()
            if "inet" in parts:
                ip = parts[parts.index("inet") + 1].split("/")[0]
                if ipaddress.ip_address(ip) in TAILSCALE_NET:
                    return ip
    except Exception:
        pass
    return None


def resolve_bind(cfg: Config) -> str:
    bind = cfg.bind.strip()
    if bind in ALL_INTERFACES:
        if not cfg.allow_all_interfaces:
            raise BindError(
                f"bind = '{bind}' would listen on ALL interfaces. Refusing. "
                "Use 127.0.0.1 (with 'tailscale serve') or bind = \"tailscale\". "
                "If you really mean it, set allow_all_interfaces = true."
            )
        banner = "!" * 72
        log.warning(
            "\n%s\n!! WARNING: listening on ALL interfaces (%s). Anyone who can reach\n"
            "!! this machine on port %d can use the app. There is NO login.\n%s",
            banner, bind or "0.0.0.0", cfg.port, banner,
        )
        return "0.0.0.0" if bind in ("", "*") else bind
    if bind.lower() == "tailscale":
        ip = detect_tailscale_ip()
        if not ip:
            log.warning("bind = 'tailscale' but no Tailscale IP found; falling back to 127.0.0.1")
            return "127.0.0.1"
        return ip
    ipaddress.ip_address(bind)  # raises ValueError for garbage
    return bind
