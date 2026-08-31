"""Accounts + shelter fleet tests — signup/login, per-user design scoping,
shelter CRUD with computed thermal metrics. Run:  python -m pytest tests/test_accounts.py -v
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _client():
    global client
    from fastapi.testclient import TestClient
    from api.index import app
    client = TestClient(app)
    return client


_NS = time.time_ns()
USER_A = f"t{_NS}a"
USER_B = f"t{_NS}b"
PASSWORD = "passw0rdX"
DESIGN = {
    "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
    "orientation_deg": 0, "wall_material": "brick", "wall_thickness_m": 0.2,
    "roof_material": "rcc_slab", "roof_thickness_m": 0.12,
    "floor_material": "concrete", "floor_thickness_m": 0.1,
    "insulation_material": "eps", "insulation_thickness_m": 0.05,
    "window_wall": "south", "window_width_m": 1.2, "window_height_m": 1.2,
    "window_sill_m": 0.9, "window_shgc": 0.82, "window_u_w_m2k": 5.8,
}
_created = {"designs": [], "shelters": [], "users": []}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _login(username):
    return client.post("/api/auth/login",
                       json={"username": username, "password": PASSWORD}).json()["token"]


# ------------------------------------------------------------------ auth
def test_signup_login_me():
    r = client.post("/api/auth/signup",
                    json={"username": USER_A, "password": PASSWORD})
    assert r.status_code == 201, r.text
    tok_a = r.json()["token"]
    _created["users"].append(r.json()["user"]["user_id"])
    assert r.json()["user"]["username"] == USER_A

    # duplicate username -> 409
    r2 = client.post("/api/auth/signup",
                     json={"username": USER_A, "password": PASSWORD})
    assert r2.status_code == 409

    # wrong password -> 401
    r3 = client.post("/api/auth/login",
                     json={"username": USER_A, "password": "wrongpass1"})
    assert r3.status_code == 401

    # correct login
    r4 = client.post("/api/auth/login",
                     json={"username": USER_A, "password": PASSWORD})
    assert r4.status_code == 200
    tok_b = r4.json()["token"]

    # me
    r5 = client.get("/api/auth/me", headers=_auth(tok_b))
    assert r5.status_code == 200
    assert r5.json()["user"]["username"] == USER_A
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me",
                      headers=_auth("bogus.token.x")).status_code == 401


def test_signup_validation():
    assert client.post("/api/auth/signup",
                       json={"username": "ab", "password": PASSWORD}).status_code == 422
    assert client.post("/api/auth/signup",
                       json={"username": "okname!", "password": PASSWORD}).status_code == 400
    assert client.post("/api/auth/signup",
                       json={"username": "okname2", "password": "123"}).status_code == 422


# ------------------------------------------------------- per-user designs
def test_designs_scoped_to_user():
    r = client.post("/api/designs", json={"name": "user-a design",
                                          "design": DESIGN},
                    headers=_auth(_login(USER_A)))
    assert r.status_code == 200, r.text
    did = r.json()["design_id"]
    _created["designs"].append(did)

    # guest list must NOT contain user-a design
    guest = client.get("/api/designs").json()["designs"]
    assert all(d["design_id"] != did for d in guest)

    # user-a list contains it
    tok = _login(USER_A)
    mine = client.get("/api/designs", headers=_auth(tok)).json()["designs"]
    assert any(d["design_id"] == did for d in mine)

    # user-b cannot see or patch/delete it
    rb = client.post("/api/auth/signup",
                     json={"username": USER_B, "password": PASSWORD}).json()
    tok_b = rb["token"]
    _created["users"].append(rb["user"]["user_id"])
    assert all(d["design_id"] != did
               for d in client.get("/api/designs",
                                   headers=_auth(tok_b)).json()["designs"])
    assert client.patch(f"/api/designs/{did}", json={"favorite": True},
                        headers=_auth(tok_b)).status_code == 404
    assert client.delete(f"/api/designs/{did}",
                         headers=_auth(tok_b)).status_code == 404

    # owner can favorite it
    assert client.patch(f"/api/designs/{did}", json={"favorite": True},
                        headers=_auth(tok)).status_code == 200
    fav = client.get("/api/designs", params={"favorite_only": "true"},
                     headers=_auth(tok)).json()["designs"]
    assert any(d["design_id"] == did for d in fav)

    # owner deletes
    assert client.delete(f"/api/designs/{did}",
                         headers=_auth(_login(USER_A))).status_code == 200
    assert client.get(f"/api/designs/{did}",
                      headers=_auth(tok)).status_code == 404


# -------------------------------------------------------------- shelters
def test_shelters_crud_and_metrics():
    tok = _login(USER_A)
    r = client.post("/api/shelters", json={
        "name": "test shelter", "location_name": "Prayagraj",
        "latitude": 25.4358, "longitude": 81.8463,
        "design": DESIGN, "status": "planned", "notes": "pytest"},
        headers=_auth(tok))
    assert r.status_code == 200, r.text
    row = r.json()
    shid = row["shelter_id"]
    _created["shelters"].append(shid)
    assert row["design"]["length_m"] == 3.0
    assert row["metrics"] is not None, "expected computed thermal metrics"
    assert "mean_indoor_c" in row["metrics"]

    # status validation
    assert client.patch(f"/api/shelters/{shid}", json={"status": "nope"},
                        headers=_auth(tok)).status_code == 400

    # deploy
    r2 = client.patch(f"/api/shelters/{shid}",
                      json={"status": "deployed"}, headers=_auth(tok))
    assert r2.status_code == 200
    assert r2.json()["status"] == "deployed"

    # scoping: guest cannot see it
    guest = client.get("/api/shelters").json()["shelters"]
    assert all(s["shelter_id"] != shid for s in guest)

    # bad status on create
    assert client.post("/api/shelters", json={"name": "x",
                                              "status": "weird"},
                       headers=_auth(tok)).status_code == 400

    # owner deletes
    assert client.delete(f"/api/shelters/{shid}",
                         headers=_auth(tok)).status_code == 200
    assert client.get(f"/api/shelters/{shid}",
                      headers=_auth(tok)).status_code == 404


# ---------------------------------------------------------------- teardown
def teardown_module():
    from src.db.store import Store
    store = Store()
    for did in _created["designs"]:
        store.delete_design(did)
    for shid in _created["shelters"]:
        store.delete_shelter(shid)
    for uid in _created["users"]:
        store.delete_user(uid)
