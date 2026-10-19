"""Year-to-date handling for NBS cumulative series.

Conventions (see docs/methods.md, "YTD and the Jan-Feb rule"):

* Inputs are month-end-indexed Series of *published* YTD levels (`ytd`) and/or published
  YTD y/y growth in percent (`ytd_yoy`). NBS publishes no January figure for IP, FAI,
  retail sales and property; the February release is Jan-Feb cumulative.
* Single month m (m >= 3): level_m = YTD_m - YTD_{m-1}.
* Jan-Feb rule: the Jan-Feb total is reported as one observation dated February with
  flag `janfeb_combined`. For monthly-frequency analytics it is split evenly into
  January and February (each = Jan-Feb / 2, both flagged `janfeb_split`); y/y for both
  months equals the Jan-Feb y/y. Even split is used rather than a CNY-weighted split
  because any split is unobservable and the even split keeps the Jan+Feb sum and the
  y/y exact; the CNY effect is then handled by keeping Jan+Feb together in SA.
* Comparable-basis single-month y/y: NBS growth rates are computed on a comparable
  sample, so y/y computed from published levels differs from published y/y. The
  implied comparable base for YTD_m is B_m = YTD_m / (1 + g_m/100). The single-month
  comparable y/y is (YTD_m - YTD_{m-1}) / (B_m - B_{m-1}) - 1, using the base implied by
  each release. This reproduces NBS's published single-month growth where it publishes
  one (retail, IP) and gives an official-consistent single-month figure where it does
  not (FAI, property).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _month_end(s: pd.Series) -> pd.Series:
    s = s.copy()
    s.index = pd.to_datetime(s.index) + pd.offsets.MonthEnd(0)
    return s.sort_index()


def single_month_from_ytd(ytd: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (single-month levels, flags). Feb holds Jan-Feb when Jan is missing.

    If January is published (e.g. some fiscal series) Feb = YTD_Feb - YTD_Jan as normal.
    A missing intermediate month (other than Jan) leaves the following month NaN rather
    than silently absorbing two months.
    """
    ytd = _month_end(ytd.dropna())
    out, flags = {}, {}
    for t, v in ytd.items():
        if t.month == 1:
            out[t] = v
            continue
        prev = t - pd.offsets.MonthEnd(1)
        if prev in ytd.index:
            out[t] = v - ytd[prev]
        elif t.month == 2:
            out[t] = v
            flags[t] = "janfeb_combined"
        else:
            out[t] = np.nan
            flags[t] = "gap_previous_month_missing"
    return pd.Series(out, dtype=float).sort_index(), pd.Series(flags, dtype=object)


def split_janfeb(single: pd.Series, flags: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Expand Jan-Feb combined observations into equal Jan and Feb halves."""
    s = single.copy()
    f = flags.reindex(s.index).fillna("")
    for t in f[f == "janfeb_combined"].index:
        jan = t - pd.offsets.MonthEnd(1)
        half = s[t] / 2.0
        s[t] = half
        s.loc[jan] = half
        f[t] = "janfeb_split"
        f.loc[jan] = "janfeb_split"
    return s.sort_index(), f.sort_index()


def implied_base(ytd: pd.Series, ytd_yoy: pd.Series) -> pd.Series:
    """Implied comparable prior-year YTD level: B_m = YTD_m / (1 + g_m/100)."""
    ytd, g = _month_end(ytd), _month_end(ytd_yoy)
    idx = ytd.index.intersection(g.index)
    return (ytd[idx] / (1 + g[idx] / 100.0)).rename("implied_base")


def comparable_single_month_yoy(ytd: pd.Series, ytd_yoy: pd.Series) -> pd.Series:
    """Official-consistent single-month y/y (%) from YTD levels and YTD y/y."""
    ytd, g = _month_end(ytd), _month_end(ytd_yoy)
    base = implied_base(ytd, g)
    sm, flags = single_month_from_ytd(ytd)
    out = {}
    for t in base.index:
        if t.month == 1 or flags.get(t, "") == "janfeb_combined":
            out[t] = g[t]                     # Jan-Feb (or Jan) single-period growth = YTD growth
            continue
        prev = t - pd.offsets.MonthEnd(1)
        if prev not in base.index:
            continue
        denom = base[t] - base[prev]
        if denom <= 0:
            continue
        out[t] = ((ytd[t] - ytd[prev]) / denom - 1) * 100
    return pd.Series(out, dtype=float).sort_index()


def naive_yoy(level: pd.Series) -> pd.Series:
    """y/y (%) computed directly from levels 12 months apart (not comparable basis)."""
    level = _month_end(level)
    lag = level.copy()
    lag.index = lag.index + pd.offsets.MonthEnd(12)
    idx = level.index.intersection(lag.index)
    return ((level[idx] / lag[idx] - 1) * 100).sort_index()


def yoy_gap_log(level: pd.Series, published_yoy: pd.Series, tol_pp: float) -> pd.DataFrame:
    """Compare y/y computed from levels with published y/y; rows beyond tolerance are exceptions."""
    comp = naive_yoy(level)
    pub = _month_end(published_yoy)
    idx = comp.index.intersection(pub.index)
    df = pd.DataFrame({"computed_yoy": comp[idx], "published_yoy": pub[idx]})
    df["gap_pp"] = df["computed_yoy"] - df["published_yoy"]
    df["exception"] = df["gap_pp"].abs() > tol_pp
    return df


def cagr_vs_2019(level_or_index: pd.Series, yoy: pd.Series | None = None) -> pd.Series:
    """2019-based annualised growth for 2021-22 periods (COVID base-effect treatment).

    With a y/y series g: for period t in year Y, the cumulative growth vs the same month of
    2019 is the product of (1+g) over Y, Y-1, ... , 2020; annualised over (Y-2019) years.
    """
    if yoy is None:
        raise ValueError("pass published y/y so the chain uses comparable growth")
    g = _month_end(yoy) / 100.0
    out = {}
    for t in g.index:
        if t.year < 2021 or t.year > 2023:
            continue
        acc, ok = 1.0, True
        for k in range(t.year - 2019):
            tk = t - pd.offsets.MonthEnd(12 * k)
            if tk not in g.index:
                ok = False
                break
            acc *= 1 + g[tk]
        if ok:
            out[t] = (acc ** (1 / (t.year - 2019)) - 1) * 100
    return pd.Series(out, dtype=float)


def fill_janfeb(s: pd.Series) -> pd.Series:
    """For series NBS publishes Jan-Feb combined (Jan missing, Feb = Jan-Feb figure), set
    January equal to the Jan-Feb value so monthly analytics see a contiguous series.
    Applied to growth rates only (for levels use split_janfeb). No-op where Jan exists."""
    s = _month_end(s.dropna())
    add = {}
    for t in s.index[s.index.month == 2]:
        jan = t - pd.offsets.MonthEnd(1)
        if jan not in s.index:
            add[jan] = s[t]
    return pd.concat([s, pd.Series(add, dtype=float)]).sort_index() if add else s
