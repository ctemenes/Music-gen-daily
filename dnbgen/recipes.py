"""Turn a theme + date into a full mix plan: theme choice, track count, track recipes and names."""
import datetime as dt
import numpy as np
from .themes import (THEMES, THEMES_BY_ID, GENERIC_WORDS, NAME_SUFFIXES, NAME_PREFIXES,
                     BLOCKED_NAMES, PROGRESSIONS)

# "mix it up": the length cycles through short / medium / long, nudged by the date
LENGTH_PLAN = [10, 16, 26, 12, 20, 26, 14]   # tracks per mix by weekday (Mon..Sun); ~2.2 min per track


def pick(rng, weights, flatten=0.4):
    """weighted choice; flatten > 0 pulls the odds toward even so long mixes don't get samey"""
    keys = list(weights)
    p = np.array([weights[k] for k in keys], float)
    p = p + flatten * p.mean()
    return keys[rng.choice(len(keys), p=p / p.sum())]


def choose_theme(rng, history, override=None):
    if override:
        return THEMES_BY_ID[override]
    recent = [d["theme"] for d in history.get("mixes", [])[-6:]]
    pool = [t for t in THEMES if t["id"] not in recent] or THEMES
    return pool[rng.integers(len(pool))]


def make_name(rng, theme, used, bases_in_mix):
    """Unique across all past mixes, and no two tracks in one mix share a base word."""
    words = theme["words"]
    for attempt in range(400):
        r = rng.random()
        if r < 0.85:
            base = words[rng.integers(len(words))]
        else:
            base = GENERIC_WORDS[rng.integers(len(GENERIC_WORDS))]
        if base in bases_in_mix and attempt < 300:
            continue
        r2 = rng.random()
        if r2 < 0.5:
            name = base
        elif r2 < 0.8:
            name = f"{base} {NAME_SUFFIXES[rng.integers(len(NAME_SUFFIXES))]}"
        elif " " not in base:
            name = f"{NAME_PREFIXES[rng.integers(len(NAME_PREFIXES))]} {base}"
        else:
            name = base
        key = name.lower()
        if key not in used and key not in BLOCKED_NAMES:
            used.add(key)
            bases_in_mix.add(base)
            return name
    # every combination used: fall back to a numbered sequel
    for k in range(2, 50):
        name = f"{words[rng.integers(len(words))]} {['II','III','IV','V','VI','VII','VIII','IX'][min(k - 2, 7)]}"
        if name.lower() not in used:
            used.add(name.lower())
            return name
    raise RuntimeError("ran out of unique names")


def make_recipe(rng, theme, name, seed, prev=None, energy=0.5):
    scale = pick(rng, theme["scales"])
    lo, hi = theme["bpm"]
    bpm = int(rng.integers(lo, hi + 1))
    drums = pick(rng, theme["drums"])
    bass = pick(rng, theme["bass"])
    # avoid two very similar tracks back to back
    for _ in range(6):
        if prev is None or (drums, bass) != (prev["drums"], prev["bass"]):
            break
        drums, bass = pick(rng, theme["drums"]), pick(rng, theme["bass"])
    pad = pick(rng, theme["pad"])
    lead = pick(rng, theme["lead"])
    pats = {"reese": ["two", "roll", "long", "tech"], "sub": ["long", "two", "roll"],
            "growl": ["tech", "two", "roll", "stab"], "stab": ["stab", "roll", "tech"]}[bass]
    bp1 = pats[rng.integers(len(pats))]
    bp2 = pats[rng.integers(len(pats))]
    hard = drums in ("techstep",) or bass == "growl"
    soft = drums == "rolling" and bass == "sub"
    r = dict(
        name=name, seed=int(seed), bpm=bpm, key=int(rng.integers(45, 56)), scale=scale,
        prog=[int(x) for x in PROGRESSIONS[scale][rng.integers(len(PROGRESSIONS[scale]))]],
        sevenths=bool(rng.random() < theme.get("sevenths", 0.25) or pad == "rhodes"),
        drums=drums, bass=bass, bass_pat=bp1, bass_pat2=bp2, pad=pad, lead=lead,
        kick="hard" if hard else ("deep" if soft else ["punch", "deep", "hard"][rng.integers(3)]),
        snare="hard" if hard else ("soft" if soft else ["crisp", "crisp", "hard", "soft"][rng.integers(4)]),
        chop=bool(drums in ("jungle", "amen") and rng.random() < .7),
        break_drums=["none", "hats", "half"][rng.integers(3)] if not soft else "hats",
        break_bass=bool(rng.random() < .4),
        intro_hats=bool(rng.random() < .5),
        atmos=bool(rng.random() < theme.get("atmos", .3)),
        swing=float(round(.004 if drums == "techstep" else rng.uniform(.008, .017), 4)),
        fm_ratio=float([1.5, 2.0, 3.0][rng.integers(3)]),
        wob_beats=int([1, 2, 4, 8][rng.integers(4)]),
        oct_prob=float(round(rng.uniform(.1, .3), 2)),
        room=float(round(rng.uniform(.3, .55) if hard else rng.uniform(.4, .6), 2)),
    )
    if pad == "rhodes":
        r["gains"] = dict(pad=.55, lead=.3 if lead != "arp" else .26)
    if lead == "stab":
        r.setdefault("gains", {})["lead"] = .32
    return r


def energy_order(recipes):
    """Arrange tracks into an arc: open warm, build, peak late, land smooth."""
    def score(r):
        s = {"rolling": 0, "twostep": 1, "amen": 2, "jungle": 3, "techstep": 3}[r["drums"]]
        s += {"sub": 0, "reese": 1, "stab": 2, "growl": 2}[r["bass"]] + (r["bpm"] - 170) / 4
        return s
    ranked = sorted(recipes, key=score)
    n = len(ranked)
    lows, highs = ranked[: n // 2], ranked[n // 2:]
    order = []
    # warm opener, then alternate rising energy, end on the hottest track then a calmer closer
    opener = lows.pop(len(lows) // 2) if lows else highs.pop(0)
    order.append(opener)
    closer = lows.pop(0) if lows else None
    while lows or highs:
        if lows:
            order.append(lows.pop(0))
        if highs:
            order.append(highs.pop(0))
    if closer:
        order.append(closer)
    return order


def declump(order):
    """No more than two tracks in a row with the same drum style, if it can be avoided."""
    order = list(order)
    for _ in range(3):
        for i in range(2, len(order)):
            if order[i]["drums"] == order[i - 1]["drums"] == order[i - 2]["drums"]:
                for j in range(i + 1, len(order)):
                    if order[j]["drums"] != order[i]["drums"]:
                        order[i], order[j] = order[j], order[i]
                        break
    return order


def plan_mix(date, history, theme_override=None, n_tracks=None, variant=0):
    seed = int(date.strftime("%Y%m%d")) * 10 + int(variant)
    rng = np.random.default_rng(seed)
    theme = choose_theme(rng, history, theme_override)
    n = n_tracks or LENGTH_PLAN[date.weekday()]
    used = set(x.lower() for x in history.get("used_names", []))
    recipes, prev, bases = [], None, set()
    for k in range(n):
        name = make_name(rng, theme, used, bases)
        r = make_recipe(rng, theme, name, (seed * 100 + k) % (2 ** 32), prev)
        recipes.append(r)
        prev = r
    recipes = declump(energy_order(recipes))
    return dict(date=date.isoformat(), seed=seed, theme=theme["id"], tracks=recipes)
