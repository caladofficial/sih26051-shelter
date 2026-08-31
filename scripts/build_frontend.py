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


def build_js() -> None:
    cfg = TOOLS / "obf.json"
    cfg.write_text(json.dumps(OBF_CONFIG))
    print(f"[build] app.js  {SRC/'app.js'} -> {OUT/'app.js'}")
    run([str(NODE_BIN / "javascript-obfuscator"),
         str(SRC / "app.js"), "--output", str(OUT / "app.js"),
         "--config", str(cfg)])
    run(["node", "--check", str(OUT / "app.js")])   # syntax gate


def build_css() -> None:
    print(f"[build] style.css {SRC/'style.css'} -> {OUT/'style.css'}")
    run([str(NODE_BIN / "csso"), str(SRC / "style.css"),
         "--output", str(OUT / "style.css")])


def build_html() -> None:
    print(f"[build] index.html {SRC/'index.html'} -> {OUT/'index.html'}")
    run([str(NODE_BIN / "html-minifier-terser"),
         str(SRC / "index.html"), "-o", str(OUT / "index.html"),
         "--collapse-whitespace", "--remove-comments"])


def main() -> None:
    if not (SRC / "app.js").exists():
        sys.exit(f"no {SRC/'app.js'} — run from repo root")
    OUT.mkdir(exist_ok=True)
    ensure_tools()
    build_js()
    build_css()
    build_html()
    for name in ("app.js", "style.css", "index.html"):
        a, b = (SRC / name).stat().st_size, (OUT / name).stat().st_size
        print(f"[build] {name}: {a:,} -> {b:,} bytes ({100 * b // max(a, 1)}%)")
    print("[build] done.")


if __name__ == "__main__":
    main()
