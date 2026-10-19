"""Real (price-adjusted) growth.

    real y/y = ((1 + nominal y/y) / (1 + deflator y/y) - 1) * 100

Deflators:
    FAI     NBS discontinued the quarterly fixed-asset-investment price index after 2019
            (verify on fetch); the tracker uses PPI y/y as the deflator proxy, flagged
            `deflator_proxy:ppi`. If the FAI price index is available for a quarter it is
            used instead and the flag says so.
    Retail  CPI consumer-goods y/y (retail sales are goods plus catering; catering is a
            ~10% share so the bias is small; flag `deflator:cpi_goods`).
"""
from __future__ import annotations

import pandas as pd


def real_yoy(nominal_yoy: pd.Series, deflator_yoy: pd.Series) -> pd.Series:
    n, d = nominal_yoy.copy(), deflator_yoy.copy()
    for s in (n, d):
        s.index = pd.to_datetime(s.index) + pd.offsets.MonthEnd(0)
    idx = n.index.intersection(d.index)
    return ((1 + n[idx] / 100) / (1 + d[idx] / 100) - 1) * 100


def ytd_average_deflator(deflator_yoy: pd.Series) -> pd.Series:
    """YTD-average deflator y/y (for deflating YTD growth rates)."""
    d = deflator_yoy.copy()
    d.index = pd.to_datetime(d.index) + pd.offsets.MonthEnd(0)
    return d.groupby(d.index.year).expanding().mean().reset_index(level=0, drop=True).sort_index()
