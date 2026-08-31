"""Hardening tests: shipped frontend is obfuscated, internal docs/schema hidden.

These assert the *inspection-resistance* contract of the deployed app:
  * public/ contains only built artifacts (obfuscated JS, minified CSS/HTML)
  * readable sources live in public-src/ which never deploys
  * internal docs/ never deploys
  * the production API exposes no OpenAPI schema (/docs, /openapi.json, /redoc)
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_js_is_obfuscated():
    src = (ROOT / "public-src" / "app.js").read_text()
    built = (ROOT / "public" / "app.js").read_text()
    # built artifact must exist and not be a copy of the source
    assert built and built != src
    # obfuscator signature: hexadecimal mangled identifiers (a0_0x8c23e2 …)
    assert re.search(r"[A-Za-z_$][A-Za-z0-9_$]*0x[0-9a-f]{2,}", built), \
        "built app.js does not look obfuscated (no hex identifiers)"
    # no readable section comments survive
    assert "/*" not in built and "// ====" not in built
    # every DOM id used by the source still exists as a runtime string
    ids = set(re.findall(r"\$\"([A-Za-z][A-Za-z0-9]*)\"", src))
    for i in ids:
        assert i in built, f"DOM id {i} lost during obfuscation"


def test_shipped_html_css_are_minified():
    for page in ("index.html", "dashboard.html", "login.html"):
        html = (ROOT / "public" / page).read_text()
        assert "<!--" not in html          # comments stripped
    assert 'id="view3d"' in (ROOT / "public" / "dashboard.html").read_text()
    assert 'id="heroStage"' in (ROOT / "public" / "index.html").read_text()
    assert 'id="authForm"' in (ROOT / "public" / "login.html").read_text()
    css = (ROOT / "public" / "style.css").read_text()
    assert "/*" not in css
    assert (ROOT / "public" / "style.css").stat().st_size < \
        (ROOT / "public-src" / "style.css").stat().st_size


def test_vercelignore_excludes_sources_and_docs():
    vi = (ROOT / ".vercelignore").read_text()
    assert "public-src" in vi, "readable sources must never deploy"
    assert "docs" in vi, "internal docs must never deploy"
    assert "tests" in vi and "scripts" in vi


def test_prod_api_hides_schema():
    code = (
        "import os; os.environ['VERCEL']='1'; "
        "import sys; sys.path.insert(0, %r); "
        "from src.api_app import app; "
        "print(app.docs_url, app.redoc_url, app.openapi_url)" % str(ROOT)
    )
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    docs, redoc, openapi = out.stdout.strip().split()
    assert docs == "None" and redoc == "None" and openapi == "None"


def test_dev_api_keeps_schema():
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from src.api_app import app; "
        "print(app.docs_url, app.redoc_url, app.openapi_url)" % str(ROOT)
    )
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    docs, redoc, openapi = out.stdout.strip().split()
    assert docs == "/docs" and redoc == "/redoc" and openapi == "/openapi.json"
