"""Three-tier entity normalisation for the assistant (roadmap Step 3.3).

Tier 1  fuzzy gazetteer      — typos and OOV spellings ("thermocoal",
          "jaisalmer", "plywod") resolve onto a known key by edit distance.
Tier 2  unit normalisation   — imperial speech ("10 by 12 feet", "9 inch
          brick walls") converts to the metres the engine actually takes.
Tier 3  context resolution   — stays in src/nlp_design.py (wall-vs-roof
          scoping was landed there in v4); this module only supplies the
          canonical lookup that Tier 3 calls.

Every resolved value carries provenance: callers get the original token back
so the UI can SAY "read 'thermocoal' as EPS" instead of silently guessing.
No dependency beyond the stdlib — this runs identically on Vercel, in tests,
and as the source of truth for the offline JS mirror.
"""
from __future__ import annotations

import re

__all__ = ["edit_distance", "fuzzy_lookup", "normalize_units"]


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance, two-row DP (keys are short, so this is trivial)."""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _allowed_dist(word: str) -> int:
    """Distance budget scales with word length; short words stay exact.

    Two errors in a four-letter word ("brick"→"birck" ok, but "ply"→"eps"
    must never fire) would let unrelated materials collide.
    """
    n = len(word)
    if n <= 4:
        return 0
    if n <= 7:
        return 1
    return 2


def fuzzy_lookup(text: str, table: dict[str, str]
                 ) -> tuple[str, str, str] | None:
    """Match `text` against the keys of `table` allowing small edit distance.

    Compares contiguous word runs (up to 3 words) against every key,
    including multiword ones ("thermo coal" / "rockwool" vs "rock wool").
    Returns (canonical_value, matched_key, typed_span) or None.

    Deliberately strict:
      * the length budget `_allowed_dist` keeps 3-4 letter words exact, so
        short unrelated tokens can never collide;
      * when two different keys tie at the best distance the lookup REFUSES
        rather than guessing — an ambiguous typo must fall through to the
        existing "not in the materials table" honesty path.
    """
    words = text.split()
    if not words:
        return None
    # Typos are single-token events in practice, so SINGLE words are matched
    # against single-word keys (a |Δlen| pre-filter keeps the DP off every
    # pair that could not qualify); multi-word keys are compared against the
    # whole text only. Cost per sentence: microseconds, not milliseconds —
    # this runs inside every /api/nlp/design parse.
    scored: list[tuple[int, str, str, str]] = []
    for key, value in table.items():
        budget = _allowed_dist(key)
        if " " in key:
            if abs(len(text) - len(key)) > 3:
                continue
            d, span = edit_distance(text, key), text
        else:
            d, span = 10 ** 6, ""
            for w in words:
                if abs(len(w) - len(key)) > budget:
                    continue
                dd = edit_distance(w, key)
                if dd < d:
                    d, span = dd, w
        if d <= budget:
            scored.append((d, key, value, span))
    if not scored:
        return None
    scored.sort(key=lambda s: s[0])
    best = scored[0]
    if best[0] > 0 and len(scored) > 1 and scored[1][0] == best[0]:
        return None                        # ambiguous typo -> refuse
    return best[2], best[1], best[3]


# --------------------------------------------------------------------------
# Tier 2: imperial units -> engine metres
# --------------------------------------------------------------------------
FT_M = 0.3048
IN_M = 0.0254

#: "10 by 12 ft", "10x12feet", "10 x 12 ft"
_RE_PAIR_FT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:by|x|×)\s*(\d+(?:\.\d+)?)\s*(feet|foot|ft)\b")
#: "a 9 inch brick wall" / "walls 9 inches"
_RE_INCH = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:\"|inches|inch)\b")
#: "ceiling 9 ft", "height 10 feet" — single dimension
_RE_SINGLE_FT = re.compile(
    r"(?:height|ceiling)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*(feet|foot|ft)\b")


def normalize_units(text: str) -> tuple[str, dict]:
    """Rewrite imperial dimensions in `text` into metric equivalents.

    Returns (rewritten_text, notes) where rewritten_text keeps the original
    phrasing shape (same slot grammar downstream just sees metres) and notes
    records each conversion for the honesty trail. Only whole measurements
    with an explicit imperial unit word are touched — nothing is inferred.
    """
    notes: dict[str, str] = {}

    def _pair(m: re.Match) -> str:
        a, b = float(m.group(1)), float(m.group(2))
        am, bm = round(a * FT_M, 2), round(b * FT_M, 2)
        notes["length_m"] = f"{a} ft -> {am} m"
        notes["width_m"] = f"{b} ft -> {bm} m"
        return f"{max(am, bm)} by {min(am, bm)} meters"

    def _inch(m: re.Match) -> str:
        v = float(m.group(1))
        mm = round(v * 25.4)
        notes["inch"] = f"{v} in -> {mm} mm"
        return f"{mm}mm "

    def _single(m: re.Match) -> str:
        v = float(m.group(1))
        mv = round(v * FT_M, 2)
        notes["height_m"] = f"{v} ft -> {mv} m"
        return f"height {mv} meters"

    t = _RE_PAIR_FT.sub(_pair, text)
    t = _RE_SINGLE_FT.sub(_single, t)
    t = _RE_INCH.sub(_inch, t)
    return t, notes
