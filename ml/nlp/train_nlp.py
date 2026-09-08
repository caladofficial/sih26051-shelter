"""Train the NLP intent classifier and export it for pure-NumPy inference.

Model: hashing-vectorised word + character n-grams -> multinomial logistic
regression (SAGA). Exported as a dense weight matrix in JSON, so runtime
needs numpy only — no scikit-learn in the Vercel bundle, exactly like
src/ai_model.py.

Why hashing rather than a fitted vocabulary: it keeps the export a fixed
small size regardless of corpus growth, and it means an unseen word degrades
gracefully into its character n-grams instead of vanishing.

Honesty contract (same as the thermal surrogate):
  * held-out accuracy and the full confusion matrix are exported with the
    model and served by GET /api/nlp/info;
  * a low-confidence parse is reported as low-confidence, never guessed;
  * the parser proposes a design, the ENGINE still verifies it.

Run:  python3 ml/nlp/train_nlp.py
Out:  src/data/nlp_model.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from sklearn.linear_model import LogisticRegression      # noqa: E402
from sklearn.metrics import (accuracy_score,             # noqa: E402
                             classification_report, confusion_matrix)
from sklearn.model_selection import train_test_split     # noqa: E402

from scipy.sparse import csr_matrix                       # noqa: E402

from src.nlp_design import N_BUCKETS, feature_counts       # noqa: E402


def build_matrix(texts) -> csr_matrix:
    """Sparse CSR — the dense equivalent is ~650 MB for 40k rows."""
    indptr, indices, data = [0], [], []
    for t in texts:
        counts = feature_counts(t)
        norm = sum(v * v for v in counts.values()) ** 0.5 or 1.0
        for b, v in counts.items():
            indices.append(b)
            data.append(v / norm)
        indptr.append(len(indices))
    return csr_matrix((np.asarray(data, dtype=np.float32),
                       np.asarray(indices, dtype=np.int32),
                       np.asarray(indptr, dtype=np.int64)),
                      shape=(len(indptr) - 1, N_BUCKETS))

DATA = Path(__file__).resolve().parent / "data" / "nlp_dataset.csv"
OUT = REPO / "src" / "data" / "nlp_model.json"


def main() -> int:
    df = pd.read_csv(DATA)
    print(f"[nlp] corpus: {len(df):,} utterances, "
          f"{df.intent.nunique()} intents")

    X = build_matrix(df.text.astype(str))
    print(f"[nlp] features: {X.shape} sparse, "
          f"{X.nnz / X.shape[0]:.0f} non-zeros/row, "
          f"{X.data.nbytes / 1e6:.0f} MB")
    labels = sorted(df.intent.unique())
    y = df.intent.map({l: i for i, l in enumerate(labels)}).to_numpy()

    Xtr, Xte, ytr, yte = train_test_split(
        X, y, test_size=0.2, random_state=7, stratify=y)

    clf = LogisticRegression(max_iter=2000, C=4.0, solver="saga",
                             n_jobs=-1)
    clf.fit(Xtr, ytr)

    pred = clf.predict(Xte)
    acc = float(accuracy_score(yte, pred))
    print(f"[nlp] held-out accuracy: {acc:.4f}")
    print(classification_report(yte, pred, target_names=labels, digits=3))

    cm = confusion_matrix(yte, pred).tolist()
    per_class = {}
    rep = classification_report(yte, pred, target_names=labels,
                                output_dict=True, zero_division=0)
    for l in labels:
        per_class[l] = {k: round(float(rep[l][k]), 4)
                        for k in ("precision", "recall", "f1-score")}

    payload = {
        "schema_version": 1,
        "family": "hashed word+char n-grams -> multinomial logistic regression",
        "n_buckets": N_BUCKETS,
        "labels": labels,
        "coef": [[round(float(v), 5) for v in row] for row in clf.coef_],
        "intercept": [round(float(v), 5) for v in clf.intercept_],
        "metrics": {
            "n_train": int(len(ytr)), "n_test": int(len(yte)),
            "accuracy": round(acc, 4),
            "per_class": per_class,
            "confusion_matrix": cm,
            "labels_order": labels,
        },
        "note": ("Intent classifier only. Slot values (site, materials, "
                 "dimensions, orientation) are extracted by an explicit "
                 "gazetteer in src/nlp_design.py — deterministic and "
                 "auditable, which matters more than raw flexibility when "
                 "the output drives a physics engine."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"[nlp] wrote {OUT.relative_to(REPO)}  ({kb:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
