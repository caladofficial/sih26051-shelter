#!/usr/bin/env python3
"""Active-learning retraining driver (roadmap Step 3.6).

Pipeline the dataset doc prescribes, executed with the same discipline as
every other model change in this repo — and without the LLM annotator, since
the project rules are zero-network, zero-API-key and zero invented labels:

  1. Export production feedback rows (ml/nlp/import_feedback.py) — the real
     phrasings the synthetic generator never produced.
  2. Build a merged corpus = base dataset + accepted feedback rows:
       * rows the user CONFIRMED ("Yes, that's it") enter as
         (text, intent-the-system-said) pairs — the user just validated it;
       * rows with a free-text correction enter ONLY when the correction
         states an intent in the project's vocabulary ("intent: compare") —
         we do not guess what a prose correction means;
       * each accepted row is expanded into deterministic paraphrases
         (case / punctuation / politeness variants only — variants that
         cannot change the label for an intent classifier).
       The 86 hand-annotated gold utterances are NEVER merged: they are the
       benchmark. This script asserts that.
  3. Train to a STAGED model file (src/data/.nlp_model_staged.json).
  4. Catastrophic-forgetting gate: score the staged weights on every hold-out
     (hand-written intent set, slot cases, gold corpus via eval_nlp) — the
     new model replaces the live one only if no gate regresses.

Usage:
    python3 scripts/retrain_nlp.py --check          # backlog report only
    python3 scripts/retrain_nlp.py --dry-run        # build corpus, no writes
    python3 scripts/retrain_nlp.py                  # full gated retrain
    python3 scripts/retrain_nlp.py --sqlite DB      # local store instead of Supabase

The 100-correction trigger from the roadmap is REPORTED, not silently
auto-applied: production is a serverless deploy; a human (or a scheduled job
in CI, not in the user-facing app) runs this script after `--check` says the
backlog has crossed the threshold.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DATA_DIR = REPO / "ml" / "nlp" / "data"
BASE_CSV = DATA_DIR / "nlp_dataset.csv"
FEEDBACK_CSV = DATA_DIR / "real_user_feedback.csv"
GOLD = DATA_DIR / "nlp_gold_v2.jsonl"
MODEL = REPO / "src" / "data" / "nlp_model.json"
STAGED = REPO / "src" / "data" / ".nlp_model_staged.json"
MERGED = DATA_DIR / ".retrain_merged.csv"
LABELS = {"design", "modify", "optimize", "compare", "explain", "unknown"}
THRESHOLD = 100            # roadmap: retrain after 100 corrections


def export_feedback(sqlite: str | None) -> bool:
    cmd = [sys.executable, str(REPO / "ml/nlp/import_feedback.py")]
    if sqlite:
        cmd += ["--sqlite", sqlite]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[retrain] feedback export failed: {exc.stderr.strip()[:200]}")
        return False


def politeness_variants(text: str) -> list[str]:
    """Label-safe paraphrases: casing, trailing period, courtesy words."""
    out = [text]
    t = text.strip()
    low = t.lower()
    if low != t:
        out.append(low)
    if not low.endswith("."):
        out.append(low + ".")
    for pfx in ("please ", "can you "):
        v = pfx + low.rstrip(".!")
        if v not in out:
            out.append(v)
    return out


def build_addendum() -> tuple[list[tuple[str, str]], dict]:
    rows = []
    if FEEDBACK_CSV.exists():
        with FEEDBACK_CSV.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    # backlog = every "that's wrong" row carrying a correction, matching
    # /api/nlp/feedback/stats pending_retrain — the TRIGGER counts user
    # demand; ADDING to the corpus separately decides what is label-safe
    stats = {"rows": len(rows), "accepted_confirm": 0,
             "accepted_correction": 0, "skipped": 0,
             "corrections_backlog": sum(
                 1 for r in rows
                 if (r.get("correct") or "").strip().lower()
                 not in ("true", "1", "yes")
                 and (r.get("correction") or "").strip())}
    gold_texts = {json.loads(l)["text"].strip().lower()
                  for l in GOLD.read_text(encoding="utf-8").splitlines()
                  if l.strip()}
    add: list[tuple[str, str]] = []
    for r in rows:
        text = (r.get("text") or "").strip()
        intent = (r.get("intent") or "").strip()
        if not text or intent not in LABELS:
            stats["skipped"] += 1
            continue
        accepted = None
        if (r.get("correct") or "").strip().lower() in ("true", "1", "yes"):
            accepted = intent                      # user validated the parse
        else:
            corr = (r.get("correction") or "").strip().lower()
            for lab in LABELS:                     # explicit correction only
                if corr.startswith(f"intent:{lab}") or f" intent:{lab}" in corr:
                    accepted = lab
                    break
            else:
                # prose corrections are kept as a work queue, not silently
                # turned into labels we would have to guess
                stats["skipped"] += 1
                continue
        if text.lower() in gold_texts:             # never contaminate the benchmark
            stats["skipped"] += 1
            continue
        for v in politeness_variants(text):
            add.append((v, accepted))
        stats["accepted_confirm" if accepted == intent
               else "accepted_correction"] += 1
    return add, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sqlite")
    args = ap.parse_args()

    print(f"[retrain] exporting production feedback (sqlite={args.sqlite or 'supabase'})")
    exported = export_feedback(args.sqlite)
    if not exported and not FEEDBACK_CSV.exists():
        print("[retrain] no feedback source available — using existing export cache")
    add, stats = build_addendum()

    # backlog report (the /api/nlp/feedback/stats mirror, offline)
    neg = stats["rows"] and stats["accepted_correction"] + stats["skipped"]
    print(f"[retrain] feedback rows: {stats['rows']}  "
          f"accepted: {stats['accepted_confirm'] + stats['accepted_correction']} "
          f"(confirmed {stats['accepted_confirm']}, corrected "
          f"{stats['accepted_correction']}), skipped {stats['skipped']}")
    print(f"[retrain] retrain threshold: {stats['corrections_backlog']} corrections "
          f"vs {THRESHOLD} (roadmap 3.6 trigger)")
    if args.check:
        return 0
    if not add:
        print("[retrain] nothing to add — corpus unchanged, model NOT touched")
        return 0

    # merged corpus: base + accepted, gold never merged (asserted)
    if not BASE_CSV.exists():
        print(f"[retrain] missing base corpus {BASE_CSV} — run the generator first")
        return 2
    with BASE_CSV.open(newline="", encoding="utf-8") as fh:
        base = list(csv.reader(fh))
    header, body = base[0], base[1:]
    gold_texts = {json.loads(l)["text"].strip().lower() for l in
                  GOLD.read_text(encoding="utf-8").splitlines() if l.strip()}
    assert not any(t.lower() in gold_texts for t, _ in add), \
        "gold corpus must never enter training"
    seen = {r[0].strip().lower() for r in body}
    merged = [r for r in add if r[0].strip().lower() not in seen]
    print(f"[retrain] merged corpus: {len(body):,} base + {len(merged):,} "
          f"new rows = {len(body) + len(merged):,}")
    if args.dry_run:
        print("[retrain] dry-run — nothing written")
        return 0

    MERGED.write_text("", encoding="utf-8")
    with MERGED.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(body)
        w.writerows(merged)

    # §3.6 forgetting check for SLOTS too: remember the baseline slot F1
    # (from the last measured benchmark) so a swap that quietly degrades
    # extraction cannot pass on intent/compare strength alone.
    bench_path = REPO / "src" / "data" / "nlp_benchmark.json"
    bench_backup = None
    baseline_f1 = None
    if bench_path.exists():
        bench_backup = bench_path.with_suffix(".json.pre-retrain")
        shutil.copy2(bench_path, bench_backup)
        baseline_f1 = (json.loads(bench_path.read_text(encoding="utf-8"))
                       .get("measured") or {}).get("slot_f1")

    # train to a STAGED file first
    print("[retrain] training staged model ...")
    rc = subprocess.run([sys.executable, str(REPO / "ml/nlp/train_nlp.py"),
                         "--data", str(MERGED), "--out", str(STAGED)]).returncode
    if rc:
        print("[retrain] staged training failed")
        return 2

    # forgetting gate: score staged weights the same way eval does
    print("[retrain] running hold-out gates against staged model ...")
    backup = None
    if MODEL.exists():
        backup = MODEL.with_suffix(".json.pre-retrain")
        shutil.copy2(MODEL, backup)
    try:
        shutil.copy2(STAGED, MODEL)
        rc = subprocess.run(
            [sys.executable, str(REPO / "ml/nlp/eval_nlp.py")]).returncode
        if rc == 0 and baseline_f1 is not None:
            new_f1 = (json.loads(bench_path.read_text(encoding="utf-8"))
                      .get("measured") or {}).get("slot_f1")
            if new_f1 is not None and new_f1 < baseline_f1 - 1e-12:
                print(f"[retrain] slot F1 regressed {baseline_f1} -> {new_f1} "
                      f"(§3.6 forgetting check) — refusing swap")
                rc = 3
    finally:
        if rc != 0:
            if backup is not None:
                shutil.copy2(backup, MODEL)  # roll the live model back
            if bench_backup is not None:
                shutil.copy2(bench_backup, bench_path)
            print("[retrain] GATE FAILED — live model + benchmark restored")
        STAGED.unlink(missing_ok=True)
    if rc != 0:
        return 1
    print("[retrain] gates pass — staged weights are now live "
          f"(previous kept at {backup.name if backup else 'n/a'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
