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

try:                                   # repo-root import (api, tests)
    from src import nlp_normalizer
except ImportError:                      # src/ on sys.path (standalone use)
    import nlp_normalizer

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
    vec = np.zeros(N_BUCKETS, dtype=np.float64)   # float64: matches the JS
    # mirror exactly — Math.imul/FLOAT64 arithmetic reproduce this dot product
    # digit for digit, which is what the edge-parity gate measures
    if not counts:
        return vec
    idx = np.fromiter(counts.keys(), dtype=np.int32, count=len(counts))
    val = np.fromiter(counts.values(), dtype=np.float64, count=len(counts))
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
    logits = np.asarray(m["coef"], dtype=np.float64) @ x + \
        np.asarray(m["intercept"], dtype=np.float64)
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
#: Vernacular/alternate names straight from the SIH dataset doc's
#: LOCATION_SYNONYMS table (Part 3.3) — pure lookup, no inference.
SITE_ALIASES = {
    "prayagraj": "Prayagraj", "allahabad": "Prayagraj", "sangam": "Prayagraj",
    "delhi": "Delhi", "new delhi": "Delhi", "ncr": "Delhi",
    "jaipur": "Jaipur", "pink city": "Jaipur", "rajasthan": "Jaipur",
    "jaisalmer": "Jaisalmer", "thar": "Jaisalmer", "pokhran": "Jaisalmer",
    "desert post": "Jaisalmer",
    "ahmedabad": "Ahmedabad", "amdavad": "Ahmedabad", "gujarat": "Ahmedabad",
    "chennai": "Chennai", "madras": "Chennai", "tamil nadu": "Chennai",
    "mumbai": "Mumbai", "bombay": "Mumbai", "coastal west": "Mumbai",
    "kolkata": "Kolkata", "calcutta": "Kolkata", "bengal": "Kolkata",
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru",
    "hyderabad": "Hyderabad", "secunderabad": "Hyderabad",
    "pune": "Pune", "poona": "Pune",
    "leh": "Leh", "ladakh": "Leh", "indus valley": "Leh",
    "srinagar": "Srinagar", "kashmir": "Srinagar", "kashmir valley": "Srinagar",
    "kargil": "Kargil", "suru valley": "Kargil",
    "dras": "Dras", "drass": "Dras", "subzero sector": "Dras",
}

WALL_ALIASES = {
    "brick": "brick", "burnt brick": "brick", "red brick": "brick",
    "clay brick": "brick", "pucca brick": "brick",
    "mud brick": "mud_brick", "mudbrick": "mud_brick", "adobe": "mud_brick",
    "sun dried brick": "mud_brick", "kaccha brick": "mud_brick",
    "mud": "mud_brick", "clay": "mud_brick",
    "rammed earth": "rammed_earth", "earth": "rammed_earth",
    "pise": "rammed_earth", "compacted earth": "rammed_earth",
    "dhajji dewari": "rammed_earth",
    "stone": "stone", "granite": "stone", "masonry": "stone",
    "sandstone": "stone", "kota stone": "stone",
    "concrete": "concrete", "cast concrete": "concrete", "rcc": "rcc_slab",
    "cement": "concrete",
    "timber": "timber", "wood": "timber", "wooden": "timber",
    "wooden beam": "timber", "sal wood": "timber",
    "plywood": "plywood", "ply": "plywood", "marine ply": "plywood",
    "wood board": "plywood",
    "aac": "aerated_concrete", "aac block": "aerated_concrete",
    "aerated concrete": "aerated_concrete", "siporex": "aerated_concrete",
    "light concrete": "aerated_concrete",
    "autoclaved aerated concrete": "aerated_concrete",
    "puf": "puf_sandwich_panel", "puf panel": "puf_sandwich_panel",
    "sandwich panel": "puf_sandwich_panel", "panel": "puf_sandwich_panel",
    "polyurethane panel": "puf_sandwich_panel", "pre-fab panel": "puf_sandwich_panel",
    "gi sheet": "gi_sheet", "gi": "gi_sheet", "tin": "gi_sheet",
    "tin sheet": "gi_sheet", "tin roof": "gi_sheet", "chadar": "gi_sheet",
    "iron sheet": "gi_sheet", "metal sheet": "gi_sheet",
    "corrugated": "gi_sheet", "corrugated sheet": "gi_sheet",
    "steel sheet": "gi_sheet",
}

ROOF_ALIASES = dict(WALL_ALIASES)
ROOF_ALIASES.update({"rcc slab": "rcc_slab", "slab": "rcc_slab",
                     "reinforced concrete": "rcc_slab",
                     "concrete slab": "rcc_slab", "cement slab": "rcc_slab",
                     "concrete roof": "rcc_slab", "rcc roof": "rcc_slab"})

INS_ALIASES = {
    "eps": "eps", "thermocol": "eps", "styrofoam": "eps", "expanded": "eps",
    "expanded polystyrene": "eps", "polystyrene": "eps",
    "white thermocol": "eps",
    "xps": "xps", "extruded": "xps", "extruded polystyrene": "xps",
    "blueboard": "xps", "pinkboard": "xps",
    "mineral wool": "mineral_wool", "rockwool": "mineral_wool",
    "rock wool": "mineral_wool", "glass wool": "mineral_wool",
    "glasswool": "mineral_wool", "slag wool": "mineral_wool",
    "sheep wool": "sheep_wool", "wool": "sheep_wool",
    "natural wool": "sheep_wool",
    # NB: no "wool insulation" key — it would out-length
    # "mineral wool" inside "mineral wool insulation" and
    # mislabel it; bare "wool" already covers that speech
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


def _has_long_word(t: str, min_len: int = 7) -> bool:
    """Cheap gate before any edit-distance work: with no word of >=min_len
    chars there is nothing the fuzzy tier could accept (_allowed_dist is 0
    for keys <=4 and 1 for <=7, and those are covered by the exact scan)."""
    return any(len(w) >= min_len for w in t.split())


def _find_alias(text: str, table: dict) -> tuple[str | None, str | None]:
    """Longest-match-first so 'mud brick' beats 'brick' and 'gi sheet' beats 'gi'."""
    best_key = None
    for key in sorted(table, key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", text):
            best_key = key
            break
    return (table[best_key], best_key) if best_key else (None, None)


# --------------------------------------------------------------------------
# Hinglish / romanised-Hindi bridge
# --------------------------------------------------------------------------
#: Real users on this project code-mix: "mitti ki deewar", "chhat pe GI sheet".
#: The classifier is trained on code-mixed sentences too (ml/nlp generator v4),
#: and slot extraction runs on this transliteration so every English rule below
#: fires on Hindi phrasing without duplicating the gazetteers. Romanised
#: Devanagari only — the featuriser's normalise() is untouched, so the
#: train/inference contract is unchanged.
HINGLISH_MAP = {
    # envelope nouns
    "deewar": "wall", "deewaron": "walls", "deewarein": "walls",
    "diwar": "wall", "chhat": "roof", "chatt": "roof", "chhath": "roof",
    "khidki": "window", "khidkiya": "windows", "khidkiyon": "windows",
    "farsh": "floor", "kamra": "room", "makan": "house", "ghar": "house",
    # materials
    "mitti": "mud", "mati": "mud", "kanchi": "glass", "pathar": "stone",
    "eent": "brick", "eeent": "brick", "lakdi": "timber", "thermocol": "eps",
    "sheet": "sheet", "loha": "metal",
    # drivers / goals
    "garmi": "heat", "thand": "cold", "sardi": "cold", "dhoop": "sun",
    "dhup": "sun", "barish": "rain", "barsat": "rain", "baadh": "flood",
    "badh": "flood", "toofan": "cyclone", "barf": "snow", "hawa": "wind",
    "bhukamp": "earthquake", "nanami": "flood",
    # size / shape
    "bada": "big", "ghumao": "rotate", "ghumaoo": "rotate",
    "taraf": "toward", "yani": "", "bohot": "very", "turant": "fast",
    "banne": "build", "jagah": "instead", "sakte": "", "sakate": "",
    "hi": "", "bas": "only", "chal": "going", "badi": "big", "bari": "big", "chhota": "small",
    "chhoti": "small", "moti": "thick", "mota": "thick", "patli": "thin",
    "patla": "thin", "ooncha": "tall", "unchi": "tall", "neechee": "low",
    # orientation
    "dakshin": "south", "uttar": "north", "purab": "east", "pachhim": "west",
    # verbs / particles that carry intent
    "banao": "design", "banado": "design", "banaiye": "design",
    "banwana": "design", "design": "design",
    "laga": "add", "daal": "add", "badlo": "change", "badal": "change",
    "karo": "do", "kiijiye": "do", "kijiye": "do",
    "zyada": "more", "jyada": "more", "kam": "less",
    "sasta": "cheap", "se": "from", "mein": "in", "me": "in", "pe": "on",
    "par": "on", "aur": "and", "ya": "or", "hai": "", "ho": "", "ke": "",
    "ki": "", "ka": "", "wali": "", "wala": "", "liye": "for",
    "bachane": "protect", "bachne": "protect", "bachao": "protect", "jawano": "soldiers",
    "behtar": "better", "achhi": "good", "achha": "good", "achhe": "good",
    "jawanon": "soldiers", "logon": "people", "logo": "people",
    "khushgawar": "comfortable", "aaram": "comfort",
}

#: multi-word Hinglish imperatives win over the single-token map, because
#: "laga do"/"kar do" ARE the modify verb in code-mixed speech
HINGLISH_PHRASES = {
    "laga do": "add", "laga dena": "add", "daal do": "add", "dal do": "add",
    "nikal do": "remove", "hata do": "remove", "kar do": "set",
    "kar dijiye": "set", "de dijiye": "add", "badh do": "increase",
}
_HING_PHRASE_RE = re.compile(
    r"\b(" + "|".join(sorted(HINGLISH_PHRASES, key=len, reverse=True)) + r")\b")
_HING_RE = re.compile(r"\b(" + "|".join(sorted(HINGLISH_MAP, key=len, reverse=True)) + r")\b")


def _hinglish(t: str) -> str:
    """Translate romanised-Hindi tokens into the English vocabulary the
    gazetteers already know. Runs only for slots/grammar — never before the
    intent featuriser, whose contract with the trained model must not move."""
    t = _HING_PHRASE_RE.sub(lambda m: HINGLISH_PHRASES[m.group(1)], t)
    return re.sub(r"\s+", " ", _HING_RE.sub(lambda m: HINGLISH_MAP[m.group(1)], t)).strip()


#: Spelled-out numbers. People write "a family of six", not "6" — and the
#: extractor was digit-only, so those requests silently lost their occupancy
#: and footprint. Applied ONLY in slot extraction: normalise() feeds the
#: intent featuriser and must stay byte-identical to training.
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20,
}

#: Hazards the project already has an engine-verified answer for. Saying
#: "cyclone" used to change nothing at all, even though a
#: Cyclone-Resilient Coastal Shell preset exists in the library.
HAZARD_WORDS = {
    "cyclone": "cyclone", "typhoon": "cyclone", "hurricane": "cyclone",
    "storm surge": "cyclone", "high wind": "cyclone",
    "flood": "flood", "floods": "flood", "flooding": "flood",
    "waterlogging": "flood", "monsoon": "monsoon", "heavy rain": "monsoon",
    "snow": "snow", "snowfall": "snow", "snow load": "snow",
    "blizzard": "snow", "earthquake": "seismic", "seismic": "seismic",
}

#: Ventilation is a REAL engine input (config ventilation_ach). "well
#: ventilated" was parsed as nothing, so the phrase changed no physics.
VENT_WORDS = {
    "cross ventilation": 8.0, "cross-ventilation": 8.0,
    "well ventilated": 6.0, "well-ventilated": 6.0, "good ventilation": 6.0,
    "plenty of ventilation": 8.0, "lots of ventilation": 8.0,
    "breezy": 6.0, "airy": 6.0, "night purge": 8.0, "night flush": 8.0,
    "sealed": 0.5, "airtight": 0.5, "air tight": 0.5, "draught free": 1.0,
    "draft free": 1.0, "no ventilation": 0.5,
}


def _digitise(t: str) -> str:
    """Rewrite spelled-out numbers as digits so the numeric regexes fire."""
    for word, num in _NUM_WORDS.items():
        t = re.sub(rf"\b{word}\b", str(num), t)
    return t


# --------------------------------------------------------------------------
# comparison constructs — "compare X with Y", "is X better than Y", "X ya Y"
# --------------------------------------------------------------------------
#: The bag-of-words classifier sees "compare brick shelter with stone shelter
#: in Jaisalmer", over-weights shelter/Jaisalmer and answers `design`. The
#: grammar itself is deterministic, so we read the pair with rules and let
#: the rules also CORRECT a confidently wrong intent (the audit dataset's
#: first severe false positive).
_OP_PATTERNS = [
    (r"\bcompare\s+(.+?)\s+(?:with|against|to|versus|vs\.?)\s+(.+?)(?=\s+(?:in|for|at|across)\s+\w|$)", 1, 2),
    (r"\bdifference between\s+(.+?)\s+and\s+(.+?)(?=\s+(?:in|for|at)\s+\w|$)", 1, 2),
    (r"\b(?:which|who)\b[^?]{0,40}?\b(?:is|performs|does|fare|stacks|works)\b[^?]{0,30}?\bbetter\b[^?]{0,30}?:?\s*(.+?)\s+(?:or|versus|vs\.?)\s+(.+?)\??$", 1, 2),
    (r"\b(.+?)\s+(?:versus|vs\.|vs|against|compared to|stack up against|compared with)\s+(.+?)(?=\s+(?:in|for|at)\s+\w|$)", 1, 2),
    (r"\b(.+?)\s+(?:is|are|hai|achhi hai|achha hai)\s+better\s+than\s+(.+?)\??$", 1, 2),
    (r"(.+?)\s+(?:behtar|better)\s+(?:hai|a)\s+ya\s+(.+?)\??$", 1, 2),
    (r"\b(.+?)\s+(?:or|ya)\s+(.+?)\b(?:kaun|which)?\s*(?:better|behtar|achhi|achha|cooler|warmer)\b", 1, 2),
    (r"\bis\s+(.+?)\s+(?:harsher|milder|hotter|cooler|wetter|drier)\s+than\s+(.+?)\??$", 1, 2),
    (r"\b(.+?)\s+(?:good|better)\s+(?:hai\s+)?(?:for\s+\w+\s+)?(?:or|ya)\s+(?:ki\s+)?(.+?)\??$", 1, 2),
    (r"\b(.+?)\s+(?:or|ya)\s+(.+?)\s+better\b", 1, 2),
]


def _side_entities(side: str) -> dict:
    """Resolve one half of a comparison into engine-known entities."""
    out: dict = {}
    mat, key = _find_alias(side, WALL_ALIASES)
    if mat:
        out["wall_material"] = mat
        out["label_matched"] = key
    ins, ikey = _find_alias(side, INS_ALIASES)
    if ins:
        out["insulation_material"] = ins
        out.setdefault("label_matched", ikey)
    thk = re.search(r"(\d+(?:\.\d+)?)\s*(mm|cm)\b", side)
    if thk:
        v = float(thk.group(1)) / (1000 if thk.group(2) == "mm" else 100)
        out["thickness_m"] = round(v, 4)
    site, _ = _find_alias(side, SITE_ALIASES)
    if site:
        out["site"] = site
    out["raw"] = side.strip()[:60]
    return out


def _compare_entities(t: str) -> dict | None:
    for pat, ga, gb in _OP_PATTERNS:
        m = re.search(pat, t)
        if not m:
            continue
        a = _side_entities(m.group(ga).strip())
        b = _side_entities(m.group(gb).strip())
        # a real pair needs one resolvable entity per side; a side that NAMES
        # a design ("Coastal Light Envelope or Cyclone-Resilient ... Shell") is
        # resolvable too — the endpoint fuzzy-matches it against the preset
        # library before anything else
        resolvable = lambda d: any(k in d for k in
                                   ("wall_material", "insulation_material", "site", "thickness_m")) \
            or re.search(r"\b(shell|envelope|studio|kit|cell|upgrade|dormitory|dwelling|"
                         r"post|bunker|cabin|hut|unit)\b\s*$", d.get("raw", "").lower()) \
            or re.search(r"\b(shell|envelope|studio|kit|cell|upgrade)\b", d.get("raw", "").lower())
        if resolvable(a) and resolvable(b):
            return {"a": a, "b": b, "pattern": pat[:24]}
    return None


# --------------------------------------------------------------------------
# Guard + undo patterns are module constants (not inline literals) so the
# OFFLINE JS runtime (offline/engine.js OfflineNLP) embeds the exact same
# strings from the bundle — one source of truth, verified by
# scripts/check_nlp_edge_parity.py rather than by faith.
# --------------------------------------------------------------------------
GUARD_INJECT = (
    r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\b"
    r"|\bsystem\s+prompt\b|\bjailbreak\b|\bpretend\s+to\s+be\b|\byou\s+are\s+now\b"
    r"|reveal.{0,24}(?:password|credential|secret|database|api key)"
    r"|(?:show|print|leak|dump|give me).{0,28}(?:\brows\b|\btable\b|\bsql\b"
    r"|api keys?|access token|secret keys?|credentials?"
    r"|passwords?\b|env vars?\b)"
    r"|\b(?:drop|delete)\s+table\b|\bselect\s+\*|\binsert\s+into\b"
    r"|\bunion\s+select\b|\bxp_cmdshell\b|\bor\s+1=1\b")

GUARD_OOD = (
    r"\bcapital of\b|\bpopulation of\b|\blinked list\b|\bpython script\b|"
    r"\bwrite (?:a|me)\b.{0,12}\b(?:poem|essay|script|function|code)\b|"
    r"\b(?:book|order|reserve) me\b|\bflight (?:ticket|to)\b|\bhotel room\b|"
    r"\brecipe\b|\bbirthday\b|\bhoroscope\b|\btranslate (?:this|the)\b|"
    r"\bmeaning of life\b|\bstory about\b|\bjoke\b"
    r"|\bhack\b|\bwifi password|\bcrack(?:ing)? .{0,10}password\b"
    r"|\bkali linux\b|\bmalware\b|\bransomware\b"
    r"|\b(?:price|rate|value) of\b[^.]{0,14}\b(?:bitcoin|btc|crypto"
    r"|gold|rupee|dollar|nifty|sensex)\b|\b(?:bitcoin|crypto)\b"
    r"|\bexchange rate\b|\bmatch score\b|\bcricket score\b")

UNDO_RE = (r"\b(?:undo|revert|rollback)\b"
           r"|\broll(?:s|ing)?\s+(?:it|that|this)\s+back\b|\broll\s?back\b"
           r"|\bback to (?:the )?previous\b|\btake it back\b")


def grammar(t: str) -> tuple[str | None, str]:
    """Deterministic intent corrections from constructs the n-gram bag
    cannot represent. Returns (intent|None, reason). Only fires on constructs
    that are unambiguous by construction, so a bare "compare" with no pair
    never overrides."""
    # --- guard: prompt injection / clearly-out-of-domain -------------------
    if re.search(GUARD_INJECT, t):
        return "unknown", "guard:prompt-injection"
    if re.search(GUARD_OOD, t):
        return "unknown", "guard:out-of-domain"
    # --- conversational undo (roadmap Step 3.4) -----------------------------
    # "undo" / "revert that" / "back to the previous design" are instructions
    # on the DIALOGUE STATE, not the envelope: always a modify, even when the
    # sentence is too short for the classifier to have seen its shape before.
    if re.search(UNDO_RE, t):
        return "modify", "grammar:undo"
    # --- optimisation verb ANYWHERE ("... ke liye optimize karo") -----------
    # the Hinglish word order puts it at the end; English imperatives at the
    # front; both are unambiguous commands
    if re.search(r"\boptimi[sz]\w*\b|\boptimal\b|\bparameter sweep\b|"
                 r"\bbest combination\b", t):
        return "optimize", "grammar:optimisation-verb"
    # --- climate-recon questions ("what's the coldest it gets in Kargil") ----
    if re.match(r"^(?:what|when|how|is|does|tell)\b", t) and re.search(
            r"\b(?:coldest|hottest|warmest|mildest|rainiest|humidest|"
            r"how (?:hot|cold|humid|windy)|temperature (?:get|range)|"
            r"degrees? in [a-z]+)\b", t):
        return "explain", "grammar:climate-recon"
    # --- conceptual questions read as explain even with an A-vs-B shape ------
    if re.match(r"^(?:what|why|when|how)\b", t) and (re.search(
            r"\bmean[s]?\b|\bmeaning\b|\bcoefficient\b|\bwhat is the\b|"
            r"\bdiffer\w*\b|\bdifference\b|\bdefine\b|\baffect\w*\b|"
            r"\bimpact\w*\b|\binfluenc\w*\b|\bcause[sd]?\b", t)
            or re.search(r"^\s*why\b.{0,48}?\b(?:cool|heat|overheat|warm|"
                         r"freeze|losing|loss)\w*\b", t)) \
            and not re.match(r"^\s*(?:please\s+)?compare\b", t):
        return "explain", "grammar:concept-question"
    # --- compare needs a resolvable pair ------------------------------------
    if _compare_entities(t) is not None:
        return "compare", "grammar:compare-construct"
    # --- imperative refinements of the design on screen ----------------------
    if re.match(r"^(?:now\s+|also\s+|please\s+)?(?:change|set|make|swap|replace|add|"
                r"remove|increase|decrease|reduce|drop|rotate|relocate|move|seal|"
                r"shrink|enlarge|extend|adjust|tweak|update|shift|switch|bump|"
                r"close|open|double|halve|thicken|thin(?:ner)?|widen)\b", t) \
            and not re.search(r"\b(shelter|house|cabin|unit)\b.{0,20}\bfor\s+[a-z]+\b.*\b"
                              r"(?:design|build)\b", t):
        # "build a shelter for X" style fresh briefs keep their own intent
        if not re.match(r"^(?:now\s+|please\s+)?(?:change|make|set)\b.*\b(?:in|for|to)\s+"
                        r"(?:" + "|".join(SITE_ALIASES) + r")\b.*\b(?:shelter|unit|cabin|hut|house)\b", t):
            return "modify", "grammar:imperative-refinement"
    # --- explicit optimisation verbs (must precede the modify clause rule:
    #     "optimize ventilation and add 50mm eps" is an OPTIMISE with a
    #     side instruction, not a bare refinement) --------------------------
    if re.search(r"^\s*(?:please\s+)?(?:optimi[sz]e|tune|sweep|maximi[sz]e|minimi[sz]e|"
                 r"find (?:me )?(?:the )?best|search (?:the )?design space|"
                 r"run (?:a )?\d+[- ]?trial|what is the optimal)\b"
                 r"|\b(?:optimal|best) (?:orientation|wall|roof|combination|envelope|"
                 r"insulation|window|configuration|thickness)\b"
                 r"|\bparameter sweep\b", t):
        return "optimize", "grammar:optimisation-verb"
    if re.search(r"\b(?:add|remove|set|change|swap|replace|increase|decrease|reduce|"
                 r"rotate|turn|flip|make)\b", t) \
            and not re.search(r"\b(?:design|build|create|need|want|draw up|"
                              f"put together|make me)\b", t) \
            and len(t.split()) <= 16 \
            and not re.search(r"\b(?:shelter|house|cabin|unit|hut)\b[^.]{0,24}"
                              r"\b(?:in|for)\s+\w+", t):
        return "modify", "grammar:imperative-clause"
    # --- design fallback: a build-noun brief, no question, no rival shape ---
    if re.search(r"\b(shelter|unit|cabin|bunker|outpost|dwelling|dormitory|camp|"
                 r"relief kit|shed|hut|house|classroom|clinic|command post|"
                 r"watch cabin|barracks)\b", t) \
            and not re.search(r"\b(?:harsher|better than|difference between|"
                              r"versus|\bvs\b|compare)\b", t) \
            and not re.search(r"\?|^\s*(?:is|are|does|do|which|how|what|why|"
                              r"when|can)\b", t):
        return "design", "grammar:build-noun"
    return None, ""


def extract_slots(text: str) -> dict:
    """Pull design values out of free text. Only returns what it truly finds."""
    # Tier 2 (nlp_normalizer): imperial speech is converted BEFORE the
    # gazetteer sees it, so "10 by 12 feet" and "9 inch brick" flow through
    # the same metric rules as everything else. Conversions are recorded in
    # `unit_conversions` for the honesty trail.
    text_m, unit_notes = nlp_normalizer.normalize_units(text)
    t = _digitise(_hinglish(normalise(text_m)))
    # gazetteer scan text: hyphens and slashes flatten to spaces so
    # "mud-brick", "well-ventilated" and "4/5" match the same keys as
    # their spaced forms (the gold corpus writes compounds hyphenated)
    t = re.sub(r"\s+", " ", re.sub(r"[-/]+", " ", t))
    slots: dict = {}
    if unit_notes:
        slots["unit_conversions"] = unit_notes

    # conversational undo (roadmap Step 3.4): the words are a request for
    # the PREVIOUS design state; the endpoint owns the history
    if re.search(UNDO_RE, t):
        slots["undo"] = True

    site, _ = _find_alias(t, SITE_ALIASES)
    if not site and _has_long_word(t):
        # Tier 1 (nlp_normalizer): typo-tolerant site lookup, e.g.
        # "jaysalmer" -> Jaisalmer. Refuses when ambiguous. The long-word
        # gate keeps the DP off the hot path for ordinary sentences
        # (short keys are exact-only anyway per _allowed_dist).
        hit = nlp_normalizer.fuzzy_lookup(t, SITE_ALIASES)
        if hit:
            site, _fk, span = hit
            slots.setdefault("fuzzy_read", {})[span] = f"site {site}"
    if site:
        slots["site"] = site

    # material words are scoped to the noun they precede/follow, so
    # "brick walls with a tin roof" resolves both correctly
    # Prefixes are scanned in WHOLE words only (an unanchored [a-z ] class
    # used to start mid-word: "...aerated concrete blocks and cool roof..."
    # matched the prefix "crete blocks and cool " and the roof-phrase cut
    # then destroyed "concrete" for the wall fallback).
    wall_ctx = re.search(r"((?:[a-z]+ ){0,4})walls?\b", t)
    roof_ctx = re.search(r"((?:[a-z]+ ){0,4})roof(?:ing)?\b", t)

    def _scope(m: re.Match | None) -> tuple[str | None, int, int]:
        """Nearest clause before the structural noun: cut at connectors and at
        the OTHER structural noun, and report the span actually consumed."""
        if not m:
            return None, 0, 0
        prefix = m.group(1)
        prefix = re.split(r"\b(?:and|with|or|plus)\b", prefix)[-1]
        prefix = re.split(r"\bwalls?\b", prefix)[-1]
        return prefix, m.end() - len(prefix), m.end()

    w_scope, w_a, w_b = _scope(wall_ctx)
    r_scope, r_a, r_b = _scope(roof_ctx)
    if w_scope:
        w, _ = _find_alias(w_scope, WALL_ALIASES)
        if not w and _has_long_word(w_scope):
            hit = nlp_normalizer.fuzzy_lookup(w_scope, WALL_ALIASES)
            if hit:
                w, _wk, span = hit
                slots.setdefault("fuzzy_read", {})[span] = "wall material " + w
        if w:
            slots["wall_material"] = w
    if r_scope:
        r, _ = _find_alias(r_scope, ROOF_ALIASES)
        if not r and _has_long_word(r_scope):
            hit = nlp_normalizer.fuzzy_lookup(r_scope, ROOF_ALIASES)
            if hit:
                r, _rk, span = hit
                slots.setdefault("fuzzy_read", {})[span] = "roof material " + r
    else:
        r = None
    if not r and "roof_material" not in slots:
        # post-noun window: "chhat pe GI sheet dal do" (put GI sheet ON the
        # roof) — in postpositional phrasing the material FOLLOWS the
        # structural noun, so a sentence-initial "roof" has an EMPTY prefix
        # and the scope scan above cannot see it at all. Scan the short
        # window after the noun, cut at conjunctions so "on the roof and
        # walls" cannot steal a wall word.
        post = re.search(r"\broof(?:ing)?\b[^.,;]{0,30}", t)
        if post:
            window = re.split(r"\b(?:and|plus)\b", post.group(0))[0]
            r, _ = _find_alias(window, ROOF_ALIASES)
    if r:
        slots["roof_material"] = r
    # structural frames carry both surfaces in prefab speech ("timber frame")
    if re.search(r"\b(?:timber|wood|steel|bamboo) frame\b", t):
        fm = re.search(r"\b(timber|wood|steel|bamboo) frame\b", t).group(1)
        fm = "timber" if fm in ("wood",) else ("bamboo" if fm == "bamboo" else fm)
        if "wall_material" not in slots and fm != "bamboo":
            slots["wall_material"] = fm
        if "roof_material" not in slots and fm != "bamboo":
            slots["roof_material"] = fm
    if "wall_material" not in slots:
        # Search with the roof phrase cut out, so the roof's material cannot
        # be mistaken for the wall's and cannot mask it either. Previously any
        # mention of a roof suppressed this fallback entirely, so "a mud brick
        # shelter with a sloped roof" lost the mud brick, and "stone shelter
        # with GI sheet roof" lost the stone.
        t_no_roof = t
        if r_a and r_b > r_a:
            t_no_roof = t[:r_a] + " " + t[r_b:]
        w, _ = _find_alias(t_no_roof, WALL_ALIASES)
        if not w and _has_long_word(t_no_roof):
            hit = nlp_normalizer.fuzzy_lookup(t_no_roof, WALL_ALIASES)
            if hit:
                w, _wk, span = hit
                slots.setdefault("fuzzy_read", {})[span] = "wall material " + w
        if not w:
            # post-noun window for walls too ("diwar me cement laga do"
            # -> "wall in cement ..."): same empty-prefix issue as roofs
            post = re.search(r"\bwalls?\b[^.,;]{0,30}", t)
            if post:
                window = re.split(r"\b(?:and|plus)\b", post.group(0))[0]
                w, _ = _find_alias(window, WALL_ALIASES)
        if w:
            slots["wall_material"] = w

    # "PUF sandwich panels" with no roof noun names the whole envelope —
    # panels are wall AND roof in practice; extend only when the same phrase
    # already set the wall and no roof material was spoken
    if ("roof_material" not in slots
            and slots.get("wall_material") == "puf_sandwich_panel"
            and re.search(r"\bpuf\b[^.]{0,18}\bpanels?\b", t)):
        slots["roof_material"] = "puf_sandwich_panel"

    if re.search(r"\b(?:remove|drop|delete|strip|eliminate)\b[^.]{0,14}\binsulat\w*", t):
        # "remove all insulation" is an instruction, not a material lookup
        slots["insulation_material"] = "none"
        slots["insulation_thickness_m"] = 0.0
    else:
        ins, _ = _find_alias(t, INS_ALIASES)
        if not ins and _has_long_word(t):
            hit = nlp_normalizer.fuzzy_lookup(t, INS_ALIASES)
            if hit:
                ins, _ik, span = hit
                slots.setdefault("fuzzy_read", {})[span] = "insulation " + ins
        if ins:
            slots["insulation_material"] = ins

    # thicknesses: "200 mm walls", "wall 0.3 m", "300mm insulation"
    # unit list ordered longest-first; "0.25 meters" is the same number as
    # "0.25 m" — an unlisted plural used to silently drop the whole clause
    for m in re.finditer(
            r"(\d+(?:\.\d+)?)\s*(mm|cm|meters?|metres?|m)\b"
            r"(?=([^.,;]{0,26}))", t):
        # (?=...) tail: v4 consumed up to 26 chars of context INSIDE the
        # match, so "50mm EPS ... and 200mm wall thickness" never let the
        # scanner reach 200mm — the audit's first-match-dropping flaw
        # surviving in a new guise. Lookahead keeps the tail for attribution
        # without eating the next number.
        val, unit, tail = float(m.group(1)), m.group(2), m.group(3)
        head = t[max(0, m.start() - 26):m.start()]
        metres = val / 1000 if unit == "mm" else val / 100 if unit == "cm" else val
        ctx = head + " " + tail
        if not 0.005 <= metres <= 1.0:
            continue
        # Attribution priority: the WORD ATTACHED TO THE NUMBER (its
        # immediate tail) decides the layer — "75mm mineral wool for autumn"
        # is an insulation number even when "timber" sits loose in the head.
        tail_att = re.match(r"\s*(?:of\s+)?(?:the\s+)?(\w+(?:\s+\w+)?)", tail)
        tail_words = tail_att.group(1) if tail_att else ""
        insul_re = r"\b(insulat|eps|xps|wool|thermocol|mineral|glass\s+wool)\w*\b"
        wall_re = (r"\b(wall|brick|stone|earth|rammed|mud|timber|plywood|"
                   r"concrete|block|panel|aac|sheet)\w*\b")
        if re.match(insul_re, tail_words):
            slots["insulation_thickness_m"] = round(metres, 4)
        elif re.match(wall_re, tail_words):
            slots["wall_thickness_m"] = round(metres, 4)
        elif re.match(r"\b(roof|slab)\w*\b", tail_words):
            slots["roof_thickness_m"] = round(metres, 4)
        elif re.search(insul_re, ctx) and not re.search(wall_re, tail):
            slots["insulation_thickness_m"] = round(metres, 4)
        elif re.search(r"\bwall\b", ctx) or (
                "thickness" in ctx and slots.get("wall_material")):
            # "200 mm walls", "make thickness 0.25 meters"
            slots["wall_thickness_m"] = round(metres, 4)

    # footprint: "3x4", "4 by 5 m", "3 x 3 metres"
    # Before v5, anything outside 1.5..20 m was SILENTLY dropped — "a 40 by
    # 30 meter hall" quietly became the zone default with no explanation. Now
    # a dimension with a spoken unit word is taken up to 60 m, and the
    # endpoint's clamp (roadmap 3.5) narrows it AND REPORTS the narrowing.
    dim = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|by|\*)\s*(\d+(?:\.\d+)?)\s*"
                    r"(meters?|metres?|feet|foot|ft|m)?\b", t)
    if dim:
        a, b, u = float(dim.group(1)), float(dim.group(2)), dim.group(3)
        in_band = 1.5 <= a <= 20 and 1.5 <= b <= 20
        if in_band or (u and 0.5 <= min(a, b) and max(a, b) <= 60):
            slots["length_m"], slots["width_m"] = a, b

    ht = re.search(r"(?:height|tall|ceiling)\D{0,12}(\d+(?:\.\d+)?)\s*m\w*\b", t) \
        or re.search(r"(\d+(?:\.\d+)?)\s*(?:m|meter|meters)\s+(?:ceiling|height)\b", t)
    if ht and 1.8 <= float(ht.group(1)) <= 6:
        slots["height_m"] = float(ht.group(1))

    # window orientation — "facing north", "windows on the south",
    # "south glazing", "small north windows" (adjective-before-noun position)
    win = re.search(r"(?:facing|faces|orient\w*|window[s]?\s+(?:on|to)?\s*(?:the)?)\s*"
                    r"(north|south|east|west)", t)
    if win:
        slots["window_wall"] = ORIENT_WORDS[win.group(1)]
    else:
        alt = re.search(r"(north|south|east|west)[\s\-]*facing", t)
        if alt:
            slots["window_wall"] = ORIENT_WORDS[alt.group(1)]
        else:
            pre = re.search(r"(?:the\s+)?\b(north|south|east|west)\s+"
                            r"(?:facing\s+)?(?:windows?|glazing|openings?|glass)\b", t)
            if not pre:
                # direction + positional word ("dakshin yani south ki taraf
                # ghumao" transliterates to "south toward") inside a
                # window/rotate clause — requires those structure words so a
                # stray compass word in prose never sets an envelope value
                pre = re.search(r"\b(north|south|east|west)\b[^.,;]{0,14}"
                                r"\b(?:side|toward\w*|dir\w*|taraf)\b", t) \
                    if re.search(r"\b(?:window|khidki|glaz\w*|rotat\w*|"
                                 r"orient\w*|ghum\w*)", t) else None
            if pre:
                slots["window_wall"] = ORIENT_WORDS[pre.group(1)]

    rot = re.search(r"(?:rotat\w*|orient\w*)\D{0,12}(\d{1,3})\s*(?:deg|degree)", t)
    if rot and 0 <= int(rot.group(1)) <= 359:
        slots["orientation_deg"] = int(rot.group(1))

    goals = []
    for k, v in GOAL_WORDS.items():
        if re.search(rf"\b{re.escape(k)}\b", t) and v not in goals:
            goals.append(v)
    # "cold" alone means "make it cooler", but "sardi ... se bachne" —
    # PROTECT FROM cold — is a heating brief; the verb governs direction.
    # Both Hindi word orders occur: verb-final ("sardi se bachne") and the
    # transliterated verb-first form.
    _cold_prot = (r"\b(?:protect|escape|avoid|survive|withstand|handle|cope"
                  r"|bachne|bachao|bachane)\b[^.]{0,18}\b(?:cold|snow|freeze"
                  r"|winter|frost|blizzard|barf|thand|sardi)\w*\b")
    _cold_first = (r"\b(?:cold|snow|freeze|winter|frost|blizzard|barf|thand"
                   r"|sardi)\w*\b[^.]{0,18}\b(?:se\s+bachne|se\s+bachao"
                   r"|bachne|bachao|bachane|protect|escape|avoid|survive)\b")
    if re.search(_cold_prot, t) or re.search(_cold_first, t):
        goals = [g for g in goals if g != "cooling"]
        if "heating" not in goals:
            goals.append("heating")
    elif re.search(r"\b(?:protect|escape|avoid|survive|withstand)\b[^.]{0,18}"
                   r"\b(?:heat|summer|sun|garmi)\w*\b", t):
        goals = [g for g in goals if g != "heating"]
        if "cooling" not in goals:
            goals.append("cooling")
    if goals:
        slots["goals"] = goals

    # Size adjectives set the FOOTPRINT only when they are not describing an
    # opening: "small north windows" (DS_DES_01) must leave the shelter size
    # to occupancy sizing — v4/v5 read "small" globally and shrank the box,
    # contradicting the gold annotation 4.6x4.6. Window-scoped adjectives are
    # handled on their own further below.
    _OPEN = r"(?:windows?|openings?|glaz\w*|vent\w*|khidki)\b"
    def _adj_scoped(word: str) -> bool:
        adj = re.escape(word)
        return bool(re.search(_OPEN + r"[^.;]{0,14}\b" + adj + r"\b|" +
                              r"\b" + adj + r"\b[^.;]{0,14}" + _OPEN, t))
    for _w in ("big", "large", "spacious", "small", "compact", "tiny",
               "minimal"):
        if _adj_scoped(_w):
            break
    else:
        if re.search(r"\b(big|large|spacious)\b", t):
            slots.setdefault("length_m", 5.0)
            slots.setdefault("width_m", 4.0)
        elif re.search(r"\b(small|compact|tiny|minimal)\b", t):
            slots.setdefault("length_m", 3.0)
            slots.setdefault("width_m", 3.0)

    # allow one filler word: "15 displaced people", "8 hardened soldiers"
    people = re.search(r"(\d+)[\s\-]*(?:\w+\s+){0,1}?"
                       r"(?:people|persons?|members|occupants|soldiers?|"
                       r"personnel|troops|jawans|sleepers|adults|children|kids)\b", t)
    if not people:
        # "a family of six" / "household of 4" — _digitise above has already
        # turned spelled-out numbers into digits, so the capture is numeric
        people = re.search(r"(?:family|families|household|group)\s+of\s+(\d+)", t)
    if people:
        n = int(people.group(1))
        if 1 <= n <= 20:
            slots["occupants"] = n
            # 3.5 m2/person — the convention the GOLD CORPUS itself uses
            # (6 occupants -> 4.6x4.6 = 21 m2, 8 -> 28 m2, 15 -> 52 m2),
            # not the ~4.5 m2 in the §3.3 sketch comment. Where the file
            # contradicts itself the hand-annotated data wins: same
            # hierarchy decision as the sloped-roof pitch (25 deg). The
            # engine has no occupancy rule of its own (footprint comes from
            # zone prescriptions), so this remains an explicit fallback the
            # endpoint clamp table still guards (2-15 m).
            area = max(9.0, 3.5 * n)
            side = round(min(max(area ** 0.5, 3.0), 12.0), 1)
            # v4 capped the side at 6.0 m, which quietly broke the very
            # area it cites (15 people need ~52 m², not 36). Now the square
            # keeps the stated area whenever the engine can hold it; the
            # endpoint's clamp table is the single place limits are enforced.
            slots.setdefault("length_m", side)
            slots.setdefault("width_m", side)

    # ---- roof pitch -------------------------------------------------------
    pitch = re.search(r"(\d{1,2})\s*(?:deg|degree)s?\s*(?:pitch|slope|roof)", t)
    if not pitch:
        pitch = re.search(r"(?:pitch|slope)\D{0,10}(\d{1,2})\s*(?:deg|degree)", t)
    if pitch and 0 <= int(pitch.group(1)) <= 60:
        slots["roof_pitch_deg"] = float(pitch.group(1))
    elif re.search(r"\b(steep(?:ly)?\s+(?:pitched|sloped|sloping)|steep roof)\b", t):
        slots["roof_pitch_deg"] = 30.0
    elif re.search(r"\b(sloped?|sloping|pitched|gable|shed|angled)\s+roof\b", t) \
            or re.search(r"\broof\s+(?:that\s+)?slopes?\b", t):
        # 25°: both gold rows with an UNQUALIFIED "sloped roof" (DS_DIS_02,
        # HN_DES_04) annotate 25 for monsoon runoff; v4's 20° contradicted
        # them. Explicit degrees still win (clause above this one).
        slots["roof_pitch_deg"] = 25.0
    elif re.search(r"\bflat\s+roof\b", t):
        slots["roof_pitch_deg"] = 0.0

    # ---- relative changes ("make it bigger") — need a prior design ---------
    # Meaningless on their own; the endpoint applies them to the design from
    # the previous turn, so a conversation can refine instead of restarting.
    rel = {}
    for pat, key, factor in (
        (r"\b(bigger|larger|roomier|more space)\b", "size", 1.25),
        (r"\b(smaller|tighter|more compact)\b", "size", 0.8),
        (r"\b(thicker|beefier)\s+(?:walls?)?", "wall_thickness_m", 1.4),
        (r"\b(thinner)\s+(?:walls?)?", "wall_thickness_m", 0.7),
        (r"\b(more insulation|better insulated|thicker insulation)\b",
         "insulation_thickness_m", 1.6),
        (r"\b(less insulation|thinner insulation)\b",
         "insulation_thickness_m", 0.6),
        (r"\b(taller|higher ceiling)\b", "height_m", 1.15),
        (r"\b(lower ceiling|shorter)\b", "height_m", 0.9),
        (r"\b(more ventilation|more airflow|draughtier)\b", "ach", 1.8),
        (r"\b(less ventilation|tighter envelope)\b", "ach", 0.6),
    ):
        if re.search(pat, t):
            rel[key] = factor
    if rel:
        slots["relative"] = rel

    # ---- ventilation -> air changes per hour (a real engine parameter) ----
    for phrase, ach in VENT_WORDS.items():
        if phrase in t:
            slots["ach"] = ach
            break

    # ---- hazards ----------------------------------------------------------
    hazards = []
    for phrase, tag in HAZARD_WORDS.items():
        if re.search(rf"\b{re.escape(phrase)}s?\b", t) and tag not in hazards:
            hazards.append(tag)
    if hazards:
        slots["hazards"] = hazards

    # ---- windows: negation, count and size --------------------------------
    if re.search(r"\b(no|without|zero)\s+(windows?|glazing|openings?)\b", t):
        # the engine has no "no window" flag; the honest equivalent is the
        # smallest opening it will accept
        slots["window_width_m"] = 0.3
        slots["window_height_m"] = 0.3
        slots["no_windows"] = True
    else:
        big = re.search(r"\b(large|big|wide|generous)\b[^.,;]{0,14}?"
                        r"\b(windows?|openings?|glazing)\b", t)
        small = re.search(r"\b(small|tiny|narrow|minimal)\b[^.,;]{0,14}?"
                          r"\b(windows?|openings?|glazing)\b", t)
        if big:
            slots["window_width_m"], slots["window_height_m"] = 1.8, 1.5
        elif small:
            slots["window_width_m"], slots["window_height_m"] = 0.6, 0.6

    # ---- glazing quality: SHGC is a real engine input ----------------------
    if re.search(r"\bhigh[\s\-]*(?:solar|shgc)\w*\b|solar gain glass|"
                 r"\bcatch (?:the )?sun\b|dhoop lene", t):
        slots["window_shgc"] = 0.85
    elif re.search(r"\blow[\s\-]*(?:solar|shgc)\w*\b|avoid (?:the )?sun|"
                   r"shade\w* (?:from )?(?:the )?sun|reduce glare", t):
        slots["window_shgc"] = 0.4

    # ---- glazing U-value spoken as a number (gold MD_ENV_18) --------------
    # only an explicit figure is taken; "double glazed low-E" alone names a
    # product, not a value, and inventing 1.8 would be a fabricated number
    uval = (re.search(r"\bu[\s\-]?value\s*(?:to|of|=|at)?\s*(\d+(?:\.\d+)?)", t)
            or re.search(r"(\d+(?:\.\d+)?)\s*(?:w/m2k|w/m[²2]k)\b", t))
    if uval:
        v = float(uval.group(1))
        if 0.5 <= v <= 12:
            slots["window_u_w_m2k"] = v

    # ---- ventilation rate stated as a number (an engine input) -------------
    ach = re.search(r"(\d+(?:\.\d+)?)\s*(?:air[\s\-]*changes?\b|ach\b)", t)
    if not ach:
        # trailing form: "drop ACH to 1", "ventilation rate of 8"
        ach = re.search(r"\bach\b\s*(?:to|of|=|at)?\s*(\d+(?:\.\d+)?)", t)
    if not ach:
        ach = re.search(r"(?:ventilation|airflow|air flow)[^.,;]{0,12}?"
                        r"(?:to|of|=|at)\s*(\d+(?:\.\d+)?)", t)
    if ach:
        v = float(ach.group(1))
        if 0.2 <= v <= 20:
            slots["ach"] = v

    # ---- cold-driver vocabulary the dataset uses ---------------------------
    if re.search(r"\bsub[\s\-]?zero\b|minus\s*\d|below freezing|"
                 r"\bblizzard\w*|frost b\w+", t):
        slots.setdefault("goals", [])
        if "heating" not in slots["goals"]:
            slots["goals"].append("heating")

    # ---- comparison pairs (drives the compare action below) ----------------
    pair = _compare_entities(t)
    if pair:
        slots["compare"] = pair

    # ---- things we could NOT honour, so the UI can say so -----------------
    # Silently ignoring a material the user named is the worst outcome: they
    # believe it was used. Report it instead.
    unknown = []
    for word in ("bamboo", "thatch", "canvas", "tarpaulin", "glass fibre",
                 "ferrocement", "cob", "straw bale", "shipping container"):
        if re.search(rf"\b{re.escape(word)}\b", t):
            unknown.append(word)
    if unknown:
        slots["unsupported_materials"] = unknown
    if re.search(r"(?:budget|under|below|less than)\s*(?:rs\.?|inr|₹)?\s*\d", t):
        slots["budget_mentioned"] = True

    # ---- a place we do not have climate data for --------------------------
    # Worst case is silence: the user names Varanasi, gets a Prayagraj design
    # and is never told. Catch the place-like token and surface it.
    if "site" not in slots:
        _stop = {"the", "a", "an", "my", "our", "this", "that", "winter",
                 "summer", "monsoon", "night", "day", "village", "town",
                 "city", "area", "region", "site", "place", "home", "family",
                 "people", "sale", "rent", "now", "cheap", "hot", "cold"}
        m = re.search(r"\b(?:in|for|at|near|around)\s+(?:the\s+|my\s+|our\s+)?"
                      r"(?:village\s+|town\s+|city\s+|area\s+)?"
                      r"(?:near\s+)?([a-z]{4,20})\b", t)
        if m and m.group(1) not in _stop:
            cand = m.group(1)
            known = any(cand in k or k in cand for k in SITE_ALIASES)
            if not known:
                slots["unknown_place"] = cand
    return slots


def parse(text: str) -> dict:
    """One entry point for the API: classifier + grammar correction + slots.

    The rules can only steer an utterance that CONTAINS the construct (a
    compare pair, a guard phrase, an imperative), never re-label an ordinary
    sentence — so a high-confidence `design` stays design.
    """
    raw = normalise(text)
    # The classifier featurises the RAW text — the training corpus contains
    # code-mixed Hinglish exactly as typed, so translating before classify
    # would feed it a distribution it never saw. The grammar rules and the
    # slot gazetteer are the ones that benefit from the transliteration.
    t = _hinglish(raw)
    intent, confidence, scores = classify(raw)
    note = ""
    g_intent, g_note = grammar(t)
    if g_intent and (g_intent != intent):
        # rules correct; a rule agreeing with the model just annotates
        note = g_note
        intent = g_intent
        confidence = max(confidence, 0.99) if g_intent != "unknown" else confidence
        if g_intent == "unknown":
            confidence = max(confidence, 0.90)
    elif g_note:
        note = g_note
    slots = extract_slots(text)
    return {"intent": intent, "confidence": confidence, "scores": scores,
            "slots": slots, "grammar": note or None}
