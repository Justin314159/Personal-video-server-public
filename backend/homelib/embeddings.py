"""OPTIONAL semantic similarity with sentence-transformers. OFF by default.

Not recommended on the target box: the model + PyTorch need roughly 1-2 GB of
RAM and the CPU would take seconds per batch. It's here so a bigger machine
can turn it on with `[recommender] embeddings = true` after
`pip install sentence-transformers` (not vendored in the USB package).
"""
from __future__ import annotations

import logging

log = logging.getLogger("homelib.embeddings")
_model = None
_failed = False


def available() -> bool:
    global _model, _failed
    if _model is not None:
        return True
    if _failed:
        return False
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        _model = SentenceTransformer("all-MiniLM-L6-v2")
        return True
    except Exception as e:  # not installed, or no RAM
        log.warning("embeddings unavailable: %s", e)
        _failed = True
        return False


def similarity(liked_titles: list[str], candidate_titles: list[str]) -> list[float]:
    """Max cosine similarity of each candidate to any liked title (0..1)."""
    if not liked_titles or not candidate_titles or not available():
        return [0.0] * len(candidate_titles)
    a = _model.encode(liked_titles, normalize_embeddings=True)
    b = _model.encode(candidate_titles, normalize_embeddings=True)
    sims = b @ a.T
    return [float(max(0.0, s.max())) for s in sims]
