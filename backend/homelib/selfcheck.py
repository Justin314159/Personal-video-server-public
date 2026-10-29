"""Self-check: hardware, tools and configuration report.

Runs with the plain system python (stdlib only) so it also works before
install. Usage: selfcheck.sh   or   python -m homelib selfcheck [--json]
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr).strip()
    except Exception:
        return ""


def cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def meminfo() -> dict[str, int]:
    out = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            out[k] = int(v.split()[0]) * 1024
    except OSError:
        pass
    return out


def gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def collect() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    add = lambda status, name, detail: rows.append((status, name, detail))  # noqa: E731

    add(OK if platform.machine() in ("x86_64", "AMD64") else WARN, "Architecture", platform.machine())
    add(OK, "CPU", f"{cpu_model()} ({os.cpu_count()} threads)")
    mem = meminfo()
    total = mem.get("MemTotal", 0)
    add(OK if total >= 3 * 1024 ** 3 else WARN, "RAM",
        gb(total) + ("" if total >= 3 * 1024 ** 3 else "  <- under 3 GB: keep 1 download at a time"))
    swap = mem.get("SwapTotal", 0)
    add(OK if swap >= 1024 ** 3 else WARN, "Swap", gb(swap) + ("" if swap else "  <- install.sh creates 2 GB"))
    try:
        osr = dict(l.split("=", 1) for l in Path("/etc/os-release").read_text().splitlines() if "=" in l)
        add(OK, "OS", osr.get("PRETTY_NAME", "?").strip('"'))
    except OSError:
        add(WARN, "OS", platform.platform())
    pv = sys.version_info
    add(OK if pv >= (3, 10) else FAIL, "Python", platform.python_version()
        + ("" if pv >= (3, 11) else "  (3.10: older yt-dlp only; see docs/OPERATIONS.md)"))

    # Config (needs tomllib on 3.11+, or tomli in the venv on 3.10)
    cfg = None
    try:
        from homelib.config import load_config, resolve_bind

        cfg = load_config()
        found = Path(cfg.source_path).is_file()
        add(OK if found else WARN, "Config", cfg.source_path + ("" if found else "  <- missing, using defaults"))
        try:
            add(OK, "Bind address", f"{resolve_bind(cfg)}:{cfg.port}"
                + ("  <- ALL INTERFACES" if cfg.allow_all_interfaces else ""))
        except Exception as e:
            add(FAIL, "Bind address", str(e))
    except Exception as e:
        add(WARN, "Config", f"not loaded ({e})")

    lib = Path(cfg.library_dir) if cfg else Path("/var/lib/homelib/library")
    probe = lib
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    du = shutil.disk_usage(probe)
    min_free = (cfg.min_free_gb if cfg else 10) * 1024 ** 3
    add(OK if du.free > min_free else WARN, "Free disk", f"{gb(du.free)} of {gb(du.total)} at {probe}"
        + ("" if du.free > min_free else "  <- below min_free_gb: downloads refused"))

    ff = shutil.which("ffmpeg")
    src = "system"
    if not ff:
        try:
            import imageio_ffmpeg

            ff, src = imageio_ffmpeg.get_ffmpeg_exe(), "imageio-ffmpeg (fallback)"
        except Exception:
            ff = None
    if ff:
        add(OK, "ffmpeg", f"{_run([ff, '-version']).splitlines()[0][:70]} [{src}]")
    else:
        add(FAIL, "ffmpeg", "missing (apt install ffmpeg, or install.sh's vendored imageio-ffmpeg)")
    add(OK if shutil.which("ionice") and shutil.which("nice") else WARN, "nice/ionice",
        "available" if shutil.which("ionice") else "missing; ffmpeg runs at normal priority")

    try:
        from yt_dlp.version import __version__ as ytv

        add(OK, "yt-dlp", ytv)
    except Exception:
        add(WARN, "yt-dlp", "not importable with this python (run inside the venv)")

    js = None
    venv_deno = Path(sys.executable).with_name("deno")
    for cand in ([str(venv_deno)] if venv_deno.is_file() else []) + [shutil.which("deno"), shutil.which("node")]:
        if cand:
            js = cand
            break
    if js:
        add(OK, "JS runtime", f"{Path(js).name} {_run([js, '--version']).splitlines()[0] if _run([js, '--version']) else ''} ({js})")
    else:
        add(WARN, "JS runtime", "no deno/node: YouTube downloads may fail (install.sh vendors deno)")

    ts = shutil.which("tailscale")
    if ts:
        ip = _run(["tailscale", "ip", "-4"]).splitlines()
        add(OK if ip else WARN, "Tailscale", f"installed, IP {ip[0] if ip else '(not up)'}")
    else:
        add(WARN, "Tailscale", "not installed (see end of install.sh output)")

    if shutil.which("systemctl") and Path("/run/systemd/system").exists():
        st = _run(["systemctl", "is-active", "homelib"]) or "unknown"
        add(OK if st == "active" else WARN, "Service", f"homelib.service is {st}")
        if cfg and st == "active":
            import urllib.request

            host = "127.0.0.1" if cfg.bind in ("127.0.0.1", "0.0.0.0") else None
            if host:
                try:
                    with urllib.request.urlopen(f"http://{host}:{cfg.port}/api/health", timeout=5) as r:
                        add(OK, "HTTP health", f"{r.status} from http://{host}:{cfg.port}")
                except Exception as e:
                    add(FAIL, "HTTP health", str(e))
    return rows


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    rows = collect()
    if "--json" in argv:
        print(json.dumps([{"status": s.strip(), "check": n, "detail": d} for s, n, d in rows], indent=2))
    else:
        print("Home Video Library self-check\n" + "-" * 60)
        for s, n, d in rows:
            print(f"[{s}] {n:<14} {d}")
    fails = [r for r in rows if r[0] == FAIL]
    if fails:
        print(f"\n{len(fails)} problem(s) need fixing.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.exit(main(sys.argv[1:]))
