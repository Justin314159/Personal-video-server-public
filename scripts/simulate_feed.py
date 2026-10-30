#!/usr/bin/env python3
"""Simulate the For you feed on a fake history and print the mix.

No internet and no server needed: a synthetic catalog of ~500 videos in 16
topics stands in for YouTube, and a throwaway database holds the history.

    python scripts/simulate_feed.py                 # the default scenarios
    python scripts/simulate_feed.py --exploration 0.4
    python scripts/simulate_feed.py --verbose       # also list the feed and the searches

Scenarios:
  1. 2 space videos + 20 varied videos (the "two Khan Academy videos took over"
     complaint): space should be one interest among many, not the feed.
  2. Only 2 space videos (a near-empty history): space is capped, the rest is
     exploration.
  3. Scenario 1 + "Not interested" on two cooking videos: cooking stays out.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("HOMELIB_MOCK", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from homelib import db, embeddings, interests, library, recommender, text  # noqa: E402

TOPICS = {
    "space": (["Orbit Notes", "Star Lab", "Khan Academy Space"],
              ["black holes", "the James Webb telescope", "Mars", "Jupiter's moons", "rocket engines", "galaxies",
               "exoplanets", "the Big Bang", "neutron stars", "the solar system", "Saturn's rings", "astronauts"]),
    "math": (["Khan Academy", "Numberphile-ish", "Proof Garden"],
             ["calculus", "prime numbers", "linear algebra", "probability", "geometry proofs", "integrals",
              "derivatives", "statistics", "matrices", "trigonometry", "fractions", "number theory"]),
    "cooking": (["Knife & Fire", "Weeknight Kitchen", "Chef at Home"],
                ["fried rice", "ramen", "pasta sauce", "dumplings", "a cast iron steak", "curry", "soup",
                 "roast potatoes", "a weeknight dinner", "noodles", "burgers", "breakfast eggs"]),
    "woodworking": (["Sawdust Sunday", "Hand Tool Shop"],
                    ["dovetails", "a walnut table", "a workbench", "chisels", "a hand plane", "joinery",
                     "a bookshelf", "wood carving", "a lathe bowl", "oak furniture"]),
    "guitar": (["Six String Lab", "Fretboard Daily"],
               ["fingerstyle guitar", "guitar chords", "blues guitar", "guitar scales", "travis picking",
                "slide guitar", "the CAGED system", "strumming patterns"]),
    "history": (["History Hour", "Ancient Tales"],
                ["the Roman empire", "ancient Egypt", "medieval castles", "World War 2", "the French revolution",
                 "Greek civilization", "the Silk Road", "Viking history"]),
    "japan": (["Slow Rail Japan", "Tokyo Walks"],
              ["Kyoto temples", "a Tokyo train ride", "Hokkaido in winter", "Osaka street food", "Nara deer",
               "a Japan rail journey", "onsen towns"]),
    "homelab": (["Homelab Hour", "Server Shed"],
                ["a home server", "Linux basics", "Tailscale", "Docker", "Ubuntu server", "backups",
                 "a Raspberry Pi", "self-hosting"]),
    "gardening": (["Green Thumb Plot"],
                  ["tomatoes", "a raised bed", "compost", "seed starting", "pruning fruit trees", "herbs"]),
    "yoga": (["Morning Mat"],
             ["morning yoga", "a mobility routine", "pilates core", "a stretching routine", "breathing exercises"]),
    "physics": (["Physics Explained"],
                ["quantum mechanics", "relativity", "gravity", "thermodynamics", "particle physics", "light waves"]),
    "biology": (["Wild Planet"],
                ["deep ocean animals", "evolution", "insects", "bird migration", "the human brain", "fungi"]),
    "cars": (["Garage Days"], ["engine rebuilds", "car restoration", "racing", "electric cars"]),
    "art": (["Studio Sketch"], ["watercolor painting", "drawing faces", "illustration", "photography"]),
    "aviation": (["Flight Deck"], ["how planes fly", "airline pilots", "jet engines", "helicopters"]),
    "pets": (["Whisker Weekly"], ["cats", "kittens", "dog training", "puppies"]),
}
TEMPLATES = ["{x} explained", "The truth about {x}", "{x} for beginners", "Why {x} matter",
             "A deep dive into {x}", "Everything about {x}", "{x}: what nobody tells you", "My week with {x}"]


def vid(seed: str) -> str:
    a = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    return "".join(a[b % 64] for b in hashlib.sha256(seed.encode()).digest()[:11])


def build_catalog() -> list[dict]:
    out = []
    for topic, (channels, nouns) in TOPICS.items():
        for i, n in enumerate(nouns):
            for j, tpl in enumerate(TEMPLATES[:6]):
                ch = channels[(i + j) % len(channels)]
                title = tpl.format(x=n)
                title = title[0].upper() + title[1:]
                v = vid(f"{topic}/{title}")
                out.append({"id": v, "title": title, "channel": ch, "channel_id": "UC" + vid("c" + ch) + vid("d" + ch)[:11],
                            "duration": 600.0, "url": f"https://www.youtube.com/watch?v={v}", "thumb_url": "",
                            "is_short": False, "view_count": random.Random(v).randint(1_000, 2_000_000),
                            "description": f"{title}. New video from {ch}.", "upload_ts": time.time() - random.Random(v).randint(1, 3000) * 86400,
                            "_topic": topic})
    return out


CATALOG = build_catalog()
TOPIC_OF = {v["id"]: v["_topic"] for v in CATALOG}


class FakeYT:
    """Answers searches from the synthetic catalog by word overlap."""

    def __init__(self):
        self.requests = []

    def search(self, query, n=15, shorts=False):
        self.requests.append(query)
        if shorts:
            return []
        q = set(text.keywords(query, limit=20))
        scored = []
        for v in CATALOG:
            vk = set(text.keywords(v["title"], v["channel"], v["_topic"], limit=30))
            scored.append((len(q & vk), random.Random(query + v["id"]).random(), v))
        scored.sort(key=lambda t: (-t[0], t[1]))
        hits = [v for s, _, v in scored if s > 0]
        if len(hits) < n:  # YouTube always returns something
            hits += [v for s, _, v in scored if s == 0][: n - len(hits)]
        return [{k: x for k, x in v.items() if not k.startswith("_")} for v in hits[:n]]

    def channel_uploads(self, channel_id, n=15, channel=""):
        self.requests.append("channel:" + (channel or channel_id))
        return [{k: x for k, x in v.items() if not k.startswith("_")}
                for v in CATALOG if v["channel_id"] == channel_id][:n]

    def thumbnail(self, *a, **k):
        return None


def watch(pid: int, v: dict, days_ago: float = 1, like: bool = False) -> None:
    library.upsert_video({k: x for k, x in v.items() if not k.startswith("_")})
    library.update_progress(pid, v["id"], 600, 600, ended=True)
    db.x("UPDATE events SET ts=? WHERE profile_id=? AND video_id=?", (time.time() - days_ago * 86400, pid, v["id"]))
    db.x("UPDATE watch_state SET updated_at=? WHERE profile_id=? AND video_id=?",
         (time.time() - days_ago * 86400, pid, v["id"]))
    if like:
        library.rate(pid, v["id"], 1)


def fresh_db() -> None:
    d = Path(tempfile.mkdtemp(prefix="homelib-sim-"))
    db.init(d / "sim.db")
    library.ensure_profiles(["Me", "Partner"])
    embeddings.configure("builtin")
    recommender.REQUEST_DELAY = 0.0


def pick(topic: str, k: int, seed: int) -> list[dict]:
    vs = [v for v in CATALOG if v["_topic"] == topic]
    return random.Random(seed).sample(vs, k)


def history_varied(pid: int) -> None:
    """2 space videos yesterday + 20 videos across 10 other topics over two months."""
    for v in pick("space", 2, 1):
        watch(pid, v, days_ago=1)
    others = ["math", "woodworking", "guitar", "history", "japan", "homelab", "gardening", "physics",
              "biology", "art"]
    for i in range(20):
        t = others[i % len(others)]
        watch(pid, pick(t, 1, 100 + i)[0], days_ago=3 + i * 3, like=(i % 5 == 0))


def run(title: str, pid: int, exploration: float, verbose: bool) -> None:
    recommender.save_algo_settings(pid, {"exploration": exploration})
    yt = FakeYT()
    recommender.refresh(yt, [pid], [], [])
    feed = recommender.rank(pid, "me")
    print(f"\n=== {title} (exploration slider {exploration:.0%}) ===")
    rows = interests.rows(pid)
    print("Interests (weight, from videos):")
    for r in sorted(rows, key=lambda r: -interests.effective_weight(r)):
        print(f"  {r['label']:<28} {interests.effective_weight(r):5.2f}   {r['stats'].get('n', 0)} items")
    print(f"Searches this refresh ({len(yt.requests)} requests):")
    plan = Counter(q.split(":")[0] for q in yt.requests)
    print("  " + ", ".join(f"{k}: {n}" for k, n in plan.items()))
    if verbose:
        for q in yt.requests:
            print("   -", q)
    true = Counter(TOPIC_OF.get(c["id"], "?") for c in feed)
    slots = Counter(c.get("slot") for c in feed)
    print(f"Feed: {len(feed)} videos; slots: " + ", ".join(f"{k} {n}" for k, n in slots.items()))
    print("Feed mix by real topic:")
    for t, n in true.most_common():
        print(f"  {t:<12} {n:3d}  {'#' * n}")
    run_len = max_run = 1
    for a, b in zip(feed, feed[1:]):
        run_len = run_len + 1 if a.get("group") == b.get("group") else 1
        max_run = max(max_run, run_len)
    print(f"Longest run of one interest in a row: {max_run}")
    if verbose:
        for c in feed:
            print(f"  [{c.get('slot', ''):<8}] {TOPIC_OF.get(c['id'], '?'):<11} {c['score']:.3f}  {c['title']}  "
                  f"— {c['reasons'][0]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exploration", type=float, default=0.2)
    ap.add_argument("--verbose", "-v", action="store_true")
    a = ap.parse_args()

    fresh_db()
    history_varied(1)
    run("1. Two space videos + 20 varied videos", 1, a.exploration, a.verbose)

    fresh_db()
    for v in pick("space", 2, 1):
        watch(1, v, days_ago=1)
    run("2. Only two space videos", 1, a.exploration, a.verbose)

    fresh_db()
    history_varied(1)
    for v in pick("cooking", 2, 7):
        library.not_interested(1, {k: x for k, x in v.items() if not k.startswith("_")})
    recommender.save_algo_settings(1, {"starter_topics": ["home cooking", "woodworking"]})
    run("3. Like 1, plus 'Not interested' on two cooking videos (and 'home cooking' as a starter topic)",
        1, a.exploration, a.verbose)


if __name__ == "__main__":
    main()
