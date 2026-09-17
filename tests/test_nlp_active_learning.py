"""Step 3.6 active learning: collection is automatic, weights are gated.

Covers the three pieces that must agree: the store (feedback rows survive a
round trip), the stats endpoint (the backlog is countable), and the retrain
script's corpus builder (confirmed rows accepted, prose corrections NOT
auto-labelled, gold rows never merged).
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

client = None


@pytest.fixture(scope="module", autouse=True)
def _client():
    global client
    from fastapi.testclient import TestClient
    from api.index import app
    client = TestClient(app)
    return client


def test_feedback_round_trip_and_stats():
    verdict = {"text": "give me a cool shed in Jaipur with mud walls",
               "intent": "design", "confidence": 0.91, "slots": {"site": "Jaipur"},
               "design": {"length_m": 4.0}, "correct": True, "correction": None}
    r = client.post("/api/nlp/feedback", json=verdict)
    assert r.status_code == 200 and r.json()["stored"] is True
    s = client.get("/api/nlp/feedback/stats").json()
    assert s["ok"] and s["total"] >= 1
    assert s["threshold"] == 100
    assert "retrain_recommended" in s


def _load_retrain():
    spec = importlib.util.spec_from_file_location(
        "retrain_nlp", ROOT / "scripts" / "retrain_nlp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_politeness_variants_keep_the_label():
    m = _load_retrain()
    vs = m.politeness_variants("Design a Stone Shelter in Leh")
    assert "design a stone shelter in leh" in vs
    assert "please design a stone shelter in leh" in vs
    # no variant may change intent-bearing words — only wrappers
    for v in vs:
        assert v.lower().strip(".!").removeprefix("please ").removeprefix(
            "can you ").rstrip(".") in ("design a stone shelter in leh",)


def test_gold_rows_are_never_merged(tmp_path, monkeypatch):
    m = _load_retrain()
    gold_first = json.loads((ROOT / "ml" / "nlp" / "data" / "nlp_gold_v2.jsonl")
                             .read_text(encoding="utf-8").splitlines()[0])
    csv_path = tmp_path / "real_user_feedback.csv"
    csv_path.write_text(
        "created_at,text,intent,confidence,correct,correction\n"
        f'2026-09-17,"{gold_first["text"]}",design,0.9,true,\n'          # refused
        "2026-09-17,give me a cool shed in Jaipur,design,0.9,true,\n"    # accepted
        "2026-09-17,whatever that was,design,0.4,false,"
        "it should compare two materials instead\n",                      # prose -> skipped
        encoding="utf-8")
    monkeypatch.setattr(m, "FEEDBACK_CSV", csv_path)
    add, stats = m.build_addendum()
    texts = [t for t, _ in add]
    assert not any(gold_first["text"].lower() in t.lower() for t in texts)
    assert stats["accepted_confirm"] == 1
    # 2 skipped: the gold row (benchmark contamination refused) AND the
    # prose correction — neither is auto-labelled
    assert stats["skipped"] == 2
