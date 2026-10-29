"""Runs the REAL yt-dlp + ffmpeg code path (not the mock) against a local web
server serving an mp4, so format selection, progress hooks, merging/extraction
and file handling are exercised without YouTube."""
import functools
import http.server
import shutil
import subprocess
import threading

import pytest

from homelib.config import Config
from homelib.tools import find_ffmpeg, make_low_priority_wrappers
from homelib.ytlib import Stopped, YouTube

ffmpeg, _ = find_ffmpeg()
pytestmark = pytest.mark.skipif(not ffmpeg, reason="needs ffmpeg")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    root = tmp_path_factory.mktemp("www")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(root / "clip.mp4")], check=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/clip.mp4"
    srv.shutdown()


@pytest.fixture
def yt(tmp_path, monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    cfg = Config(mock=False, data_dir=tmp_path)
    wrappers = make_low_priority_wrappers(tmp_path / "bin", ffmpeg)
    return YouTube(cfg, ffmpeg, wrappers, (None, None))


@pytest.mark.parametrize("quality,ext", [("compat", ".mp4"), ("best", ".mp4"), ("mp3", ".mp3"), ("m4a", ".m4a")])
def test_real_download(yt, served, tmp_path, quality, ext):
    seen = []
    res = yt.download("clip", served, quality, False, tmp_path / "out", seen.append, lambda: None)
    assert res["file_path"].endswith(ext)
    assert res["file_size"] > 1000
    assert seen, "progress hook never fired"
    if ext != ".mp4":
        probe = subprocess.run([ffmpeg, "-i", res["file_path"]], capture_output=True, text=True).stderr
        assert "Video:" not in probe.split("Input #0")[1].split("Output")[0] or "mjpeg" in probe  # audio (+cover art)
        assert "Audio: " + ("mp3" if quality == "mp3" else "aac") in probe


def test_real_cancel(yt, served, tmp_path):
    with pytest.raises(Stopped) as e:
        yt.download("clip", served, "compat", False, tmp_path / "out", lambda p: None, lambda: "cancel")
    assert e.value.what == "cancel"
