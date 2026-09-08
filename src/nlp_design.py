"""Natural-language design assistant — parse a sentence into a real design.

Two halves, deliberately different in kind:

  * INTENT is learned. A hashed word+char n-gram vector goes through a
    multinomial logistic regression trained offline (ml/nlp/train_nlp.py) and
    exported to JSON. Inference here is pure NumPy, so the serverless bundle
    carries no scikit-learn — the same trick src/ai_model.py uses.

  * SLOTS are extracted by an explicit gazetteer + regex. This is a
    deliberate choice, not a shortcut: the slots drive a physics engine, so a
    wrong material silently substituted is worse than an honest "I didn't
    catch that". A gazetteer is auditable, needs no training data for a
    15-material vocabulary, and never hallucinates a value that isn't in the
    project's own material list.

Everything runs offline with no API key, so the offline edition and the
"free stack" claim both survive.

The parser PROPOSES. The RC engine still verifies — /api/nlp/design returns a
design plus the confidence it was understood, and the UI runs the real
simulation on it.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import numpy as np

MODEL_FILE = Path(__file__).resolve().parent / "data" / "nlp_model.json"

#: hashing dimension — fixed so the exported matrix size never depends on
#: how much training data we throw at it
N_BUCKETS = 4096

_model_cache: dict | None = None


# --------------------------------------------------------------------------
# featuriser — MUST be identical at train and inference time
# --------------------------------------------------------------------------
def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).lower()
    text = re.sub(r"[^a-z0-9\s\.\-x/]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _hash(token: str) -> int:
    # FNV-1a: stable across processes and Python versions, unlike hash()
    h = 0x811C9DC5
    for ch in token.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h % N_BUCKETS


def feature_counts(text: str) -> dict[int, float]:
    """Hashed word 1-2 grams + char 3-5 grams as {bucket: count}.

    Returned sparse because the training matrix would otherwise be
    n_utterances x N_BUCKETS dense floats — 655 MB for 40k rows, which is an
    instant OOM on a small box. Roughly 200 of 4096 buckets are ever non-zero.
    """
    t = normalise(text)
    counts: dict[int, float] = {}
    def bump(tok: str) -> None:
        b = _hash(tok)
        counts[b] = counts.get(b, 0.0) + 1.0
    words = t.split()
    for w in words:
        bump("w:" + w)
    for i in range(len(words) - 1):
        bump("b:" + words[i] + "_" + words[i + 1])
    padded = f" {t} "
    for n in (3, 4, 5):
        for i in range(len(padded) - n + 1):
            bump(f"c{n}:" + padded[i:i + n])
    return counts


def featurise(text: str) -> np.ndarray:
    """Dense L2-normalised feature vector — used for single-utterance
    inference, where one 4096-float vector is trivial."""
    counts = feature_counts(text)
    vec = np.zeros(N_BUCKETS, dtype=np.float32)
    if not counts:
        return vec
    idx = np.fromiter(counts.keys(), dtype=np.int32, count=len(counts))
    val = np.fromiter(counts.values(), dtype=np.float32, count=len(counts))
    vec[idx] = val
    n = np.linalg.norm(vec)
    return vec / n if n > 0 else vec


# --------------------------------------------------------------------------
# intent
# --------------------------------------------------------------------------
def load_model(refresh: bool = False) -> dict | None:
    global _model_cache
    if _model_cache is not None and not refresh:
        return _model_cache
    try:
        _model_cache = json.loads(MODEL_FILE.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        _model_cache = None
    return _model_cache


def classify(text: str) -> tuple[str, float, dict]:
    """Return (intent, confidence, all_scores)."""
    m = load_model()
    if not m:
        return "design", 0.0, {}
    x = featurise(text)
    logits = np.asarray(m["coef"], dtype=np.float32) @ x + \
        np.asarray(m["intercept"], dtype=np.float32)
    logits = logits - logits.max()
    p = np.exp(logits)
    p = p / p.sum()
    labels = m["labels"]
    i = int(np.argmax(p))
    return labels[i], float(p[i]), {l: round(float(v), 4)
                                    for l, v in zip(labels, p)}


# --------------------------------------------------------------------------
# slot gazetteers — every value maps onto something the engine really has
# --------------------------------------------------------------------------
SITE_ALIASES = {
    "prayagraj": "Prayagraj", "allahabad": "Prayagraj",
    "delhi": "Delhi", "new delhi": "Delhi", "ncr": "Delhi",
    "jaipur": "Jaipur", "pink city": "Jaipur",
    "jaisalmer": "Jaisalmer", "thar": "Jaisalmer",
    "ahmedabad": "Ahmedabad", "amdavad": "Ahmedabad",
    "chennai": "Chennai", "madras": "Chennai",
    "mumbai": "Mumbai", "bombay": "Mumbai",
    "kolkata": "Kolkata", "calcutta": "Kolkata",
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru",
    "hyderabad": "Hyderabad", "pune": "Pune", "poona": "Pune",
    "leh": "Leh", "ladakh": "Leh",
    "srinagar": "Srinagar", "kashmir": "Srinagar",
    "kargil": "Kargil", "dras": "Dras", "drass": "Dras",
}

WALL_ALIASES = {
    "brick": "brick", "burnt brick": "brick", "red brick": "brick",
    "mud brick": "mud_brick", "mudbrick": "mud_brick", "adobe": "mud_brick",
    "mud": "mud_brick", "clay": "mud_brick",
    "rammed earth": "rammed_earth", "earth": "rammed_earth",
    "stone": "stone", "granite": "stone", "masonry": "stone",
    "concrete": "concrete", "rcc": "rcc_slab", "cement": "concrete",
    "timber": "timber", "wood": "timber", "wooden": "timber",
    "plywood": "plywood", "ply": "plywood",
    "aac": "aerated_concrete", "aac block": "aerated_concrete",
    "aerated concrete": "aerated_concrete", "siporex": "aerated_concrete",
    "puf": "puf_sandwich_panel", "puf panel": "puf_sandwich_panel",
    "sandwich panel": "puf_sandwich_panel", "panel": "puf_sandwich_panel",
    "gi sheet": "gi_sheet", "gi": "gi_sheet", "tin": "gi_sheet",
    "tin sheet": "gi_sheet", "metal sheet": "gi_sheet",
    "corrugated": "gi_sheet", "steel sheet": "gi_sheet",
}

ROOF_ALIASES = dict(WALL_ALIASES)
ROOF_ALIASES.update({"rcc slab": "rcc_slab", "slab": "rcc_slab",
                     "concrete roof": "rcc_slab", "rcc roof": "rcc_slab"})

INS_ALIASES = {
    "eps": "eps", "thermocol": "eps", "styrofoam": "eps", "expanded": "eps",
    "xps": "xps", "extruded": "xps",
    "mineral wool": "mineral_wool", "rockwool": "mineral_wool",
    "rock wool": "mineral_wool", "glass wool": "mineral_wool",
    "glasswool": "mineral_wool",
    "sheep wool": "sheep_wool", "wool": "sheep_wool",
    "no insulation": "none", "without insulation": "none",
    "uninsulated": "none", "none": "none",
}

ORIENT_WORDS = {"north": "north", "south": "south",
                "east": "east", "west": "west"}

GOAL_WORDS = {
    "cool": "cooling", "cooler": "cooling", "cold": "cooling",
    "hot weather": "cooling", "summer": "cooling", "heat": "cooling",
    "warm": "heating", "warmer": "heating", "winter": "heating",
    "freezing": "heating", "cold climate": "heating",
    "cheap": "low_cost", "low cost": "low_cost", "affordable": "low_cost",
    "budget": "low_cost", "inexpensive": "low_cost",
    "quick": "rapid", "rapid": "rapid", "fast": "rapid",
    "emergency": "rapid", "temporary": "rapid",
    "comfortable": "comfort", "liveable": "comfort", "livable": "comfort",
}


def _find_alias(text: str, table: dict) -> tuple[str | None, str | None]:
    """Longest-match-first so 'mud brick' beats 'brick' and 'gi sheet' beats 'gi'."""
    best_key = None
    for key in sorted(table, key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", text):
            best_key = key
            break
    return (table[best_key], best_key) if best_key else (None, None)


def extract_slots(text: str) -> dict:
    """Pull design values out of free text. Only returns what it truly finds."""
    t = normalise(text)
    slots: dict = {}

    site, _ = _find_alias(t, SITE_ALIASES)
    if site:
        slots["site"] = site

    # material words are scoped to the noun they precede/follow, so
    # "brick walls with a tin roof" resolves both correctly
    wall_ctx = re.search(r"([a-z \-]{0,22})\bwalls?\b", t)
    roof_ctx = re.search(r"([a-z \-]{0,22})\broof(?:ing)?\b", t)
    if wall_ctx:
        w, _ = _find_alias(wall_ctx.group(1), WALL_ALIASES)
        if w:
            slots["wall_material"] = w
    if roof_ctx:
        r, _ = _find_alias(roof_ctx.group(1), ROOF_ALIASES)
        if r:
            slots["roof_material"] = r
    if "wall_material" not in slots:
        w, _ = _find_alias(t, WALL_ALIASES)
        if w and not roof_ctx:
            slots["wall_material"] = w

    ins, _ = _find_alias(t, INS_ALIASES)
    if ins:
        slots["insulation_material"] = ins

    # thicknesses: "200 mm walls", "wall 0.3 m", "300mm insulation"
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(mm|cm|m)\b([^.,;]{0,26})", t):
        val, unit, tail = float(m.group(1)), m.group(2), m.group(3)
        head = t[max(0, m.start() - 26):m.start()]
        metres = val / 1000 if unit == "mm" else val / 100 if unit == "cm" else val
        ctx = head + " " + tail
        if not 0.005 <= metres <= 1.0:
            continue
        if "insul" in ctx or "eps" in ctx or "wool" in ctx:
            slots["insulation_thickness_m"] = round(metres, 4)
        elif "wall" in ctx:
            slots["wall_thickness_m"] = round(metres, 4)
        elif "roof" in ctx or "slab" in ctx:
            slots["roof_thickness_m"] = round(metres, 4)

    # footprint: "3x4", "4 by 5 m", "3 x 3 metres"
    dim = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|by|\*)\s*(\d+(?:\.\d+)?)", t)
    if dim:
        a, b = float(dim.group(1)), float(dim.group(2))
        if 1.5 <= a <= 20 and 1.5 <= b <= 20:
            slots["length_m"], slots["width_m"] = a, b

    ht = re.search(r"(?:height|tall|ceiling)\D{0,12}(\d+(?:\.\d+)?)\s*m\b", t)
    if ht and 1.8 <= float(ht.group(1)) <= 6:
        slots["height_m"] = float(ht.group(1))

    # window orientation — "facing north", "windows on the south"
    win = re.search(r"(?:facing|faces|orient\w*|window[s]?\s+(?:on|to)?\s*(?:the)?)\s*"
                    r"(north|south|east|west)", t)
    if win:
        slots["window_wall"] = ORIENT_WORDS[win.group(1)]
    else:
        alt = re.search(r"(north|south|east|west)[\s\-]*facing", t)
        if alt:
            slots["window_wall"] = ORIENT_WORDS[alt.group(1)]

    rot = re.search(r"(?:rotat\w*|orient\w*)\D{0,12}(\d{1,3})\s*(?:deg|degree)", t)
    if rot and 0 <= int(rot.group(1)) <= 359:
        slots["orientation_deg"] = int(rot.group(1))

    goals = []
    for k, v in GOAL_WORDS.items():
        if re.search(rf"\b{re.escape(k)}\b", t) and v not in goals:
            goals.append(v)
    if goals:
        slots["goals"] = goals

    if re.search(r"\b(big|large|spacious)\b", t):
        slots.setdefault("length_m", 5.0)
        slots.setdefault("width_m", 4.0)
    elif re.search(r"\b(small|compact|tiny|minimal)\b", t):
        slots.setdefault("length_m", 3.0)
        slots.setdefault("width_m", 3.0)

    people = re.search(r"(\d+)\s*(?:people|persons?|members|occupants)", t)
    if people:
        n = int(people.group(1))
        if 1 <= n <= 20:
            slots["occupants"] = n
            area = max(9.0, 3.5 * n)              # ~3.5 m2/person, Sphere-ish
            side = round(area ** 0.5, 1)
            slots.setdefault("length_m", min(side, 6.0))
            slots.setdefault("width_m", min(side, 6.0))
    return slots
