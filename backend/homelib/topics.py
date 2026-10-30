"""A small, hand-written map of common video topics.

Used for three things, none of which need a model or the internet:
* readable interest labels ("Space science" instead of "black · hole"),
* exploration: topics *next to* an interest (space -> physics, engineering),
* the built-in embedder (embeddings.HashEmbedder): words of one topic share
  a feature, so "black hole" and "Mars atmosphere" end up close together.

It is deliberately coarse. With the MiniLM model installed the embeddings do
the real work and this only names things.
"""
from __future__ import annotations

import re
from functools import lru_cache

# key: (label, words, adjacent topics, exploration searches)
TOPICS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "space": ("Space science",
              "space astronomy astronomer astrophysics nasa esa spacex galaxy galaxies planet planets planetary "
              "mars jupiter saturn venus mercury pluto europa titan moon lunar universe cosmos cosmic cosmology "
              "telescope webb hubble orbit orbital rocket rockets launch astronaut astronauts nebula supernova "
              "star stars stellar solar comet asteroid exoplanet spacecraft satellite astrophotography",
              "physics engineering math earth aviation", ("how big is the universe", "space documentary",
                                                         "life on other planets", "how rockets work")),
    "physics": ("Physics",
                "physics physicist quantum relativity einstein particle particles atom atoms atomic electron "
                "photon gravity energy entropy thermodynamics magnetism electromagnetism nuclear fusion fission "
                "mechanics wave waves light speed",
                "space math engineering chemistry", ("physics explained simply", "quantum mechanics for beginners")),
    "math": ("Math lessons",
             "math maths mathematics mathematician algebra calculus geometry trigonometry equation equations "
             "theorem proof proofs integral integrals derivative derivatives probability statistics arithmetic "
             "fraction fractions number numbers prime primes topology matrix matrices linear vector vectors "
             "exam",
             "physics computing science_ed puzzles", ("beautiful math proofs", "math explained visually")),
    "science_ed": ("Science lessons",
                   "lecture lectures academy classroom teacher education educational school homework",
                   "math physics history biology", ("crash course science", "how science works")),
    "biology": ("Biology & nature",
                "biology cell cells dna gene genes genetics evolution species animal animals wildlife nature "
                "ecosystem ocean marine insects insect birds bird mammals fungi plants virus bacteria brain "
                "neuroscience human body",
                "science_ed earth health", ("nature documentary", "amazing animal behaviour")),
    "earth": ("Earth & climate",
              "earth geology volcano volcanoes earthquake earthquakes climate weather storm storms hurricane "
              "tornado glacier glaciers rocks minerals ocean oceans river rivers",
              "biology space travel", ("how volcanoes work", "weather explained")),
    "chemistry": ("Chemistry",
                  "chemistry chemical chemicals reaction reactions element elements periodic molecule molecules "
                  "acid acids lab experiment experiments",
                  "physics science_ed", ("cool chemistry experiments",)),
    "engineering": ("Engineering",
                    "engineering engineer engineers machine machines engine engines mechanical bridge bridges "
                    "factory manufacturing design robot robots robotics electronics circuit circuits "
                    "invention inventions",
                    "physics space diy cars computing", ("how it's made", "engineering explained")),
    "computing": ("Computers & tech",
                  "computer computers programming programmer code coding python javascript linux software "
                  "hardware tech technology ai algorithm algorithms server servers homelab network networking "
                  "tailscale docker ubuntu raspberry backup backups selfhosting self-hosting cpu gpu",
                  "engineering math gaming", ("computer science explained", "homelab tour")),
    "history": ("History",
                "history historical ancient medieval empire empires roman romans rome greek greece egypt "
                "egyptian war wars battle battles ww2 wwii ww1 revolution century civilization civilisation "
                "archaeology dynasty king kings queen castle castles",
                "geography documentary science_ed", ("history documentary", "ancient civilizations explained")),
    "geography": ("Geography & places",
                  "geography country countries map maps border borders city cities island islands continent",
                  "travel history", ("geography explained", "strange borders")),
    "documentary": ("Documentaries",
                    "documentary documentaries investigation story stories mystery mysteries true",
                    "history biology", ("full documentary",)),
    "cooking": ("Cooking",
                "cooking cook cooks recipe recipes kitchen chef chefs food foods dinner lunch breakfast meal "
                "meals pasta sauce sauces ramen dumplings dumpling burger burgers fried rice potatoes potato "
                "knife knives cast iron pan grill grilling bbq soup curry noodles steak chicken egg eggs "
                "vegetables vegan",
                "baking travel gardening", ("easy weeknight dinner", "street food")),
    "baking": ("Baking",
               "baking bake baker bread breads sourdough dough focaccia bagel bagels pizza cake cakes cookie "
               "cookies pastry pastries croissant croissants cinnamon rolls flour yeast oven crumb",
               "cooking", ("bread baking for beginners",)),
    "woodworking": ("Woodworking",
                    "woodworking woodworker wood wooden walnut oak timber joinery dovetail dovetails chisel bookshelf "
                    "chisels plane planes saw saws workbench furniture table shelf carving lathe sawdust",
                    "diy engineering", ("woodworking project build", "hand tool woodworking")),
    "diy": ("DIY & making",
            "diy repair repairs restore restoration restoring fix fixing build building workshop tools tool "
            "renovation home improvement maker 3d printing printer",
            "woodworking engineering cars", ("restoration video", "satisfying repair")),
    "gardening": ("Gardening",
                  "garden gardens gardening plant plants planting seed seeds seedling seedlings tomato tomatoes "
                  "compost composting soil raised bed beds prune pruning fruit trees herb herbs harvest",
                  "cooking biology diy", ("garden tour", "growing vegetables")),
    "travel": ("Travel",
               "travel traveling travelling trip trips tour tours journey vacation holiday train trains rail "
               "railway hotel hostel backpacking japan tokyo kyoto osaka hokkaido nara europe italy france "
               "paris london iceland walking",
               "geography cooking history", ("slow travel documentary", "train journey")),
    "music_lessons": ("Music lessons",
                      "guitar guitars chord chords fingerstyle riff riffs piano keyboard drums drum bass ukulele "
                      "scales strum strumming picking theory lesson",
                      "music", ("learn an instrument", "music theory explained")),
    "music": ("Music",
              "music song songs album albums band bands concert live performance cover covers singer musician "
              "jazz blues rock classical orchestra lofi beats",
              "music_lessons", ("live session", "album review")),
    "fitness": ("Fitness & yoga",
                "fitness workout workouts exercise exercises yoga pilates stretch stretching mobility core "
                "strength training gym running run cardio posture",
                "health", ("beginner workout", "morning stretch")),
    "health": ("Health",
               "health healthy sleep nutrition diet medicine medical doctor mental anxiety stress breathing "
               "meditation",
               "fitness biology", ("how sleep works",)),
    "pets": ("Pets & animals",
             "cat cats kitten kittens dog dogs puppy puppies pet pets whiskers",
             "biology", ("funny animals",)),
    "gaming": ("Gaming",
               "game games gaming gamer minecraft zelda nintendo playstation xbox speedrun speedrunning "
               "walkthrough playthrough",
               "computing", ("game design explained",)),
    "cars": ("Cars & vehicles",
             "car cars engine engines motor motorcycle truck trucks vehicle vehicles driving drive racing",
             "engineering diy", ("car restoration",)),
    "aviation": ("Aviation",
                 "plane planes airplane airplanes aircraft airline airlines pilot pilots flight flights flying "
                 "airport jet jets helicopter",
                 "engineering space travel", ("how planes fly",)),
    "finance": ("Money & economics",
                "money finance finances economy economics invest investing investment stock stocks market "
                "markets budget budgeting tax taxes",
                "history", ("economics explained",)),
    "art": ("Art & design",
            "art artist artists drawing draw painting paint paintings sketch sketching design illustration "
            "photography photo photos camera",
            "diy", ("art process video",)),
    "comedy": ("Comedy",
               "comedy funny standup stand-up sketch jokes prank laugh",
               "music", ("stand-up comedy",)),
    "language": ("Languages",
                 "language languages english spanish french german japanese grammar vocabulary pronunciation "
                 "linguistics",
                 "travel", ("learn a language",)),
    "film": ("Film & TV",
             "movie movies film films cinema trailer review reviews director scene tv series episode",
             "music art", ("film analysis",)),
    "sports": ("Sports",
               "sport sports football soccer basketball baseball tennis golf cricket rugby hockey match "
               "highlights olympics",
               "fitness", ("sports documentary",)),
}

# Exploration searches for topics outside everything a profile likes.
OUTSIDE_SEEDS = ("how it's made", "nature documentary", "history documentary", "science explained",
                 "engineering explained", "slow travel documentary", "art process video", "music theory explained")

_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)


def stem(w: str) -> str:
    """Very light English stemming (plural s/ies); enough to match lexicon words."""
    w = w.lower().strip("'")
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def words(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall((text or "").lower())]


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, set[str]], dict[tuple[str, str], set[str]]]:
    """word -> topics, and (word, word) -> topics for two-word phrases."""
    uni: dict[str, set[str]] = {}
    bi: dict[tuple[str, str], set[str]] = {}
    for key, (_, ws, _, _) in TOPICS.items():
        for w in ws.split():
            parts = [stem(p) for p in w.replace("_", " ").replace("-", " ").split()]
            if len(parts) == 1:
                uni.setdefault(parts[0], set()).add(key)
            else:
                bi.setdefault((parts[0], parts[1]), set()).add(key)
    # common two-word phrases
    for a, b, key in (("black", "hole", "space"), ("cast", "iron", "cooking"), ("home", "server", "computing"),
                      ("hand", "tool", "woodworking"), ("raised", "bed", "gardening")):
        bi.setdefault((a, b), set()).add(key)
    return uni, bi


def topics_in(text: str) -> dict[str, int]:
    """{topic key: number of matching words} for a piece of text."""
    uni, bi = _index()
    ws = words(text)
    out: dict[str, int] = {}
    for w in ws:
        for k in uni.get(w, ()):
            out[k] = out.get(k, 0) + 1
    for a, b in zip(ws, ws[1:]):
        for k in bi.get((a, b), ()):
            out[k] = out.get(k, 0) + 2
    return out


def dominant(weighted_texts: list[tuple[str, float]], min_share: float = 0.45) -> str | None:
    """The topic most of the (text, weight) pairs are about, if one stands out."""
    total = sum(max(0.0, w) for _, w in weighted_texts) or 1.0
    score: dict[str, float] = {}
    for t, w in weighted_texts:
        found = topics_in(t)
        if not found:
            continue
        best = max(found.values())
        for k, n in found.items():
            if n == best:
                score[k] = score.get(k, 0.0) + max(0.0, w)
    if not score:
        return None
    k, s = max(score.items(), key=lambda kv: (kv[1], kv[0]))
    return k if s / total >= min_share else None


def label(key: str) -> str:
    return TOPICS[key][0] if key in TOPICS else key


def adjacent(key: str) -> list[str]:
    return TOPICS[key][2].split() if key in TOPICS else []


def seeds(key: str) -> tuple[str, ...]:
    return TOPICS[key][3] if key in TOPICS else ()
