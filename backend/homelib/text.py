"""Keyword extraction for the recommender. Deliberately simple and explainable."""
from __future__ import annotations

import re

STOPWORDS = set(
    """
a about above after again against all also am an and any are aren as at be because been before being
below between both but by can cannot could did didn do does doesn doing don down during each even ever
every few for from further get gets got had has have having he her here hers herself him himself his how
i if in into is isn it its itself just let me more most much my myself no nor not now of off on once only
or other our ours ourselves out over own really same she should so some such than that the their theirs
them themselves then there these they this those through to too under until up us very via vs was we
were what when where which while who whom why will with without won would you your yours yourself
yourselves
video videos official full new best top episode ep part pt live stream shorts short watch watching
youtube channel subscribe ft feat featuring 4k hd 1080p 720p trailer music clip how why what ever
amp quot one two three first make made making day days time way thing things like vs lol omg
""".split()
)

_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)
MAX_KEYWORDS = 12


def keywords(*texts: str, limit: int = MAX_KEYWORDS) -> list[str]:
    """Lowercased, de-duplicated content words in order of first appearance."""
    out: list[str] = []
    seen: set[str] = set()
    for t in texts:
        for w in _WORD.findall((t or "").lower()):
            w = w.strip("'")
            if w.endswith("'s"):
                w = w[:-2]
            if len(w) < 3 or w.isdigit() or w in STOPWORDS or w in seen:
                continue
            seen.add(w)
            out.append(w)
            if len(out) >= limit:
                return out
    return out


# Words that are common in video titles but say nothing about the topic. The
# interest labels, the interest searches and the built-in embedder skip them.
GENERIC = set(
    """
explained explain explains explaining explainer truth everything nobody tells told beginner beginners
guide guides tutorial tutorials tips tricks ultimate complete deep dive intro introduction basics basic
learn learning lesson lessons course easy easiest simple simply quick quickly minute minutes hour hours week
weeks year years matter matters really actually thing things way ways why what how ever never always
inside behind secret secrets amazing incredible insane crazy best worst top new old full part episode
review reviews reaction vlog story stories look looking trying tried try test tested first last
""".split()
)


def topic_words(*texts: str, limit: int = MAX_KEYWORDS) -> list[str]:
    """keywords() without the generic title words."""
    return [w for w in keywords(*texts, limit=limit * 2) if w not in GENERIC][:limit]
