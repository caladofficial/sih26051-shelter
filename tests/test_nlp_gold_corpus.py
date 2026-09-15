"""Whole-corpus gate: every one of the 86 hand-annotated audit utterances.

If this fails, a parser or model change regressed real human phrasing —
exactly the class of failure the 99.97% synthetic split hides. The corpus
lives in ml/nlp/data/nlp_gold_v2.jsonl (provenance: docs/nlp_assistant.md)
and is NEVER in the training CSV.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src import nlp_design  # noqa: E402

GOLD = [json.loads(l) for l in
        (ROOT / "ml" / "nlp" / "data" / "nlp_gold_v2.jsonl")
        .read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.mark.parametrize("row", GOLD, ids=lambda r: r["id"])
def test_gold_intent(row):
    got = nlp_design.parse(row["text"])["intent"]
    assert got == row["intent"], f"{got!r} != {row['intent']!r}"


@pytest.mark.parametrize("row", [g for g in GOLD if g["intent"] == "design"])
def test_gold_design_never_loses_the_site(row):
    slots = nlp_design.parse(row["text"])["slots"]
    spoken = [v for v in nlp_design.SITE_ALIASES if v in row["text"].lower()]
    if spoken:
        assert slots.get("site"), f"site named but not extracted: {row['text']}"
