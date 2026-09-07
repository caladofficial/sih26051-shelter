"""Build obfuscated/minified frontend artifacts from readable sources.

    Sources  (readable, NEVER deployed):  public-src/   <- edit these
    Artifacts (obfuscated, DEPLOYED):     public/       <- never edit by hand

Why two trees? The deployed site should not hand visitors a readable
implementation of the app logic. `public/` is what Vercel ships (and the only
thing any visitor can download). `public-src/` stays in the repo so the
project remains maintainable — edit sources, rebuild, commit both trees.

Obfuscation profile (deliberately conservative — no self-defending traps,
no control-flow flattening, no dead code): identifier mangling (hex),
base64 string-array encoding, comment stripping. The app's behaviour and
DOM contract are unchanged; only readability is removed.

Tooling is installed once into /tmp/febuild (outside the repo):

    npm install --prefix /tmp/febuild javascript-obfuscator@4.1.1 \
        csso-cli@4.0.2 html-minifier-terser@7.2.0

Usage:  python scripts/build_frontend.py   (run from repo root)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "public-src"
OUT = ROOT / "public"
TOOLS = Path("/tmp/febuild")
NODE_BIN = TOOLS / "node_modules" / ".bin"

OBF_CONFIG = {
    "compact": True,
    "identifierNamesGenerator": "hexadecimal",
    "renameGlobals": True,
    "stringArray": True,
    "stringArrayEncoding": ["base64"],
    "stringArrayThreshold": 0.9,
    "rotateStringArray": True,
    "shuffleStringArray": True,
    "selfDefending": False,
    "debugProtection": False,
    "controlFlowFlattening": False,
    "deadCodeInjection": False,
    "transformObjectKeys": False,
    "unicodeEscapeSequence": False,
    "numbersToExpressions": False,
    "simplify": True,
    "sourceMap": False,
    "comments": False,
}


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def ensure_tools() -> None:
    if (NODE_BIN / "javascript-obfuscator").exists():
        return
    print("[build] installing obfuscation tooling into /tmp/febuild (one-time)…")
    run(["npm", "install", "--prefix", str(TOOLS), "--no-audit", "--no-fund",
         "javascript-obfuscator@4.1.1", "csso-cli@4.0.2",
         "html-minifier-terser@7.2.0"])


JS_FILES = ("app.js", "auth.js", "home.js", "login.js")
HTML_FILES = ("index.html", "dashboard.html", "login.html")
CSS_FILES = ("style.css",)


def build_js() -> None:
    cfg = TOOLS / "obf.json"
    cfg.write_text(json.dumps(OBF_CONFIG))
    for name in JS_FILES:
        print(f"[build] {name}  {SRC/name} -> {OUT/name}")
        run([str(NODE_BIN / "javascript-obfuscator"),
             str(SRC / name), "--output", str(OUT / name),
             "--config", str(cfg)])
        run(["node", "--check", str(OUT / name)])   # syntax gate


def build_css() -> None:
    for name in CSS_FILES:
        print(f"[build] {name} {SRC/name} -> {OUT/name}")
        run([str(NODE_BIN / "csso"), str(SRC / name),
             "--output", str(OUT / name)])


def build_static() -> None:
    """Copy small verbatim assets (favicon, data bundles) into public/."""
    for name in ("favicon.svg",):
        src = SRC / name
        if src.exists():
            (OUT / name).write_bytes(src.read_bytes())
            print(f"[build] {name} copied verbatim")
    # climate fallback bundle: canonical copy lives in src/data (it must ship
    # inside the serverless function); mirror it to public/data for the CDN
    bundle = ROOT / "src" / "data" / "climate_bundle.json"
    if bundle.exists():
        (OUT / "data").mkdir(exist_ok=True)
        (OUT / "data" / bundle.name).write_bytes(bundle.read_bytes())
        print(f"[build] data/{bundle.name} {bundle.stat().st_size:,} bytes")


def build_fonts() -> None:
    """Self-hosted typefaces: copy woff2 files verbatim (never CDN)."""
    src, out = SRC / "fonts", OUT / "fonts"
    if not src.is_dir():
        return
    out.mkdir(exist_ok=True)
    for f in sorted(src.glob("*.woff2")):
        (out / f.name).write_bytes(f.read_bytes())
        print(f"[build] fonts/{f.name} {f.stat().st_size:,} bytes")


def build_html() -> None:
    for name in HTML_FILES:
        print(f"[build] {name} {SRC/name} -> {OUT/name}")
        run([str(NODE_BIN / "html-minifier-terser"),
             str(SRC / name), "-o", str(OUT / name),
             "--collapse-whitespace", "--remove-comments"])


def main() -> None:
    if not (SRC / "app.js").exists():
        sys.exit(f"no {SRC/'app.js'} — run from repo root")
    OUT.mkdir(exist_ok=True)
    ensure_tools()
    build_js()
    build_css()
    build_fonts()
    build_static()
    build_html()
    for name in JS_FILES + CSS_FILES + HTML_FILES:
        if not (OUT / name).exists():
            sys.exit(f"missing artifact: {OUT/name}")
        a, b = (SRC / name).stat().st_size, (OUT / name).stat().st_size
        print(f"[build] {name}: {a:,} -> {b:,} bytes ({100 * b // max(a, 1)}%)")
    print("[build] done.")


if __name__ == "__main__":
    main()
