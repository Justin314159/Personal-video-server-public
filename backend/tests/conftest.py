import os
import time

import pytest

os.environ["HOMELIB_MOCK"] = "1"
os.environ.setdefault("HOMELIB_MOCK_DL_SECONDS", "0")

from homelib import db, library  # noqa: E402
from homelib.config import Config  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    c = Config()
    c.data_dir = tmp_path / "data"
    c.library_dir = tmp_path / "data" / "library"
    c.min_free_gb = 0
    c.mock = True
    c.source_path = "test"
    return c


@pytest.fixture
def fresh_db(cfg):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    db.init(cfg.db_path)
    library.ensure_profiles(["Alex", "Partner"])
    return cfg


@pytest.fixture
def client(cfg):
    from fastapi.testclient import TestClient

    from homelib.app import create_app

    app = create_app(cfg, start_workers=True, start_scheduler=False)
    with TestClient(app) as c:
        c.app_ref = app
        yield c


def wait_for(fn, timeout=15.0, interval=0.05):
    end = time.time() + timeout
    while time.time() < end:
        r = fn()
        if r:
            return r
        time.sleep(interval)
    raise AssertionError("condition not met in time")
