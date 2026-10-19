"""Project-type classification ("six networks" and others) with an audit.

Rules: config/six_networks.yaml keyword lists, first match in `priority` order wins.
Unmatched records are kept as "unclassified" and shown in the app (never dropped).

Audit: a random sample of classified + unclassified records is labelled independently
of the rules (labeller sees only the project text, not the rule output). Precision and
recall per category are computed against those labels and published with the data.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
SIX = ["water", "power_energy", "transport", "computing", "urban_pipes", "logistics"]


def rules() -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "six_networks.yaml").read_text())
    kw = {**cfg["categories"], **cfg.get("other_categories", {})}
    return {"keywords": kw, "priority": cfg["priority"]}


def classify(text: str, r: dict | None = None) -> tuple[str, str]:
    """Return (category, matched keyword)."""
    r = r or rules()
    t = str(text or "")
    for cat in r["priority"]:
        for k in r["keywords"][cat]:
            if k in t:
                return cat, k
    return "unclassified", ""


def classify_frame(df: pd.DataFrame, text_col: str) -> pd.DataFrame:
    r = rules()
    res = df[text_col].map(lambda s: classify(s, r))
    out = df.copy()
    out["category"] = [c for c, _ in res]
    out["matched_keyword"] = [k for _, k in res]
    out["six_networks"] = out["category"].isin(SIX)
    return out


def audit_metrics(labels: pd.Series, preds: pd.Series) -> pd.DataFrame:
    """Per-category precision / recall / support, plus overall accuracy row."""
    cats = sorted(set(labels) | set(preds))
    rows = []
    for c in cats:
        tp = int(((preds == c) & (labels == c)).sum())
        fp = int(((preds == c) & (labels != c)).sum())
        fn = int(((preds != c) & (labels == c)).sum())
        rows.append({"category": c, "support": int((labels == c).sum()), "predicted": int((preds == c).sum()),
                     "precision": tp / (tp + fp) if tp + fp else float("nan"),
                     "recall": tp / (tp + fn) if tp + fn else float("nan")})
    out = pd.DataFrame(rows)
    acc = float((labels == preds).mean())
    out = pd.concat([out, pd.DataFrame([{"category": "_overall_accuracy", "support": len(labels),
                                         "predicted": len(preds), "precision": acc, "recall": acc}])])
    return out.reset_index(drop=True)
