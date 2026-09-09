"""Export /api/nlp/feedback rows to CSV — the real user phrasings corpus.

The assistant (SEC/11) was trained on synthetic templates. Every time a user
answers "did I understand you correctly?", a row lands in the nlp_feedback
table (migration 0007). This script pulls those rows so they can be scored
(eval_nlp.py --extra) and folded into the next training run.

Usage:
    # from the production Supabase project
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
        python3 ml/nlp/import_feedback.py

    # from a local sqlite fallback DB
    python3 ml/nlp/import_feedback.py --sqlite path/to/shelter.db

Writes ml/nlp/data/real_user_feedback.csv (never committed — contains real
user sentences; it is gitignored like the rest of ml/nlp/data caches).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

OUT = REPO / "ml" / "nlp" / "data" / "real_user_feedback.csv"
COLS = ["created_at", "text", "intent", "confidence", "correct", "correction"]


def pull(sqlite_path: str | None) -> list[dict]:
    if sqlite_path:
        for var in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                    "SUPABASE_ANON_KEY", "DATABASE_URL"):
            os.environ.pop(var, None)          # force the sqlite branch
        from src.db.store import Store
        store = Store(db_path=sqlite_path)
    else:
        from src.db.store import Store          # uses .env Supabase creds
        store = Store()
    if not store._rest and not store._conn:    # noqa: SLF001 (deliberate)
        raise SystemExit("no store configured — set SUPABASE_* or pass --sqlite")
    return store.list_nlp_feedback(limit=5000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", help="read from a local sqlite DB instead of "
                                     "Supabase")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    rows = pull(args.sqlite)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c) for c in COLS})

    confirmed = sum(1 for r in rows if r.get("correct"))
    print(f"wrote {len(rows)} rows -> {out}")
    print(f"  confirmed-understood: {confirmed} "
          f"({confirmed / len(rows):.1%})" if rows else "  table is empty")
    if rows and confirmed < len(rows):
        missed = [r for r in rows if not r.get("correct")]
        print(f"  to retrain on: {len(missed)} misunderstood sentences "
              f"(with corrections where users gave them)")
    print("next: python3 ml/nlp/eval_nlp.py --extra "
          f"{out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
