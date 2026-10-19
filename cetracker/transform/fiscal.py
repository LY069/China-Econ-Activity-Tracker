"""Government bond issuance: classification, pace vs NPC quota, same-date comparison.

Categories (see config/breaks.yaml for the swap-programme history):
    central:  cgb_regular | cgb_ultra_long_special | cgb_bank_recap_special
    local:    lg_general_new | lg_special_new | lg_refi_regular | lg_refi_debt_swap

Pace: cumulative issuance in year Y up to day-of-year d (or month m) divided by the
year's quota for that category; compared with the same date in prior years. Without a
quota (e.g. regular refinancing, set by maturities) pace is shown in RMB bn only.

Net financing (for reconciliation with the PBoC TSF government-bond line):
    net = gross issuance - redemptions (principal repaid), by month.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]

CATEGORIES = [
    "cgb_regular", "cgb_ultra_long_special", "cgb_bank_recap_special",
    "lg_general_new", "lg_special_new", "lg_refi_regular", "lg_refi_debt_swap",
]
QUOTA_KEY = {
    "cgb_ultra_long_special": "ultra_long_special_cgb",
    "cgb_bank_recap_special": "bank_recap_special_cgb",
    "lg_general_new": "lg_general_new",
    "lg_special_new": "lg_special_new",
    "lg_refi_debt_swap": "debt_swap_refinancing",
}
NEW_MONEY = ["cgb_regular", "cgb_ultra_long_special", "cgb_bank_recap_special", "lg_general_new", "lg_special_new"]


def quotas() -> dict:
    return yaml.safe_load((ROOT / "config" / "quotas.yaml").read_text())


def quota(year: int, category: str) -> tuple[float | None, bool]:
    q = quotas().get(year, {})
    key = QUOTA_KEY.get(category)
    if key is None:
        return None, False
    v = q.get(key)
    return (float(v) if v is not None else None), bool(q.get("verified", False))


def classify_local_bond(name: str, bond_type: str = "", use_of_proceeds: str = "") -> str:
    """Classify one local-government bond from its name/type/purpose text (Chinese).

    Order matters: refinancing markers are checked before general/special.
    """
    text = f"{name} {bond_type} {use_of_proceeds}"
    if "再融资" in text:
        if any(k in text for k in ("特殊再融资", "置换", "隐性债务", "化债")):
            return "lg_refi_debt_swap"
        if "专项" in text and any(k in text for k in ("置换存量隐性债务", "偿还存量")):
            return "lg_refi_debt_swap"
        return "lg_refi_regular"
    if "置换" in text:
        return "lg_refi_debt_swap"
    if "专项" in text:
        return "lg_special_new"
    if "一般" in text:
        return "lg_general_new"
    return "unclassified"


def classify_central_bond(name: str) -> str:
    if "超长期特别国债" in name:
        return "cgb_ultra_long_special"
    if "抗疫特别国债" in name:
        return "cgb_covid_special"
    if "特别国债" in name:
        return "cgb_bank_recap_special"
    return "cgb_regular"


def cumulative_by_year(monthly: pd.Series) -> pd.Series:
    s = monthly.copy()
    s.index = pd.to_datetime(s.index) + pd.offsets.MonthEnd(0)
    return s.groupby(s.index.year).cumsum()


def pace_table(monthly: pd.Series, category: str) -> pd.DataFrame:
    """Year x month table of cumulative issuance as % of quota (or RMB bn if no quota)."""
    cum = cumulative_by_year(monthly)
    rows = []
    for t, v in cum.items():
        q, ver = quota(t.year, category)
        rows.append({"year": t.year, "month": t.month, "cum_bn": v,
                     "quota_bn": q, "pct_quota": (v / q * 100) if q else np.nan, "quota_verified": ver})
    return pd.DataFrame(rows)


def same_date_comparison(monthly: pd.Series, category: str, year: int, n_prior: int = 3) -> pd.DataFrame:
    """Current-year pace vs the average of the prior n years at the same month."""
    tbl = pace_table(monthly, category)
    cur = tbl[tbl.year == year].set_index("month")
    prior = tbl[(tbl.year < year) & (tbl.year >= year - n_prior)]
    col = "pct_quota" if cur["pct_quota"].notna().any() and prior["pct_quota"].notna().any() else "cum_bn"
    avg = prior.groupby("month")[col].mean()
    out = pd.DataFrame({"current": cur[col], f"prior{n_prior}y_avg": avg}).dropna(how="all")
    out["diff"] = out["current"] - out[f"prior{n_prior}y_avg"]
    out.attrs["unit"] = "% of quota" if col == "pct_quota" else "RMB bn"
    return out
