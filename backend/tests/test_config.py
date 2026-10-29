import pytest

from homelib.config import BindError, Config, load_config, resolve_bind


def test_default_bind_is_localhost():
    assert resolve_bind(Config()) == "127.0.0.1"


@pytest.mark.parametrize("bind", ["0.0.0.0", "::", ""])
def test_all_interfaces_refused_by_default(bind):
    with pytest.raises(BindError):
        resolve_bind(Config(bind=bind))


def test_all_interfaces_with_flag(caplog):
    assert resolve_bind(Config(bind="0.0.0.0", allow_all_interfaces=True)) == "0.0.0.0"
    assert "ALL interfaces" in caplog.text


def test_tailscale_fallback(monkeypatch):
    import homelib.config as c

    monkeypatch.setattr(c, "detect_tailscale_ip", lambda: None)
    assert resolve_bind(Config(bind="tailscale")) == "127.0.0.1"
    monkeypatch.setattr(c, "detect_tailscale_ip", lambda: "100.64.0.1")
    assert resolve_bind(Config(bind="tailscale")) == "100.64.0.1"


def test_garbage_bind():
    with pytest.raises(ValueError):
        resolve_bind(Config(bind="not-an-ip"))


def test_load_example(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text('[server]\nport = 9000\n[library]\ndir = "/srv/vids"\nmin_free_gb = 5\n'
                 '[downloads]\nmax_concurrent = 1\nbackground_workers = 1\n[cleanup]\nauto_delete_hours = 13\n'
                 '[profiles]\nnames = ["A", "B"]\n')
    monkeypatch.delenv("HOMELIB_DATA_DIR", raising=False)
    for k in ("HOMELIB_PORT", "HOMELIB_BIND", "HOMELIB_PROFILE_NAMES", "HOMELIB_COOKIES_FILE"):
        monkeypatch.delenv(k, raising=False)
    c = load_config(str(p))
    assert c.port == 9000 and str(c.library_dir) == "/srv/vids" and c.min_free_gb == 5
    assert c.foreground_workers == 1 and c.background_workers == 0  # capped by max_concurrent
    assert c.auto_delete_hours == 24  # invalid value -> default
    assert c.profile_names == ["A", "B"]


def test_env_overrides(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text('[server]\nbind = "127.0.0.1"\n[profiles]\nnames = ["A", "B"]\n')
    monkeypatch.setenv("HOMELIB_BIND", "tailscale")
    monkeypatch.setenv("HOMELIB_PROFILE_NAMES", " Alex , Sam ")
    monkeypatch.setenv("HOMELIB_COOKIES_FILE", "/etc/homelib/youtube-cookies.txt")
    c = load_config(str(p))
    assert c.bind == "tailscale" and c.profile_names == ["Alex", "Sam"]
    assert c.cookies_file == "/etc/homelib/youtube-cookies.txt"
    monkeypatch.setenv("HOMELIB_PROFILE_NAMES", "OnlyOne")  # needs two names; file wins
    assert load_config(str(p)).profile_names == ["A", "B"]
