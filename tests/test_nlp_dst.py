"""Step 3.4 conversation state — server-side turns, undo, and Step 3.5 clamps.

Undo matters because the assistant can now be told to do things in sequence;
without a stack, a wrong "make the walls stone" is permanent until the user
rebuilds every other choice by hand. The tests drive the REAL endpoint.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src import dst_manager  # noqa: E402

client = None


@pytest.fixture(scope="module", autouse=True)
def _client():
    global client
    from fastapi.testclient import TestClient
    from api.index import app
    client = TestClient(app)
    dst_manager.reset()
    return client


PRAY = {"lat": 25.45, "lon": 81.85}


def test_undo_round_trip_through_the_endpoint():
    r1 = client.post("/api/nlp/design", json={
        "text": "a cool shelter for Jaipur with thick mud walls",
        **PRAY})
    assert r1.status_code == 200
    sid = r1.json()["dialogue"]["session"]
    assert r1.json()["dialogue"]["turn"] == 1

    r2 = client.post("/api/nlp/design", json={
        "text": "change walls to stone", "session_id": sid, **PRAY})
    d2 = r2.json()
    assert d2["dialogue"]["turn"] == 2
    assert d2["design"]["wall_material"] == "stone"

    r3 = client.post("/api/nlp/design", json={
        "text": "undo that", "session_id": sid, **PRAY})
    d3 = r3.json()
    assert d3["kind"] == "undo" and d3["actionable"]
    # back to what turn 1 left on screen
    assert d3["design"]["wall_material"] == r1.json()["design"]["wall_material"]
    assert any("undid" in a.lower() for a in d3["applied"])
    # and the undo response carries its OWN verified numbers
    assert d3.get("metrics") and "max_indoor_c" in d3["metrics"]


def test_undo_without_history_is_said_not_faked():
    r = client.post("/api/nlp/design", json={"text": "undo", "session_id": "nope"})
    d = r.json()
    assert d["actionable"] is False
    assert "nothing to undo" in d["message"].lower()


def test_undo_recognised_by_grammar_variants():
    from src import nlp_design as nd
    for t in ("undo that", "revert the last change", "roll it back",
              "back to the previous design", "take it back"):
        g, n = nd.grammar(nd.normalise(t))
        assert (g, n) == ("modify", "grammar:undo"), t


def test_dst_manager_history_depth():
    dst_manager.reset()
    sid = dst_manager.open_session("t-depth", None)
    for i in range(15):
        dst_manager.record_turn(sid, f"t{i}", {"wall_material": f"m{i%3}"}, [])
    s = dst_manager.state(sid)
    assert s["undo_depth"] == 10          # capped at HISTORY_DEPTH
    assert s["turn"] == 15
    restored = dst_manager.pop_undo(sid)
    # history stores the PRE-turn state each record: the last push held
    # turn 14's design (m13, since 13 % 3 == 1), and undo returns that
    assert restored["design"]["wall_material"] == "m1"
    assert restored["undid_text"] == "t14"


def test_session_seeded_from_client_context():
    dst_manager.reset()
    seed = {"wall_material": "plywood", "length_m": 4.0}
    sid = dst_manager.open_session("t-seed", seed)
    st = dst_manager.state(sid)
    assert st["session"] == "t-seed"
    # a first turn is recorded ON TOP of the seed, so undo returns the seed
    dst_manager.record_turn(sid, "bigger", {"wall_material": "plywood",
                                            "length_m": 5.0}, ["length x1.25"])
    back = dst_manager.pop_undo(sid)
    assert back["design"]["length_m"] == 4.0


# --------------------------- Step 3.5 clamps ------------------------------
def test_clamp_reports_instead_of_silently_editing():
    from src.api_app import _nlp_clamp
    d, notes = _nlp_clamp({"length_m": 30.0, "ach": 0.1,
                           "insulation_thickness_m": 0.5})
    assert d["length_m"] == 15.0 and d["ach"] == 0.5 \
        and d["insulation_thickness_m"] == 0.25
    assert len(notes) == 3 and all("engine-safe" in n for n in notes)


def test_clamp_leaves_valid_engine_state_alone():
    from src.api_app import _nlp_clamp
    import json as _json
    pres = _json.loads((ROOT / "src" / "data" / "shelter_presets.json")
                       .read_text(encoding="utf-8"))["presets"]
    for pr in pres:
        d = dict(pr.get("design") or {})
        _, notes = _nlp_clamp(d)
        assert not notes, (pr["id"], notes)   # every shipped preset fits


def test_endpoint_clamps_extreme_request():
    r = client.post("/api/nlp/design", json={
        "text": "design a 40 by 30 meter warehouse in Delhi", **PRAY})
    assert r.status_code == 200
    d = r.json()
    assert d["design"]["length_m"] == 15.0 and d["design"]["width_m"] == 15.0
    assert any("engine-safe" in a for a in d["applied"])
