"""Feedback loop (/api/nlp/feedback) — the real-phrasing corpus.

Rows posted by the "did I understand you correctly?" widget feed migration
0007's nlp_feedback table and later retraining runs. These tests pin the
contract: the endpoint always answers 200 with a stored flag (it must never
break the UI), validates the payload, and round-trips through the Store in
both sqlite and (mocked) REST modes.
"""
import sys
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


def test_feedback_roundtrip_via_api():
    r = client.post("/api/nlp/feedback", json={
        "text": "a cool mud brick shelter for a family of six in Jaipur",
        "intent": "design", "confidence": 0.98,
        "slots": {"site": "Jaipur", "wall_material": "mud_brick"},
        "design": {"length_m": 4.0},
        "correct": True,
    })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "stored": True}

    # the row is retrievable through the same Store the endpoint writes to
    from api.index import app
    store = app.state.store if hasattr(app.state, "store") else None
    if store is None:
        from src import api_app
        store = api_app.STORE
    rows = store.list_nlp_feedback(limit=10)
    assert rows, "feedback row did not persist"
    top = rows[0]
    assert top["text"] == "a cool mud brick shelter for a family of six in Jaipur"
    assert top["correct"] is True
    assert top["slots"]["site"] == "Jaipur"


def test_feedback_rejects_empty_text():
    r = client.post("/api/nlp/feedback", json={"text": "", "correct": True})
    assert r.status_code == 422


def test_feedback_correction_only_when_not_understood():
    r = client.post("/api/nlp/feedback", json={
        "text": "throw up something quick for the flood camp",
        "intent": "design", "confidence": 0.71, "correct": False,
        "correction": "I meant a rapid-deploy shelter, not a permanent one",
    })
    assert r.status_code == 200
    assert r.json()["stored"] is True

    from src import api_app
    rows = api_app.STORE.list_nlp_feedback(limit=5)
    assert rows[0]["correct"] is False
    assert "rapid-deploy" in rows[0]["correction"]


def test_store_sqlite_direct(tmp_path):
    """Store.save/list_nlp_feedback works on a bare sqlite DB too."""
    from src.db.store import Store
    store = Store(db_path=tmp_path / "fb.db")
    store.save_nlp_feedback({"text": "make it bigger", "intent": "modify",
                             "confidence": 0.9, "correct": False,
                             "correction": "increase floor area"})
    rows = store.list_nlp_feedback()
    assert len(rows) == 1
    assert rows[0]["intent"] == "modify"
    assert rows[0]["correct"] is False
