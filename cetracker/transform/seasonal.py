"""Seasonal adjustment with a Lunar New Year moving-holiday effect.

Primary method: X-13ARIMA-SEATS (Census binary `x13as_html`, from the `x13binary`
wheel) run as X-11 with automatic ARIMA selection and three user-defined holiday
regressors for the Spring Festival:

    pre   window [-20, -1] days before New Year's Day (pre-holiday front-loading)
    on    window [0, +6]   (the official holiday week)
    post  window [+7, +20] (slow restart until after the Lantern Festival)

Each regressor = share of the window's days that fall in the month, centred by
subtracting its calendar-month mean over 2005-2030 (genhol convention, so the average
holiday effect stays in the seasonal factor and only the year-to-year shift is removed).

For NBS series published Jan-Feb combined, January and February regressor values are
summed and split evenly (matching the even Jan/Feb data split), so only the leakage of
the post-holiday window into March remains a holiday effect.

Cross-check: STL (statsmodels) on log levels after removing the holiday effect estimated
by OLS of the log level on the same regressors plus month dummies and a trend.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = {"cny_pre": (-20, -1), "cny_on": (0, 6), "cny_post": (7, 20)}


@lru_cache(maxsize=1)
def cny_dates() -> dict[int, pd.Timestamp]:
    raw = yaml.safe_load((ROOT / "config" / "cny.yaml").read_text())
    return {int(k): pd.Timestamp(v) for k, v in raw.items()}


def cny_regressors(index: pd.DatetimeIndex, janfeb_combined: bool = False) -> pd.DataFrame:
    """Centred holiday regressors for month-end dates in `index` (2005-2030 coverage)."""
    full = pd.date_range("2005-01-31", "2030-12-31", freq="ME")
    cols = {}
    for name, (a, b) in WINDOWS.items():
        days = pd.Series(0.0, index=full)
        width = b - a + 1
        for _, d in cny_dates().items():
            for k in range(a, b + 1):
                m = (d + pd.Timedelta(days=k)) + pd.offsets.MonthEnd(0)
                if m in days.index:
                    days[m] += 1.0 / width
        if janfeb_combined:
            for y in range(2005, 2031):
                j, f = pd.Timestamp(y, 1, 31), pd.Timestamp(y, 2, 1) + pd.offsets.MonthEnd(0)
                tot = days[j] + days[f]
                days[j] = days[f] = tot / 2
        days = days - days.groupby(days.index.month).transform("mean")
        cols[name] = days
    reg = pd.DataFrame(cols)
    idx = pd.DatetimeIndex(index) + pd.offsets.MonthEnd(0)
    missing = idx.difference(reg.index)
    if len(missing):
        raise ValueError(f"no CNY dates for {missing[:3]}")
    return reg.loc[idx].set_axis(index)


def _drop_constant(reg: pd.DataFrame) -> pd.DataFrame:
    """Drop regressors with no variation (with Jan-Feb combined, the pre- and on-holiday
    windows always fall inside Jan+Feb, so only the post-window leak into March remains)."""
    return reg.loc[:, reg.std() > 1e-9]


def _x13_binary() -> str | None:
    return shutil.which("x13as_html") or shutil.which("x13as")


def _fmt_block(vals, per_line=6) -> str:
    vals = list(vals)
    return "\n".join(" ".join(f"{v:.6f}" for v in vals[i:i + per_line]) for i in range(0, len(vals), per_line))


def x13(y: pd.Series, janfeb_combined: bool = False, holiday: bool = True) -> pd.DataFrame:
    """Run X-13 (X-11 decomposition). Returns DataFrame with sa, seasonal factor, trend.

    Raises RuntimeError if the binary is missing or the run fails; callers fall back to STL.
    """
    exe = _x13_binary()
    if exe is None:
        raise RuntimeError("x13as binary not found")
    y = y.dropna().astype(float)
    if len(y) < 36:
        raise RuntimeError("X-13 needs >= 3 years")
    if (pd.Series(y.index).diff().dt.days.dropna() > 31).any():
        raise RuntimeError("series has gaps; X-13 needs a contiguous monthly span")
    start = y.index[0]
    with tempfile.TemporaryDirectory() as td:
        spc = [f'series{{title="s" start={start.year}.{start.month} period=12\n data=(\n{_fmt_block(y.values)}\n)}}']
        positive = (y > 0).all()
        spc.append("transform{function=%s}" % ("auto" if positive else "none"))
        if holiday:
            # regressors must extend over forecast horizon (default 12 months beyond data)
            ridx = pd.date_range(start, y.index[-1] + pd.offsets.MonthEnd(24), freq="ME")
            reg = cny_regressors(ridx, janfeb_combined)
            reg = _drop_constant(reg)
            rows = "\n".join(" ".join(f"{v:.6f}" for v in r) for r in reg.values)
            Path(td, "cny.dat").write_text(rows + "\n")
            names = " ".join(reg.columns)
            spc.append(f"regression{{user=({names}) file=\"cny.dat\" start={start.year}.{start.month} usertype=holiday}}")
        spc.append("automdl{}")
        spc.append("x11{save=(d10 d11 d12)}")
        Path(td, "s.spc").write_text("\n".join(spc) + "\n")
        proc = subprocess.run([exe, "s", "-s"], cwd=td, capture_output=True, text=True, timeout=120)
        out = {}
        for tbl in ("d10", "d11", "d12"):
            p = Path(td, f"s.{tbl}")
            if not p.exists():
                err = Path(td, "s_err.html").read_text(errors="ignore")[-800:] if Path(td, "s_err.html").exists() else proc.stdout[-800:]
                raise RuntimeError(f"X-13 failed: {err}")
            d = pd.read_csv(p, sep="\t", skiprows=2, header=None, names=["date", "v"])
            d.index = pd.to_datetime(d["date"].astype(str), format="%Y%m") + pd.offsets.MonthEnd(0)
            out[tbl] = d["v"].astype(float)
    df = pd.DataFrame({"sa": out["d11"], "seasonal": out["d10"], "trend": out["d12"]})
    df.index = y.index[: len(df)] if len(df) == len(y) else df.index
    df.attrs["method"] = "X-13ARIMA-SEATS (X-11, automdl" + (", CNY regressors)" if holiday else ")")
    return df


def stl_holiday(y: pd.Series, janfeb_combined: bool = False) -> pd.DataFrame:
    """Cross-check SA: OLS holiday removal on logs, then STL (robust, period 12)."""
    import statsmodels.api as sm
    from statsmodels.tsa.seasonal import STL

    y = y.dropna().astype(float)
    use_log = (y > 0).all()
    z = np.log(y) if use_log else y.copy()
    reg = _drop_constant(cny_regressors(y.index, janfeb_combined))
    X = pd.get_dummies(y.index.month, prefix="m", drop_first=True).astype(float).set_index(y.index)
    X["trend"] = np.arange(len(y)) / 12.0
    X = pd.concat([X, reg], axis=1)
    X = sm.add_constant(X)
    fit = sm.OLS(z.values, X.values).fit()
    beta = pd.Series(fit.params, index=X.columns)
    hol = reg @ beta[reg.columns]
    res = STL(z - hol, period=12, robust=True).fit()
    sa = z - hol - res.seasonal
    if use_log:
        df = pd.DataFrame({"sa": np.exp(sa), "seasonal": np.exp(res.seasonal + hol), "trend": np.exp(res.trend)})
    else:
        df = pd.DataFrame({"sa": sa, "seasonal": res.seasonal + hol, "trend": res.trend})
    df.attrs["method"] = "STL (robust) after OLS CNY-holiday removal"
    df.attrs["holiday_coefs"] = beta[reg.columns].to_dict()
    return df


def seasonal_adjust(y: pd.Series, janfeb_combined: bool = False) -> tuple[pd.DataFrame, pd.DataFrame | None, str]:
    """Primary SA plus cross-check. Returns (primary, crosscheck, note)."""
    try:
        prim = x13(y, janfeb_combined)
        try:
            cross = stl_holiday(y, janfeb_combined)
        except Exception:
            cross = None
        return prim, cross, prim.attrs["method"]
    except Exception as e:  # documented fallback, surfaced in the series metadata
        prim = stl_holiday(y, janfeb_combined)
        return prim, None, f"{prim.attrs['method']} (X-13 unavailable: {str(e)[:120]})"


def sa_mom(sa: pd.Series) -> pd.Series:
    """Seasonally adjusted m/m % change."""
    return (sa / sa.shift(1) - 1) * 100


def saar_3m(sa: pd.Series) -> pd.Series:
    """3m/3m annualised % change of the SA level (smoother real-time momentum measure)."""
    m3 = sa.rolling(3).mean()
    return ((m3 / m3.shift(3)) ** 4 - 1) * 100
