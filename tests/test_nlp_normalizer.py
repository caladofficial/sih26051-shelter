"""Step 3.3 normalisation tiers — fuzzy gazetteer + imperial units (v5).

These cover the audit's "language brittleness" finding from the OPPOSITE
side of the fuzz: the parser may now repair typos, but only within a
deterministic budget, never on short words, and never when two candidates
tie — silence beats a coin-flip.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src import nlp_design as nd            # noqa: E402
from src import nlp_normalizer as nrm       # noqa: E402


# --------------------------- Tier 1: fuzzy -------------------------------
def test_edit_distance_basics():
    assert nrm.edit_distance("", "") == 0
    assert nrm.edit_distance("kitten", "sitting") == 3
    assert nrm.edit_distance("same", "same") == 0


def test_fuzzy_resolves_material_typos():
    hit = nrm.fuzzy_lookup("thermocoal", nd.INS_ALIASES)
    assert hit and hit[0] == "eps"
    hit = nrm.fuzzy_lookup("jaysalmer", nd.SITE_ALIASES)
    assert hit and hit[0] == "Jaisalmer"


def test_fuzzy_refuses_ambiguous_ties():
    # equidistant from two keys -> REFUSE; the endpoint's honesty path then
    # reports "not in the materials table" instead of guessing
    assert nrm.fuzzy_lookup("roxk wool",
                            {"rock wool": "mineral_wool",
                             "foxk wool": "sheep_wool"}) is None
    # ≤4-char keys are exact-only: 'eps' must never fuzzy-accept 'epz'
    assert nrm.fuzzy_lookup("epz", {"eps": "eps"}) is None


def test_fuzzy_budget_blocks_unrelated_words():
    assert nrm.fuzzy_lookup("happiness", nd.WALL_ALIASES) is None


def test_parse_fuzzy_records_provenance():
    p = nd.parse("design a shelter with thermocoal insulation in jaysalmer")
    assert p["slots"].get("site") == "Jaisalmer"
    assert p["slots"].get("insulation_material") == "eps"
    fr = p["slots"].get("fuzzy_read") or {}
    assert "thermocoal" in fr or "jaysalmer" in fr   # typed form recorded


# --------------------------- Tier 2: units -------------------------------
def test_feet_pair_to_metres():
    t, notes = nrm.normalize_units("a 10 by 12 feet shelter")
    assert "3.66 by 3.05 meters" in t
    assert notes["length_m"].startswith("10.0 ft")


def test_inch_thickness_flows_into_wall_slot():
    p = nd.parse("change walls to 9 inch brick")
    s = p["slots"]
    assert s.get("wall_thickness_m") == pytest.approx(0.2286, abs=1e-3)
    assert s.get("wall_material") == "brick"


def test_height_in_feet():
    p = nd.parse("make the ceiling 9 ft")
    assert p["slots"].get("height_m") == pytest.approx(2.74, abs=0.01)
    assert "height_m" in (p["slots"].get("unit_conversions") or {})


def test_metric_phrasing_untouched():
    # conversions must fire ONLY on explicit imperial unit words
    t, notes = nrm.normalize_units("walls 300mm brick, height 2.6 meters")
    assert notes == {}
    assert "300mm" in t


# --------------------------- expanded gazetteer ---------------------------
@pytest.mark.parametrize("phrase,want", [
    ("kaccha brick walls", "mud_brick"),
    ("dhajji dewari envelope", "rammed_earth"),
    ("blueboard insulation", "xps"),
    ("kota stone walls", "stone"),
    ("chadar roof", "gi_sheet"),
])
def test_new_synonyms_land(phrase, want):
    s = nd.extract_slots(f"design a shelter with {phrase}")
    got = s.get("wall_material") or s.get("roof_material") \
        or s.get("insulation_material")
    assert got == want


@pytest.mark.parametrize("phrase,want", [
    ("secunderabad field unit", "Hyderabad"),
    ("indus valley outpost", "Leh"),
    ("sangam camp site", "Prayagraj"),
    ("kashmir valley command post", "Srinagar"),
])
def test_location_synonyms_from_the_file(phrase, want):
    s = nd.extract_slots(f"design a {phrase}")
    assert s.get("site") == want


# --------------------------- glazing U-value ------------------------------
def test_u_value_only_when_spoken():
    assert nd.extract_slots("switch window U-value to 1.8 double glazed")\
        .get("window_u_w_m2k") == 1.8
    # no number spoken -> no invented value
    assert "window_u_w_m2k" not in nd.extract_slots(
        "use double glazed low-E windows")
