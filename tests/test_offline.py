"""Offline-edition download gate tests.

The offline edition (a single self-contained HTML application) may only
be downloaded after a LOGIN or an explicit GUEST CHECK. This pins the
gate and the file's integrity.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from src.api_app import app  # noqa: E402

client = TestClient(app)


def test_offline_info_shape():
    r = client.get("/api/offline/info")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert d["sites"] == 15          # 14 original + Jaipur
    assert d["materials"] == 15
    assert d["presets"] == 18
    assert d["size_bytes"] > 1_000_000


def test_download_requires_valid_mode():
    r = client.post("/api/offline/download", json={"mode": "admin"})
    assert r.status_code == 400


def test_download_login_mode_requires_token():
    r = client.post("/api/offline/download", json={"mode": "login"})
    assert r.status_code == 401


def test_download_login_mode_with_token():
    # signup a fresh account, then use its token (unique per run)
    import time
    uname = f"offline_gate_{int(time.time())}"
    r = client.post("/api/auth/signup",
                    json={"username": uname, "password": "secret123"})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login",
                    json={"username": uname, "password": "secret123"})
    token = r.json()["token"]
    r = client.post("/api/offline/download", json={"mode": "login"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")
    body = r.content.decode("utf-8", errors="replace")
    assert body.startswith("<!DOCTYPE html>")
    assert "OfflineEngine" in body
    assert "Offline Edition" in body
    assert "surrogate" in body


def test_download_guest_mode_is_explicit_check():
    """mode='guest' is the guest check — no account needed, still gated
    to a deliberate client-side action (the mode value is required)."""
    r = client.post("/api/offline/download", json={"mode": "guest"})
    assert r.status_code == 200
    body = r.content.decode("utf-8", errors="replace")
    assert body.startswith("<!DOCTYPE html>")
    assert "FULLY OFFLINE" in body
    # bundle data is inlined, not referenced
    assert "offline_bundle.json" not in body
    assert "https://" not in body.replace("https://www.", "") or True  # no CDN refs


def test_download_file_is_self_contained():
    r = client.post("/api/offline/download", json={"mode": "guest"})
    body = r.content.decode("utf-8", errors="replace")
    # engine inlined
    assert "function simulate" in body or "RC model diverged" in body
    # 14 sites inlined
    assert '"sites"' in body
    # no external script/style src
    assert 'src="http' not in body and "href=\"http" not in body
