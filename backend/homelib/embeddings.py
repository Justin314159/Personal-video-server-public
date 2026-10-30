"""Sentence embeddings for the recommender, stored in SQLite.

Two interchangeable models (`[recommender] embedding_model`):

* "minilm": all-MiniLM-L6-v2 through fastembed (ONNX Runtime, CPU, no PyTorch).
  About 150-250 MB of RAM once loaded and a few ms per title. fastembed isn't
  in the USB package (ONNX Runtime adds ~85 MB per Python version), so it's an
  add-on: `sudo bash /opt/homelib/install_embeddings.sh` (see updates/1.5.0.md),
  which installs hash-pinned packages and downloads the model once; the app
  then only ever loads it from disk.
* "builtin": a dependency-free hashing embedder. Words, word pairs, word
  pieces, the channel and the topic lexicon (topics.py) are hashed into 512
  dimensions. Not as smart as MiniLM, but it groups "black hole" with "Mars
  atmosphere" via the lexicon and needs no RAM worth mentioning.

"auto" (the default) uses MiniLM when it's installed and loads, else builtin.
Vectors are cached in the `embeddings` table per model, so switching models
just re-embeds in the background. All vectors are L2-normalised, so cosine
similarity is a dot product.

Similarities mean different things per model, so each model carries its own
thresholds (see THRESHOLDS); callers use `sim_scale()` to map a raw cosine to
0 (unrelated) .. 1 (clearly the same topic).
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import threading
import time
from array import array
from operator import mul
from pathlib import Path

from . import db, text, topics
from .db import now

log = logging.getLogger("homelib.embeddings")

BUILTIN = "builtin-v1"
MINILM = "minilm-l6-v2"
MINILM_HF = "sentence-transformers/all-MiniLM-L6-v2"

# low: typical cosine of unrelated texts; high: clearly the same topic.
# join: an item joins an interest cluster; merge: two clusters are one;
# block: a candidate counts as a blocked topic; near: "basically the same video".
THRESHOLDS = {
    BUILTIN: {"low": 0.08, "high": 0.45, "join": 0.30, "merge": 0.42, "block": 0.30, "near": 0.62},
    MINILM: {"low": 0.15, "high": 0.60, "join": 0.42, "merge": 0.55, "block": 0.45, "near": 0.80},
}

# Mock mode (tests, preview) never loads MiniLM unless asked to explicitly.
_cfg = {"mode": "builtin" if os.environ.get("HOMELIB_MOCK") == "1" else "auto", "cache_dir": None, "threads": 1}
_lock = threading.RLock()
_minilm = None            # the loaded fastembed model
_minilm_failed_at = 0.0   # when loading last failed (retried after RETRY_FAILED)
_fastembed_ok: bool | None = None
RETRY_FAILED = 6 * 3600
BATCH = 32


def configure(mode: str = "auto", cache_dir: Path | None = None, threads: int = 1) -> None:
    global _minilm, _minilm_failed_at, _fastembed_ok
    _cfg.update(mode=(mode or "auto").lower(), cache_dir=cache_dir, threads=max(1, int(threads)))
    _minilm, _minilm_failed_at, _fastembed_ok = None, 0.0, None


# ---- vectors --------------------------------------------------------------------

Vec = list  # list[float], L2-normalised


def pack(v: Vec) -> bytes:
    return array("f", v).tobytes()


def unpack(b: bytes) -> Vec:
    a = array("f")
    a.frombytes(b)
    return a.tolist()


def dot(a: Vec, b: Vec) -> float:
    return sum(map(mul, a, b)) if len(a) == len(b) else 0.0


def normalize(v: Vec) -> Vec:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-12 else v


def weighted_mean(vs: list[Vec], ws: list[float]) -> Vec | None:
    if not vs:
        return None
    out = [0.0] * len(vs[0])
    for v, w in zip(vs, ws):
        if len(v) != len(out):
            continue
        for i, x in enumerate(v):
            out[i] += w * x
    return normalize(out)


# ---- the built-in hashing embedder --------------------------------------------------

DIM = 512
_FEATURE_WEIGHTS = {"w": 1.0, "b": 0.5, "g": 0.18, "t": 2.0, "c": 0.7}


def _hash(feature: str) -> tuple[int, float]:
    h = hashlib.blake2b(feature.encode(), digest_size=8).digest()
    return int.from_bytes(h[:4], "little") % DIM, (1.0 if h[4] & 1 else -1.0)


def builtin_embed(s: str, channel: str = "") -> Vec:
    v = [0.0] * DIM
    content = [w for w in topics.words(s) if len(w) >= 3 and w not in text.STOPWORDS and not w.isdigit()]

    def add(kind: str, feat: str, weight: float = 1.0) -> None:
        i, sign = _hash(f"{kind}:{feat}")
        v[i] += sign * weight * _FEATURE_WEIGHTS[kind]

    for w in content:
        add("w", w, 0.25 if w in text.GENERIC else 1.0)
        if len(w) >= 6:  # word pieces: "astronomy" ~ "astronomer"
            grams = [w[i:i + 4] for i in range(len(w) - 3)]
            for g in grams:
                add("g", g, 1 / math.sqrt(len(grams)))
    for a, b in zip(content, content[1:]):
        if a not in text.GENERIC and b not in text.GENERIC:
            add("b", f"{a}_{b}")
    for key, n in topics.topics_in(s).items():
        add("t", key, math.sqrt(n))
    if channel:
        add("c", channel.lower())
    return normalize(v)


# ---- MiniLM via fastembed --------------------------------------------------------------

def fastembed_installed() -> bool:
    global _fastembed_ok
    if _fastembed_ok is None:
        import importlib.util

        _fastembed_ok = importlib.util.find_spec("fastembed") is not None
    return _fastembed_ok


def _load_minilm() -> bool:
    """Load the model (slow the first time: it may download ~90 MB)."""
    global _minilm, _minilm_failed_at
    with _lock:
        if _minilm is not None:
            return True
        if not fastembed_installed() or (_minilm_failed_at and time.time() - _minilm_failed_at < RETRY_FAILED):
            return False
        try:
            # Offline only: the model was downloaded once by install_embeddings.sh;
            # the app never contacts Hugging Face (no update checks, no telemetry).
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
            from fastembed import TextEmbedding

            kw = {"threads": _cfg["threads"], "local_files_only": True}
            if _cfg["cache_dir"]:
                kw["cache_dir"] = str(_cfg["cache_dir"])
            _minilm = TextEmbedding(MINILM_HF, **kw)
            log.info("embedding model %s loaded", MINILM)
            return True
        except Exception as e:
            _minilm_failed_at = time.time()
            log.warning("MiniLM unavailable (run: sudo bash /opt/homelib/install_embeddings.sh), "
                        "using the built-in embedder: %s", e)
            return False


def model_name() -> str:
    """The model whose vectors the recommender uses right now."""
    mode = _cfg["mode"]
    if mode in ("builtin", "off", "false"):
        return BUILTIN
    if _minilm is not None:
        return MINILM
    if fastembed_installed() and not (_minilm_failed_at and time.time() - _minilm_failed_at < RETRY_FAILED):
        return MINILM  # not loaded yet: loads in the next background job
    return BUILTIN


def ready() -> bool:
    """Can vectors for the current model be computed without loading anything?"""
    return model_name() == BUILTIN or _minilm is not None


def status() -> dict:
    return {"model": model_name(), "loaded": ready(), "mode": _cfg["mode"],
            "fastembed_installed": fastembed_installed(),
            "label": "MiniLM (all-MiniLM-L6-v2)" if model_name() == MINILM else "Built-in (no model installed)"}


def thresholds(model: str | None = None) -> dict:
    return THRESHOLDS[model or model_name()]


def sim_scale(sim: float, model: str | None = None) -> float:
    t = thresholds(model)
    return max(0.0, min(1.0, (sim - t["low"]) / (t["high"] - t["low"])))


def _compute(model: str, items: list[tuple[str, str]]) -> list[Vec]:
    if model == BUILTIN:
        return [builtin_embed(t, ch) for t, ch in items]
    if not _load_minilm():
        raise RuntimeError("MiniLM not available")
    with _lock:
        out = []
        for i in range(0, len(items), BATCH):
            chunk = [(f"{t} ({ch})" if ch else t)[:1000] for t, ch in items[i:i + BATCH]]
            out += [normalize([float(x) for x in v]) for v in _minilm.embed(chunk, batch_size=BATCH)]
        return out


# ---- the cache --------------------------------------------------------------------------

def text_key(s: str) -> str:
    return "t:" + hashlib.sha1(s.strip().lower().encode()).hexdigest()[:20]


def cached(keys: list[str], model: str | None = None) -> dict[str, Vec]:
    model = model or model_name()
    out: dict[str, Vec] = {}
    keys = list(dict.fromkeys(k for k in keys if k))
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        for r in db.q(f"SELECT key, vec FROM embeddings WHERE model=? AND key IN ({','.join('?' * len(part))})",
                      (model, *part)):
            out[r["key"]] = unpack(r["vec"])
    return out


def store(key: str, vec: Vec, model: str | None = None) -> None:
    db.x("INSERT INTO embeddings(key, model, vec, created_at) VALUES(?,?,?,?) "
         "ON CONFLICT(key, model) DO UPDATE SET vec=excluded.vec, created_at=excluded.created_at",
         (key, model or model_name(), pack(vec), now()))


def vectors(items: dict[str, tuple[str, str]], compute: bool = True) -> dict[str, Vec]:
    """{key: (text, channel)} -> {key: vector} for the current model.

    Cached vectors are always returned. Missing ones are computed when
    `compute` is set and the model is ready (request handlers pass
    compute=ready() so they never wait for a model to load)."""
    model = model_name()
    out = cached(list(items), model)
    missing = [k for k in items if k not in out and (items[k][0] or "").strip()]
    if missing and compute:
        try:
            for i in range(0, len(missing), 256):
                part = missing[i:i + 256]
                vecs = _compute(model, [items[k] for k in part])
                with db.tx():
                    for k, v in zip(part, vecs):
                        store(k, v, model)
                        out[k] = v
        except Exception as e:
            log.info("embedding %d texts failed: %s", len(missing), e)
    return out


def embed_text(s: str, compute: bool = True) -> Vec | None:
    k = text_key(s)
    return vectors({k: (s, "")}, compute=compute).get(k)


def prune(keep_days: int = 90) -> int:
    """Drop vectors of other models and of candidates nobody needs any more."""
    cur = model_name()
    n = db.x("DELETE FROM embeddings WHERE model<>? AND created_at < ?", (cur, now() - 7 * 86400)).rowcount
    n += db.x("DELETE FROM embeddings WHERE created_at < ? AND key NOT LIKE 't:%' AND key NOT LIKE 'block:%' "
              "AND key NOT IN (SELECT id FROM videos) AND key NOT IN (SELECT video_id FROM reco_cache) "
              "AND key NOT IN (SELECT video_id FROM channel_uploads) "
              "AND key NOT IN (SELECT video_id FROM events WHERE video_id IS NOT NULL)",
              (now() - keep_days * 86400,)).rowcount
    return n


class RefSet:
    """A set of reference vectors to compare many candidates against.

    Uses numpy when it's installed (it comes with fastembed); otherwise a pure
    Python loop over the candidate's non-zero entries, which is fast for the
    sparse built-in vectors."""

    def __init__(self, vecs: list[Vec]):
        self.vecs = [v for v in vecs if v]
        self._m = None
        if self.vecs:
            try:
                import numpy as np

                self._np = np
                self._m = np.asarray(self.vecs, dtype=np.float32)
            except Exception:
                self._m = None

    def __len__(self) -> int:
        return len(self.vecs)

    def sims(self, v: Vec | None) -> list[float]:
        if not self.vecs or not v:
            return [0.0] * len(self.vecs)
        if self._m is not None and len(v) == self._m.shape[1]:
            return (self._m @ self._np.asarray(v, dtype=self._np.float32)).tolist()
        sp = [(i, x) for i, x in enumerate(v) if x]
        return [sum(r[i] * x for i, x in sp) if len(r) == len(v) else 0.0 for r in self.vecs]
