"""The offline JS NLP must MATCH the server classifier, or it doesn't ship.

scripts/check_nlp_edge_parity.py compares the two implementations over a
sampled slice of the training corpus plus hand-authored edge shapes; the
intent-parity gate there is >= 0.999. This test simply runs that gate in CI
(smaller sample), and skips when node is unavailable — the browser bundle is
generated, so the check belongs at build/test time, never at runtime.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not available")


def test_edge_parity_gate():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_nlp_edge_parity.py"),
         "--n", "800"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300)
    print(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr[-800:]
    assert "PASS" in proc.stdout


def test_bundle_nlp_block_shape_if_bundle_present():
    p = ROOT / "src" / "data" / "offline_bundle.json"
    if not p.exists():
        pytest.skip("offline bundle not built in this checkout")
    import json
    b = json.loads(p.read_text(encoding="utf-8"))
    if "nlp" not in b:
        pytest.skip("bundle predates v5 — rebuild with build_offline_bundle.py")
    n = b["nlp"]
    assert len(n["coef"]) == len(n["labels"])
    assert len(n["coef"][0]) == n["n_buckets"] == 4096
    assert "undo" in n["guards"] and "inject" in n["guards"]
    assert "Jaisalmer" in set(n["sites"].values())


def test_engine_js_exposes_nlp():
    src = (ROOT / "offline" / "engine.js").read_text(encoding="utf-8")
    assert "OfflineNLP" in src or "nlp: NLP" in src
    assert "0x01000193" in src          # FNV prime — the hash must be mirrored
