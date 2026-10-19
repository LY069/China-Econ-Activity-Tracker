"""Credit transforms: TSF ex-government bonds, credit impulse, M1-M2 gap, loan structure.

Credit impulse (docs/methods.md):
    F12_t      = sum of the last 12 monthly flows (RMB bn)
    GDP4Q_t    = nominal GDP over the last 4 published quarters (RMB bn), stepped to months
                 using only quarters published by month t (real-time consistent)
    impulse_t  = (F12_t - F12_{t-12}) / GDP4Q_t * 100       [% of GDP]
Computed for total TSF, TSF ex-government bonds, and RMB loans to the real economy.
"""
from __future__ import annotations

import pandas as pd

# NBS publishes quarterly GDP ~15-20 days after quarter end, so a quarter is usable from the
# month after quarter end (Q1, published mid-April, is used from April onward).
GDP_PUBLICATION_LAG_MONTHS = 1


def _me(s: pd.Series) -> pd.Series:
    s = s.copy()
    s.index = pd.to_datetime(s.index) + pd.offsets.MonthEnd(0)
    return s.sort_index()


def rolling12(flow: pd.Series) -> pd.Series:
    f = _me(flow)
    full = pd.date_range(f.index.min(), f.index.max(), freq="ME")
    f = f.reindex(full)
    return f.rolling(12, min_periods=12).sum()


def gdp_4q_monthly(gdp_q_nominal: pd.Series, months: pd.DatetimeIndex) -> pd.Series:
    """Trailing 4-quarter nominal GDP available at each month (step function)."""
    q = _me(gdp_q_nominal)
    # time-based window: reindex to every quarter so a missing quarter yields NaN, never a sum of
    # four non-consecutive quarters
    q = q.reindex(pd.date_range(q.index.min(), q.index.max(), freq="QE"))
    s4 = q.rolling(4, min_periods=4).sum().dropna()
    avail = s4.copy()
    avail.index = avail.index + pd.offsets.MonthEnd(GDP_PUBLICATION_LAG_MONTHS)
    return avail.reindex(avail.index.union(months)).ffill().reindex(months)


def credit_impulse(flow: pd.Series, gdp_q_nominal: pd.Series) -> pd.Series:
    f12 = rolling12(flow)
    g = gdp_4q_monthly(gdp_q_nominal, f12.index)
    return ((f12 - f12.shift(12)) / g * 100).dropna().rename("credit_impulse")


def impulse_vs_stock(flow: pd.Series, stock: pd.Series) -> pd.Series:
    """GDP-free credit impulse: (F12_t - F12_{t-12}) / TSF stock_{t-12} * 100.

    Used for lead/lag inference because NBS release pages give nominal GDP only from late 2021,
    which would leave the GDP-scaled impulse with ~30 observations. Scaling by the lagged stock
    changes the units (percent of outstanding financing) but not the timing of turning points."""
    f12 = rolling12(flow)
    st = _me(stock)
    lag = st.copy()
    lag.index = lag.index + pd.offsets.MonthEnd(12)
    idx = f12.index.intersection(lag.index)
    return ((f12[idx] - f12.shift(12)[idx]) / lag[idx] * 100).dropna().rename("impulse_vs_stock")


def flow_from_stock(stock: pd.Series) -> pd.Series:
    """Monthly flow implied by stock differences (validation only: write-offs/FX/reclassification
    make this differ from PBoC's published flow)."""
    s = _me(stock)
    full = pd.date_range(s.index.min(), s.index.max(), freq="ME")
    return s.reindex(full).diff()


def ex_component(total: pd.Series, component: pd.Series) -> pd.Series:
    t, c = _me(total), _me(component)
    idx = t.index.intersection(c.index)
    return (t[idx] - c[idx]).rename(f"{total.name}_ex_{component.name}")


def gap(a: pd.Series, b: pd.Series) -> pd.Series:
    a, b = _me(a), _me(b)
    idx = a.index.intersection(b.index)
    return (a[idx] - b[idx])


def stock_yoy(stock: pd.Series) -> pd.Series:
    s = _me(stock)
    lag = s.copy()
    lag.index = lag.index + pd.offsets.MonthEnd(12)
    idx = s.index.intersection(lag.index)
    return (s[idx] / lag[idx] - 1) * 100


def sum_identity(total: pd.Series, parts: dict[str, pd.Series], tol_abs: float, tol_rel: float = 0.0) -> pd.DataFrame:
    """Check total == sum(parts) period by period. Exception if |diff| > max(tol_abs, tol_rel*|total|)."""
    t = _me(total)
    P = pd.concat({k: _me(v) for k, v in parts.items()}, axis=1)
    idx = t.index.intersection(P.dropna().index)
    df = pd.DataFrame({"total": t[idx], "sum_parts": P.loc[idx].sum(axis=1)})
    df["diff"] = df["total"] - df["sum_parts"]
    df["tol"] = (df["total"].abs() * tol_rel).clip(lower=tol_abs)
    df["exception"] = df["diff"].abs() > df["tol"]
    return df
