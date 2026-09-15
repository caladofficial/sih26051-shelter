"""Slot-extraction guarantees for the plain-English design assistant.

Every case here was silently dropped before: the sentence parsed, the user
believed their instruction had been honoured, and nothing in the design
actually changed. Silence is the worst failure mode for this feature — a
wrong number is visible, an ignored instruction is not.
"""
from __future__ import annotations

from src import nlp_design


def test_spelled_out_numbers_are_parsed():
    """'a family of six' is far more common than 'a family of 6'."""
    s = nlp_design.extract_slots("we need shelter for a family of six in Leh")
    assert s["occupants"] == 6
    assert s["length_m"] > 3.0          # sized from occupancy, not a default
    d = nlp_design.extract_slots("three by four metres unit in Pune")
    assert (d["length_m"], d["width_m"]) == (3.0, 4.0)


def test_ventilation_maps_to_a_real_engine_input():
    """ach is a genuine RC-engine parameter; the phrase must move it."""
    vented = nlp_design.extract_slots("well ventilated shelter for Chennai")
    sealed = nlp_design.extract_slots("sealed airtight shelter for Chennai")
    assert vented["ach"] > 4.0
    assert sealed["ach"] < 1.0


def test_hazards_are_recognised():
    for text, tag in [("cyclone resistant shelter for Chennai", "cyclone"),
                      ("cheap shelter for Kolkata after the floods", "flood"),
                      ("needs to handle heavy snow load in Dras", "snow")]:
        assert tag in nlp_design.extract_slots(text).get("hazards", []), text


def test_window_negation_and_size():
    none = nlp_design.extract_slots("shelter with no windows for Leh")
    assert none["no_windows"] is True and none["window_width_m"] <= 0.3
    big = nlp_design.extract_slots("large windows facing south in Delhi")
    small = nlp_design.extract_slots("small windows facing north in Delhi")
    assert big["window_width_m"] > small["window_width_m"]


def test_unsupported_material_is_reported_not_substituted():
    s = nlp_design.extract_slots("bamboo hut in Kolkata")
    assert "bamboo" in s.get("unsupported_materials", [])
    assert "wall_material" not in s      # must NOT quietly pick something else


def test_unknown_place_is_flagged():
    """Naming a city we have no data for must not silently use another."""
    assert nlp_design.extract_slots(
        "something for my village near Varanasi")["unknown_place"] == "varanasi"
    # known sites and generic words must not trip it
    assert "unknown_place" not in nlp_design.extract_slots("shelter for Chennai")
    assert "unknown_place" not in nlp_design.extract_slots("a hut in the village")


def test_budget_is_acknowledged_not_invented():
    """No costing data exists, so a budget may be noted but never acted on."""
    s = nlp_design.extract_slots("budget under 50000 rupees for Delhi")
    assert s.get("budget_mentioned") is True
    assert not any(k.startswith("cost") or k.startswith("price") for k in s)


def test_verbless_requests_classify_as_design():
    """'bamboo hut in Kolkata' has no build verb; it used to land in explain."""
    for text in ("bamboo hut in Kolkata",
                 "three by four metres unit in Pune",
                 "needs to handle heavy snow load in Dras"):
        intent, conf, _ = nlp_design.classify(text)
        assert intent == "design", f"{text!r} -> {intent} ({conf:.2f})"


def test_explicit_instruction_still_outranks_a_hazard_preset():
    """A hazard may swap the envelope, but only where the user was silent."""
    s = nlp_design.extract_slots(
        "cyclone shelter for Chennai with stone walls")
    assert s["wall_material"] == "stone"
    assert "cyclone" in s["hazards"]


# --- v4: audit-corpus regressions (each row was a real production miss) -----
def test_hyphenated_aliases_match():
    s = nlp_design.extract_slots("a cool mud-brick shelter for a family of "
                                 "six in Jaipur, small north windows")
    assert s["wall_material"] == "mud_brick"          # mud-brick == mud brick
    assert s["window_wall"] == "north"                 # adjective-before-noun
    assert s["occupants"] == 6


def test_context_prefixes_do_not_leak_across_layers():
    # the wall's stone used to bleed into the roof because normalise() ate the
    # comma: "stone masonry walls, flat roof" -> roof=stone
    s = nlp_design.extract_slots(
        "family dwelling in Pune with stone masonry walls, flat roof")
    assert s["wall_material"] == "stone"
    assert "roof_material" not in s                    # roof material: silence
    assert s["roof_pitch_deg"] == 0.0                  # 'flat roof' IS stated


def test_rcc_slab_roof_still_resolves_through_the_wall_clause():
    s = nlp_design.extract_slots("stone walls, RCC slab roof, 300 mm walls, "
                                 "Srinagar")
    assert s["roof_material"] == "rcc_slab"
    assert s["wall_thickness_m"] == 0.3


def test_thickness_attaches_to_the_word_next_to_the_number():
    s = nlp_design.extract_slots("design a tactical command post in Srinagar "
                                 "with timber frame and 75mm mineral wool")
    assert s["insulation_thickness_m"] == 0.075        # not a wall thickness
    assert s["wall_material"] == "timber"              # timber frame carries…
    assert s["roof_material"] == "timber"              # …both surfaces


def test_insulation_removal_and_trailing_ach():
    s = nlp_design.extract_slots("remove all insulation and drop ACH to 1")
    assert s["insulation_material"] == "none"
    assert s["insulation_thickness_m"] == 0.0
    assert s["ach"] == 1.0


def test_spelled_and_unit_variants():
    s = nlp_design.extract_slots("make the shelter bigger 6 by 5 meters with "
                                 "3 meter ceiling")
    assert (s["length_m"], s["width_m"]) == (6.0, 5.0)
    assert s["height_m"] == 3.0                        # "meters" == "m"
    assert s["relative"]["size"] == 1.25
    s2 = nlp_design.extract_slots("switch wall material to autoclaved "
                                  "aerated concrete and make thickness 0.25 "
                                  "meters")
    assert s2["wall_material"] == "aerated_concrete"
    assert s2["wall_thickness_m"] == 0.25              # bare 'thickness' → wall


def test_occupancy_with_filler_and_hyphens():
    assert nlp_design.extract_slots(
        "deploy an 8-soldier hardened border outpost in Dras")["occupants"] == 8
    assert nlp_design.extract_slots(
        "lightweight plywood dormitory in Mumbai for 15 displaced people"
    )["occupants"] == 15
    assert nlp_design.extract_slots(
        "sentry watch cabin for 2 personnel at 12000 feet elevation in Dras"
    )["occupants"] == 2


def test_sandwich_panels_imply_the_whole_envelope():
    s = nlp_design.extract_slots("rapid assembly bunker with PUF sandwich "
                                 "panels and airtight seals")
    assert s["wall_material"] == "puf_sandwich_panel"
    assert s["roof_material"] == "puf_sandwich_panel"  # panels are both sides
    assert s["ach"] == 0.5                             # "airtight seals"


def test_subzero_is_a_heating_driver():
    s = nlp_design.extract_slots("an outpost in Dras capable of surviving "
                                 "minus 30 subzero blizzards")
    assert "heating" in s.get("goals", [])
    assert "snow" in s.get("hazards", [])


def test_compare_pair_carries_thickness_variants():
    s = nlp_design.extract_slots("compare 50mm EPS versus 100mm EPS in Delhi "
                                 "full year comfort")
    pair = s["compare"]
    assert pair["a"]["insulation_material"] == "eps"
    assert abs(pair["a"]["thickness_m"] - 0.05) < 1e-6
    assert abs(pair["b"]["thickness_m"] - 0.1) < 1e-6
