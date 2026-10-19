"""70-city housing prices: tier aggregates built from city-level NBS indices.

NBS publishes, per city and per segment (new commercial residential / second-hand
residential), indices with previous month = 100 (m/m), same month last year = 100 (y/y)
and (since 2021) previous-year average = 100. NBS does not publish an official tier index
series in the data portal; the release text quotes tier m/m and y/y.

Tier aggregate method (documented, reproducible):
    tier m/m % = simple mean over tier cities of (index_mom - 100)
    tier y/y % = simple mean over tier cities of (index_yoy - 100)
    tier level = chain of (1 + m/m/100), base 2022 average = 100 (the first full year of
                 the NBS release archive); a month with no m/m is shown as a gap, and the
                 chain resumes afterwards assuming no change over the missing month
Simple (unweighted) means match NBS commentary convention; a sales-weighted variant
would need city transaction weights NBS does not publish monthly.
Validation: tier m/m and y/y vs the figures in the NBS interpretation text (tolerance 0.1pp:
published tier figures are rounded to 0.1 and city indices to 0.1 before averaging); a tier
is reported only if all its cities report.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]


def tiers() -> dict[str, list[str]]:
    return yaml.safe_load((ROOT / "config" / "tiers.yaml").read_text())


def tier_aggregate(city_idx: pd.DataFrame, require_all: bool = True) -> pd.DataFrame:
    """city_idx: index = month-end, columns = city names, values = index (prev = 100).

    Returns DataFrame (index month-end, columns tier1/tier2/tier3/all70) of % changes.
    """
    out = {}
    tmap = tiers()
    tmap = {**tmap, "all70": sum(tmap.values(), [])}
    for tier, cities in tmap.items():
        cols = [c for c in cities if c in city_idx.columns]
        if not cols:
            continue
        sub = city_idx[cols] - 100.0
        m = sub.mean(axis=1)
        if require_all:
            complete = sub.notna().sum(axis=1) == len(cities)
            m = m.where(complete)
        out[tier] = m
    return pd.DataFrame(out)


def chain_level(mom_pct: pd.DataFrame, base_year: int = 2022) -> pd.DataFrame:
    full = mom_pct.reindex(pd.date_range(mom_pct.index.min(), mom_pct.index.max(), freq="ME"))
    lvl = (1 + full.fillna(0) / 100.0).cumprod()
    lvl = lvl.where(full.notna())                     # gaps are shown as gaps, not as 0% change
    if base_year in set(lvl.index.year):
        lvl = lvl / lvl[lvl.index.year == base_year].mean() * 100
    else:
        lvl = lvl / lvl.iloc[0] * 100
    return lvl


def breadth(city_mom: pd.DataFrame) -> pd.Series:
    """Number of the 70 cities with m/m price declines (index < 100)."""
    return (city_mom < 100).sum(axis=1).where(city_mom.notna().any(axis=1))
