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
