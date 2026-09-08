"""Generate the NLP training corpus for the shelter design assistant.

Why synthesised rather than scraped: there is no public corpus of "describe
the shelter you want" utterances, and the label space here is entirely
project-specific (our 15 sites, our 15 materials, our design schema). So the
corpus is built from templates crossed with a paraphrase vocabulary, which
gives full label accuracy and lets us cover phrasings a scraped set never
would (Hinglish-flavoured word order, imperative vs polite, typos).

The output feeds ml/nlp/train_nlp.py, which trains an intent classifier and
exports it to compact JSON for pure-NumPy inference (same pattern as
src/ai_model.py, so the serverless bundle needs no sklearn at runtime).

Run:  python3 ml/nlp/generate_nlp_dataset.py [--n 24000]
Out:  ml/nlp/data/nlp_dataset.csv   (text,intent)
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

OUT_DIR = Path(__file__).resolve().parent / "data"

# --------------------------------------------------------------------------
# vocabulary — deliberately overlapping so the classifier cannot separate
# intents on a single give-away keyword
# --------------------------------------------------------------------------
SITES = ["Prayagraj", "Delhi", "Jaipur", "Jaisalmer", "Ahmedabad", "Chennai",
         "Mumbai", "Kolkata", "Bengaluru", "Hyderabad", "Pune", "Leh",
         "Srinagar", "Kargil", "Dras", "Allahabad", "Bangalore", "Bombay",
         "Calcutta", "Madras", "Ladakh", "Kashmir"]

WALL_WORDS = ["brick", "mud brick", "adobe", "rammed earth", "stone",
              "concrete", "timber", "wood", "plywood", "AAC block",
              "aerated concrete", "PUF panel", "sandwich panel", "GI sheet",
              "tin sheet", "metal sheet"]
INS_WORDS = ["EPS", "thermocol", "XPS", "mineral wool", "rockwool",
             "sheep wool", "glass wool", "insulation", "no insulation"]
ROOF_WORDS = ["RCC slab", "concrete roof", "GI sheet roof", "tin roof",
              "timber roof", "PUF panel roof", "sandwich panel roof"]

GOALS = ["cool", "cooler", "cold", "warm", "warmer", "comfortable", "liveable",
         "cheap", "low cost", "affordable", "quick to build", "rapid deploy",
         "energy efficient", "passive", "flood safe", "cyclone resistant"]

USES = ["family", "4 people", "six people", "a clinic", "a classroom",
         "relief camp", "disaster relief", "flood victims", "earthquake relief",
         "refugees", "workers", "a storeroom", "emergency housing"]

POLITE = ["", "please ", "can you ", "could you ", "i want you to ",
          "i need you to ", "kindly ", "hey ", "ok so ", "hi, "]

DESIGN_VERBS = ["design", "build", "make", "create", "generate", "give me",
                "i need", "i want", "set up", "plan", "propose", "draft",
                "configure", "suggest a design for", "come up with"]

MODIFY_VERBS = ["make", "change", "increase", "decrease", "reduce", "raise",
                "lower", "swap", "replace", "set", "adjust", "add", "remove",
                "use", "switch to", "bump up", "thicken", "thin out"]

OPTIMIZE_WORDS = ["optimize", "optimise", "find the best", "best possible",
                  "tune", "search for the optimal", "what is the optimal",
                  "give me the best", "maximise comfort", "minimise heat",
                  "run an optimisation", "auto tune"]

EXPLAIN_WORDS = ["how hot", "how cold", "what is the temperature",
                 "what's the climate", "tell me about the weather",
                 "how humid", "how much rain", "what is the peak temperature",
                 "is it hot", "explain the climate", "what zone is",
                 "how many hours above 35", "show me the trend"]

COMPARE_WORDS = ["compare", "which is better", "difference between",
                 "versus", "vs", "how does it compare", "benchmark"]

DIMS = ["3x3", "4 by 4", "3 by 4 metres", "6x4 m", "5 by 5", "small",
        "large", "big", "compact", "12 square metres", "20 sqm"]

ORIENT = ["facing north", "facing south", "north facing", "south facing",
          "east facing", "west facing", "windows on the north",
          "window on the south side", "rotated 45 degrees"]


def _p(rng, seq):
    return rng.choice(seq)


def _maybe(rng, s, prob=0.5):
    return s if rng.random() < prob else ""


# ---------------------------------------------------------------- paraphrase
# The first corpus was one rigid template per intent, which scored 0.9998 on
# its own split and 0.705 on hand-written phrasings — it had learned the
# templates, not the intents. These families cover the CONSTRUCTIONS people
# actually use: indirect needs, comparatives, "X instead of Y", yes/no
# questions. Vocabulary is kept distinct from ml/nlp/eval_nlp.py so the
# held-out score stays honest.

NEED_OPENERS = ["we need", "i need", "we want", "there is a need for",
                "looking for", "we're after", "requirement is",
                "we have to put up", "we must provide", "need"]
PLACES_FOR = ["somewhere", "a place", "accommodation", "housing", "space",
              "quarters", "lodging"]
INFORMAL_BUILD = ["throw up", "put up", "knock together", "get up",
                  "stand up", "roll out", "deploy"]
WHY = ["after the floods", "after the earthquake", "for the monsoon",
       "before the heatwave", "for the winter", "post cyclone",
       "for the migrant workers", "during the cold spell",
       "for the displaced families", "for the rescue teams"]
CLIMATE_FACTS = ["it gets to minus twenty there", "it touches 48 in summer",
                 "humidity is brutal", "it snows for months",
                 "the nights are freezing", "it rains non stop",
                 "there is no shade", "the wind never stops"]

TOO_ADJ = ["too thin", "too thick", "too small", "too big", "too heavy",
           "too light", "too dark", "too draughty", "not thick enough",
           "not big enough", "too weak"]
FIX_VERBS = ["double it", "halve it", "fix it", "sort it out", "beef it up",
             "trim it down", "bump it", "cut it back"]
MORE_LESS = ["less", "more", "fewer", "a bit more", "a bit less"]
FEATURES = ["glass", "window area", "insulation", "mass", "openings",
            "shading", "ventilation", "thickness"]
SIDES = ["west side", "east side", "north wall", "south wall", "roof",
         "front", "back"]

SUPERLATIVE = ["as {g} as possible", "the most {g} option", "maximum {g}",
               "get the most out of it", "squeeze every bit of {g} out of it",
               "push {g} to the limit", "wring out more {g}"]
PAIRS = [("wall", "roof"), ("insulation", "glazing"), ("material", "orientation"),
         ("envelope", "window"), ("thickness", "material")]

YESNO = ["is", "does", "would", "will", "can"]
CLIMATE_ADJ = ["mild", "bearable", "brutal", "humid", "dry", "freezing",
               "pleasant", "extreme", "temperate", "harsh"]
COMPARATIVE = ["harsher", "hotter", "colder", "wetter", "drier", "milder",
               "tougher", "worse", "better", "more extreme"]
STACK = ["stack up against", "measure up to", "hold up against",
         "do compared to", "fare against", "compare with"]
DATA_ASKS = ["can i get the raw csv", "where do i download the data",
             "give me the json", "is there an api", "export the dataset",
             "can i see the code", "send me the spreadsheet",
             "how do i cite this", "what license is this"]
LIFE = ["what's for lunch", "my laptop is slow", "the wifi is down",
        "i'm tired", "is it friday yet", "my phone died",
        "traffic was terrible", "i need coffee", "what's the score"]


def gen_design(rng) -> str:
    bits = [_p(rng, POLITE), _p(rng, DESIGN_VERBS), " "]
    bits.append(_maybe(rng, "a ", 0.7))
    bits.append(_maybe(rng, _p(rng, GOALS) + " ", 0.8))
    bits.append(_p(rng, ["shelter", "house", "unit", "cabin", "room",
                         "structure", "hut", "dwelling"]))
    if rng.random() < 0.6:
        bits.append(" for " + _p(rng, SITES))
    if rng.random() < 0.4:
        bits.append(" for " + _p(rng, USES))
    if rng.random() < 0.45:
        bits.append(" with " + _p(rng, WALL_WORDS) + " walls")
    if rng.random() < 0.3:
        bits.append(" and " + _p(rng, INS_WORDS))
    if rng.random() < 0.25:
        bits.append(", " + _p(rng, DIMS))
    if rng.random() < 0.2:
        bits.append(", " + _p(rng, ORIENT))
    return "".join(bits)


def gen_design_indirect(rng) -> str:
    """Needs-based phrasing that never names a build verb."""
    r = rng.random()
    if r < 0.34:
        s = (f"{_p(rng, NEED_OPENERS)} {_p(rng, PLACES_FOR)} for "
             f"{_p(rng, USES)}")
        if rng.random() < 0.7:
            s += " in " + _p(rng, SITES)
        if rng.random() < 0.4:
            s += " " + _p(rng, WHY)
        return s
    if r < 0.67:
        s = (f"something {_p(rng, GOALS)} we can {_p(rng, INFORMAL_BUILD)} "
             f"{_p(rng, WHY)}")
        if rng.random() < 0.6:
            s += " in " + _p(rng, SITES)
        return s
    s = f"shelter for {_p(rng, SITES)}"
    if rng.random() < 0.8:
        s += ", " + _p(rng, CLIMATE_FACTS)
    return s


def gen_modify(rng) -> str:
    target = _p(rng, ["the walls", "the roof", "the insulation", "the windows",
                      "the window", "the height", "the orientation",
                      "wall thickness", "roof thickness", "the ceiling"])
    verb = _p(rng, MODIFY_VERBS)
    tail = _p(rng, [
        " thicker", " thinner", " bigger", " smaller", " to 300 mm",
        " to 200mm", " by 50 mm", " to " + _p(rng, WALL_WORDS),
        " to " + _p(rng, INS_WORDS), " " + _p(rng, ORIENT),
        " more insulated", " lighter", " heavier",
    ])
    return f"{_p(rng, POLITE)}{verb} {target}{tail}"


def gen_modify_indirect(rng) -> str:
    r = rng.random()
    if r < 0.34:
        return (f"{_p(rng, ['the ', 'that ', ''])}"
                f"{_p(rng, ['wall', 'roof', 'window', 'slab', 'ceiling'])} is "
                f"{_p(rng, TOO_ADJ)}, {_p(rng, FIX_VERBS)}")
    if r < 0.67:
        return f"{_p(rng, MORE_LESS)} {_p(rng, FEATURES)} on the {_p(rng, SIDES)}"
    if rng.random() < 0.5:
        a, b = rng.sample(INS_WORDS, 2)
    else:
        a, b = rng.sample(WALL_WORDS, 2)
    if rng.random() < 0.5:
        return f"use {a} instead of {b}"
    return (f"make it {_p(rng, ['one', 'two', 'half a', '0.5'])} "
            f"{_p(rng, ['metre', 'meter', 'm'])} "
            f"{_p(rng, ['wider', 'longer', 'taller', 'shorter', 'narrower'])}")


def gen_optimize_indirect(rng) -> str:
    if rng.random() < 0.5:
        return _p(rng, SUPERLATIVE).format(g=_p(rng, GOALS))
    a, b = _p(rng, PAIRS)
    return (f"{_p(rng, ['find', 'work out', 'figure out', 'identify'])} "
            f"{_p(rng, ['me ', 'us ', ''])}the "
            f"{_p(rng, ['optimal', 'best', 'ideal'])} {a} and {b} "
            f"{_p(rng, ['combination', 'pairing', 'mix', 'setup'])}")


def gen_explain_yesno(rng) -> str:
    return (f"{_p(rng, YESNO)} {_p(rng, SITES)} "
            f"{_p(rng, ['actually ', 'really ', ''])}"
            f"{_p(rng, CLIMATE_ADJ)}"
            f"{_p(rng, [' all year', ' in summer', ' in winter', ''])}"
            f"{_maybe(rng, '?', 0.5)}")


def gen_compare_indirect(rng) -> str:
    a, b = rng.sample(SITES, 2)
    r = rng.random()
    if r < 0.4:
        return f"is {a} {_p(rng, COMPARATIVE)} than {b}{_maybe(rng, '?', 0.4)}"
    if r < 0.7:
        return f"how does {a} {_p(rng, STACK)} {b}{_maybe(rng, '?', 0.4)}"
    m1, m2 = rng.sample(WALL_WORDS, 2)
    return (f"{m1} or {m2}, which "
            f"{_p(rng, ['performs better', 'is better', 'wins', 'holds up'])}"
            f"{_maybe(rng, '?', 0.4)}")


def gen_unknown_extra(rng) -> str:
    return _p(rng, [_p(rng, DATA_ASKS), _p(rng, LIFE)])


def gen_optimize(rng) -> str:
    s = f"{_p(rng, POLITE)}{_p(rng, OPTIMIZE_WORDS)} "
    s += _p(rng, ["design", "shelter", "configuration", "envelope", "setup"])
    if rng.random() < 0.6:
        s += " for " + _p(rng, SITES)
    if rng.random() < 0.3:
        s += " to stay " + _p(rng, GOALS)
    return s


def gen_explain(rng) -> str:
    s = f"{_p(rng, POLITE)}{_p(rng, EXPLAIN_WORDS)}"
    if rng.random() < 0.75:
        s += " in " + _p(rng, SITES)
    return s + _maybe(rng, "?", 0.5)


def gen_compare(rng) -> str:
    a, b = rng.sample(SITES, 2)
    if rng.random() < 0.5:
        return f"{_p(rng, POLITE)}{_p(rng, COMPARE_WORDS)} {a} and {b}"
    m1, m2 = rng.sample(WALL_WORDS, 2)
    return f"{_p(rng, POLITE)}{_p(rng, COMPARE_WORDS)} {m1} vs {m2}"


# Out-of-scope input must be REJECTED, not force-fitted onto a design. A fixed
# phrase list dedupes down to a few hundred rows and leaves the class starved,
# so compose these too.
CHITCHAT = ["hello", "hi there", "hey", "good morning", "thanks", "thank you",
            "ok", "nice", "cool", "great", "bye", "see you", "yo", "sup"]
META_Q = ["what can you do", "who made this", "what is this website",
          "how do i log in", "is this free", "who are you", "help",
          "show me the source code", "how does this work", "what is SIH",
          "where is the documentation", "can i export this",
          "how accurate is the model", "what data do you use"]
OFFTOPIC = ["book me a flight to goa", "what's the weather on mars",
            "tell me a joke", "what time is it", "play some music",
            "order a pizza", "who won the match", "translate this to hindi",
            "write me an essay", "solve 2x + 5 = 11", "call my mother",
            "what is the capital of france", "recommend a movie",
            "how do i cook rice", "buy bitcoin"]
UI_CMDS = ["reset everything", "undo that", "go back", "export the report",
           "download the model", "clear the form", "log me out",
           "start over", "cancel", "stop", "refresh the page"]
GIBBERISH = ["asdf", "test", "??", "...", "aaa", "qwerty", "123", "xyz",
             "hmm", "idk", "wat", "lol", "?!", "..", "zzz"]


def gen_unknown(rng) -> str:
    pool = _p(rng, [CHITCHAT, META_Q, OFFTOPIC, UI_CMDS, GIBBERISH])
    base = _p(rng, pool)
    # compose so the class has real variety instead of a few hundred dupes
    if rng.random() < 0.35:
        base = f"{_p(rng, POLITE)}{base}"
    if rng.random() < 0.25:
        base = f"{base} {_p(rng, ['please', 'thanks', 'now', 'quickly', 'again'])}"
    if rng.random() < 0.15:
        base = f"{base} {_p(rng, CHITCHAT)}"
    return base.strip()


def _mix(*fns):
    """Pick among several phrasing families for the same intent."""
    def pick(rng):
        return _p(rng, fns)(rng)
    return pick


HAZARDS = ["cyclone", "storm surge", "flooding", "the floods", "monsoon rain",
           "heavy snow load", "snow", "an earthquake", "high winds",
           "waterlogging", "a blizzard"]
VENT_PHRASES = ["cross ventilation", "good ventilation", "well ventilated",
                "night purge", "plenty of ventilation", "sealed and airtight"]
BARE_NOUNS = ["hut", "shed", "unit", "shelter", "cabin", "room", "house",
              "block", "dwelling", "structure"]


def gen_design_bare(rng) -> str:
    """Verb-less noun phrases and hazard-led asks.

    Real users write "bamboo hut in Kolkata" or "needs to handle heavy snow
    load in Dras" — no build verb, no need-opener. Every design template had
    one or the other, so these landed in `explain` at ~0.4 confidence and the
    request was refused instead of designed.
    """
    r = rng.random()
    if r < 0.34:                                   # "<material> <noun> in <site>"
        s = f"{_p(rng, WALL_WORDS)} {_p(rng, BARE_NOUNS)}"
        if rng.random() < 0.8:
            s += f" {_p(rng, ['in', 'for', 'at'])} {_p(rng, SITES)}"
        if rng.random() < 0.3:
            s += f", {_p(rng, DIMS)}"
        return s
    if r < 0.58:                                   # "<dims> <noun> in <site>"
        s = f"{_p(rng, DIMS)} {_p(rng, BARE_NOUNS)} {_p(rng, ['in', 'for'])} {_p(rng, SITES)}"
        if rng.random() < 0.4:
            s += f" with {_p(rng, WALL_WORDS)} walls"
        return s
    if r < 0.82:                                   # hazard-led
        opener = _p(rng, ["needs to handle", "has to survive", "must withstand",
                          "something that can take", "it has to cope with",
                          "must be safe in"])
        s = f"{opener} {_p(rng, HAZARDS)}"
        if rng.random() < 0.75:
            s += f" {_p(rng, ['in', 'at', 'for'])} {_p(rng, SITES)}"
        return s
    s = f"{_p(rng, BARE_NOUNS)} with {_p(rng, VENT_PHRASES)}"   # ventilation-led
    if rng.random() < 0.8:
        s += f" {_p(rng, ['in', 'for'])} {_p(rng, SITES)}"
    return s


GENERATORS = {
    "design": _mix(gen_design, gen_design, gen_design_indirect,
                   gen_design_bare, gen_design_bare),
    "modify": _mix(gen_modify, gen_modify_indirect, gen_modify_indirect),
    "optimize": _mix(gen_optimize, gen_optimize_indirect),
    "explain": _mix(gen_explain, gen_explain_yesno),
    "compare": _mix(gen_compare, gen_compare_indirect, gen_compare_indirect),
    "unknown": _mix(gen_unknown, gen_unknown_extra),
}
# design is the workhorse intent, so it gets the most coverage
WEIGHTS = {"design": 0.32, "modify": 0.19, "optimize": 0.14,
           "explain": 0.14, "compare": 0.08, "unknown": 0.13}


def noisy(rng, text: str) -> str:
    """Typos, casing and punctuation drift — real users are not tidy."""
    if rng.random() < 0.30:
        text = text.lower()
    elif rng.random() < 0.05:
        text = text.upper()
    if rng.random() < 0.12 and len(text) > 12:      # drop a character
        i = rng.randrange(len(text))
        text = text[:i] + text[i + 1:]
    if rng.random() < 0.10 and len(text) > 12:      # swap two characters
        i = rng.randrange(len(text) - 1)
        text = text[:i] + text[i + 1] + text[i] + text[i + 2:]
    if rng.random() < 0.10:
        text = text.replace(" ", "  ", 1)
    return text.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24000)
    ap.add_argument("--seed", type=int, default=20260908)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "nlp_dataset.csv"

    intents = list(WEIGHTS)
    probs = [WEIGHTS[i] for i in intents]
    seen: set[str] = set()
    rows = []
    guard = 0
    while len(rows) < args.n and guard < args.n * 60:
        guard += 1
        intent = rng.choices(intents, weights=probs, k=1)[0]
        text = noisy(rng, GENERATORS[intent](rng))
        if len(text) < 2 or text in seen:
            continue
        seen.add(text)
        rows.append((text, intent))

    rng.shuffle(rows)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["text", "intent"])
        w.writerows(rows)

    counts: dict[str, int] = {}
    for _, i in rows:
        counts[i] = counts.get(i, 0) + 1
    print(f"[nlp] wrote {len(rows):,} unique utterances -> {out}")
    for i in sorted(counts):
        print(f"       {i:<10s} {counts[i]:>6,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
