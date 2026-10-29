"""Entry point: `python -m homelib` (what the systemd unit and run.sh call)."""
from __future__ import annotations

import logging
import sys

from .config import BindError, load_config, resolve_bind


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "selfcheck":
        from .selfcheck import main as sc

        return sc(sys.argv[2:])
    cfg = load_config()
    try:
        host = resolve_bind(cfg)
    except (BindError, ValueError) as e:
        logging.getLogger("homelib").error("%s", e)
        return 2
    import uvicorn

    from .app import create_app

    app = create_app(cfg)
    # One worker on purpose: background threads + SQLite + 4 GB RAM.
    uvicorn.run(app, host=host, port=cfg.port, workers=1, log_level="info",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
