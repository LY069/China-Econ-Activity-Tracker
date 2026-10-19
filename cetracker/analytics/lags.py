"""Lead/lag analysis that survives scrutiny.

Pipeline for a pair (x = candidate leading indicator, y = official target), monthly:

1. Stationarity: ADF + KPSS on each series; if either test says non-stationary, first-
   difference (or, for level series, use y/y growth upstream). The transform used is
   recorded in the result.
2. Prewhitening: fit AR(p) to x (p by AIC, max 12); filter both x and y with the same AR
   polynomial; cross-correlate the residuals. Without this, two smooth series show high
   correlation at every lag (the classic spurious-CCF problem).
3. ADL: y_t = a + sum_{i=1..p} b_i y_{t-i} + sum_{j=k0..k1} c_j x_{t-j} + e_t,
   lags chosen by BIC; HAC (Newey-West) standard errors; report sum(c) and its p-value.
4. Granger causality (F-test) at the ADL lag order.
5. Rolling 36-month prewhitened correlation at the best lag (stability over time).
6. Break tests: Chow F at the register dates inside the sample, and a Quandt-Andrews
   sup-F for one unknown break (15% trimming) with a fixed-regressor bootstrap p-value
   (Hansen 2000) so no tabulated critical values are needed.
7. Placebo: (a) circular block-bootstrap of x (block = 12 months, preserves x's own
   autocorrelation, destroys alignment with y) -> distribution of max |CCF| over the lag
   window -> p-value of the observed max; (b) reverse direction: the best positive-lag
   (x leads) correlation must exceed the best negative-lag (y leads) correlation.

A "x leads y by k months" claim is published only if ALL of: prewhitened CCF at k is
outside the 95% band, block-placebo p < 0.05, Granger p < 0.05, and the lead is not
dominated by the reverse direction. Otherwise the app says "no robust lead found" and
shows the evidence anyway.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.ar_model import AutoReg, ar_select_order
from statsmodels.tsa.stattools import adfuller, kpss, grangercausalitytests

warnings.filterwarnings("ignore", category=FutureWarning)


from ..transform.ytd import fill_janfeb


def _align(x: pd.Series, y: pd.Series) -> pd.DataFrame:
    """Common observations, restricted to the longest run of consecutive months.

    Shifts below are positional, so a gap (e.g. a month NDRC did not report) would silently
    pair the wrong months; restricting to a contiguous span avoids that.
    """
    d = pd.concat({"x": fill_janfeb(x), "y": fill_janfeb(y)}, axis=1, sort=True).dropna()
    if len(d) < 2:
        return d
    idx = pd.DatetimeIndex(d.index)
    step = np.diff(idx.year * 12 + idx.month)
    breaks = np.where(step != 1)[0]
    bounds = np.concatenate([[-1], breaks, [len(d) - 1]])
    lens = np.diff(bounds)
    k = int(lens.argmax())
    return d.iloc[bounds[k] + 1: bounds[k + 1] + 1]


def _ccf_np(xv: np.ndarray, yv: np.ndarray, max_lag: int) -> np.ndarray:
    """corr(x_{t-k}, y_t), k = -max_lag..max_lag, on aligned contiguous arrays."""
    n = len(xv)
    out = np.full(2 * max_lag + 1, np.nan)
    for i, k in enumerate(range(-max_lag, max_lag + 1)):
        if k >= 0:
            a, b = xv[: n - k], yv[k:]
        else:
            a, b = xv[-k:], yv[: n + k]
        if len(a) > 10:
            a = a - a.mean()
            b = b - b.mean()
            den = np.sqrt((a * a).sum() * (b * b).sum())
            out[i] = (a * b).sum() / den if den > 0 else np.nan
    return out


def stationarity(s: pd.Series) -> dict:
    s = s.dropna()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        adf_p = adfuller(s, autolag="AIC")[1]
        kpss_p = kpss(s, regression="c", nlags="auto")[1]
    return {"adf_p": float(adf_p), "kpss_p": float(kpss_p), "stationary": bool(adf_p < 0.05 and kpss_p > 0.05)}


def make_stationary(s: pd.Series) -> tuple[pd.Series, str]:
    st = stationarity(s)
    if st["stationary"]:
        return s, "level"
    return s.diff().dropna(), "diff1"


def prewhiten(x: pd.Series, y: pd.Series, maxlag: int = 12) -> tuple[pd.Series, pd.Series, int]:
    d = _align(x, y)
    sel = ar_select_order(d["x"].values, maxlag=min(maxlag, len(d) // 5), ic="aic", trend="c")
    p = max(sel.ar_lags[-1] if sel.ar_lags else 0, 0)
    if p == 0:
        return d["x"] - d["x"].mean(), d["y"] - d["y"].mean(), 0
    fit = AutoReg(d["x"].values, lags=p, trend="c").fit()
    phi = fit.params[1:]

    def filt(v: np.ndarray) -> np.ndarray:
        v = v - v.mean()
        out = v.copy()
        for i in range(1, p + 1):
            out[p:] -= phi[i - 1] * v[p - i:len(v) - i]
        return out[p:]

    idx = d.index[p:]
    return pd.Series(filt(d["x"].values), idx), pd.Series(filt(d["y"].values), idx), p


def ccf(x: pd.Series, y: pd.Series, max_lag: int = 12) -> pd.Series:
    """corr(x_{t-k}, y_t) for k in [-max_lag, max_lag]; k > 0 means x leads y."""
    d = _align(x, y)
    return pd.Series(_ccf_np(d["x"].values, d["y"].values, max_lag), index=range(-max_lag, max_lag + 1))


def adl(x: pd.Series, y: pd.Series, max_p: int = 6, max_q: int = 12, min_lag: int = 0) -> dict:
    d = _align(x, y)
    best = None
    for p in range(1, max_p + 1):
        for q in range(min_lag, max_q + 1):
            cols = {f"y_l{i}": d["y"].shift(i) for i in range(1, p + 1)}
            cols.update({f"x_l{j}": d["x"].shift(j) for j in range(min_lag, q + 1)})
            X = pd.DataFrame(cols)
            m = pd.concat([d["y"], X], axis=1).dropna()
            m = m.iloc[max_p + max_q - (p + q):] if len(m) > max_p + max_q else m  # common sample
            if len(m) < 30:
                continue
            res = sm.OLS(m["y"], sm.add_constant(m.drop(columns="y"))).fit()
            if best is None or res.bic < best[0]:
                best = (res.bic, p, q)
    if best is None:
        return {}
    _, p, q = best
    cols = {f"y_l{i}": d["y"].shift(i) for i in range(1, p + 1)}
    cols.update({f"x_l{j}": d["x"].shift(j) for j in range(min_lag, q + 1)})
    m = pd.concat([d["y"], pd.DataFrame(cols)], axis=1).dropna()
    hac_lags = int(np.floor(4 * (len(m) / 100) ** (2 / 9)))
    res = sm.OLS(m["y"], sm.add_constant(m.drop(columns="y"))).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})
    xcols = [c for c in res.params.index if c.startswith("x_l")]
    R = np.zeros((1, len(res.params)))
    for c in xcols:
        R[0, list(res.params.index).index(c)] = 1
    t_sum = res.t_test(R)
    joint = res.f_test(np.eye(len(res.params))[[list(res.params.index).index(c) for c in xcols]])
    lr_mult = res.params[xcols].sum() / (1 - res.params[[c for c in res.params.index if c.startswith("y_l")]].sum())
    return {
        "p": p, "q": q, "n": int(res.nobs), "r2": float(res.rsquared), "hac_lags": hac_lags,
        "sum_x": float(res.params[xcols].sum()), "sum_x_p": float(np.squeeze(t_sum.pvalue)),
        "joint_x_p": float(np.squeeze(joint.pvalue)), "long_run_multiplier": float(lr_mult),
        "coefs": {c: float(res.params[c]) for c in xcols},
    }


def granger(x: pd.Series, y: pd.Series, lag: int) -> float:
    d = _align(x, y)[["y", "x"]]
    lag = max(1, min(lag, len(d) // 6))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = grangercausalitytests(d.values, maxlag=[lag])
    return float(r[lag][0]["ssr_ftest"][1])


def rolling_corr(xw: pd.Series, yw: pd.Series, lag: int, window: int = 36) -> pd.Series:
    d = pd.concat({"x": xw.shift(lag), "y": yw}, axis=1).dropna()
    return d["x"].rolling(window).corr(d["y"]).dropna()


def _ssr(y, X):
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    return float(e @ e), e


def chow(y: np.ndarray, X: np.ndarray, k: int) -> float:
    """Chow F statistic for a break after observation k (all coefficients shift)."""
    n, m = X.shape
    s_all, _ = _ssr(y, X)
    s1, _ = _ssr(y[:k], X[:k])
    s2, _ = _ssr(y[k:], X[k:])
    return ((s_all - s1 - s2) / m) / ((s1 + s2) / (n - 2 * m))


def sup_f(y: np.ndarray, X: np.ndarray, trim: float = 0.15, n_boot: int = 299, seed: int = 0) -> dict:
    n, m = X.shape
    lo, hi = int(np.floor(n * trim)), int(np.ceil(n * (1 - trim)))
    lo, hi = max(lo, m + 1), min(hi, n - m - 1)
    if hi <= lo:
        return {}
    grid = list(range(lo, hi))
    stats = np.array([chow(y, X, k) for k in grid])
    kstar = int(grid[int(stats.argmax())])
    sup = float(stats.max())
    # fixed-regressor bootstrap (Hansen 2000): y* ~ N(0, s^2) with the regressors held fixed;
    # the bootstrap sup is taken over the same grid as the observed statistic.
    _, e = _ssr(y, X)
    rng = np.random.default_rng(seed)
    sd = e.std(ddof=m)
    cnt = 0
    for _ in range(n_boot):
        yb = rng.normal(0, sd, n)
        cnt += max(chow(yb, X, k) for k in grid) >= sup
    return {"sup_f": sup, "break_index": kstar, "p_boot": (cnt + 1) / (n_boot + 1)}


def block_placebo(x: pd.Series, y: pd.Series, max_lag: int, observed_max: float,
                  n_boot: int = 499, block: int = 12, seed: int = 0) -> float:
    """p-value: share of circular-block-shuffled x giving max_{k>0}|CCF| >= observed."""
    d = _align(x, y)
    xv, yv, n = d["x"].values, d["y"].values, len(d)
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    cnt = 0
    for _ in range(n_boot):
        starts = rng.integers(0, n, nb)
        idx = np.concatenate([(np.arange(s, s + block) % n) for s in starts])[:n]
        c = _ccf_np(xv[idx], yv, max_lag)
        cnt += np.nanmax(np.abs(c[max_lag + 1:])) >= observed_max
    return (cnt + 1) / (n_boot + 1)


@dataclass
class LeadLagResult:
    x: str
    y: str
    n: int
    transform_x: str
    transform_y: str
    ar_order_prewhiten: int
    ccf_raw: dict
    ccf_prewhitened: dict
    band95: float
    best_lead: int | None
    best_lead_corr: float | None
    best_reverse_corr: float | None
    placebo_p: float | None
    granger_p: float | None
    adl: dict
    rolling: dict
    breaks: dict
    verdict: str
    criteria: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def lead_lag(x: pd.Series, y: pd.Series, xname: str, yname: str, max_lag: int = 12,
             break_dates: list[str] | None = None, n_boot: int = 499) -> LeadLagResult:
    xs, tx = make_stationary(x.dropna())
    ys, ty = make_stationary(y.dropna())
    d = _align(xs, ys)
    raw = ccf(d["x"], d["y"], max_lag)
    xw, yw, p = prewhiten(d["x"], d["y"])
    pw = ccf(xw, yw, max_lag)
    n = len(xw)
    band = 1.96 / np.sqrt(n)
    pos = pw[pw.index > 0].abs()
    neg = pw[pw.index < 0].abs()
    best = int(pos.idxmax()) if pos.notna().any() else None
    best_corr = float(pw[best]) if best is not None else None
    rev = float(neg.max()) if neg.notna().any() else None
    plac = block_placebo(xw, yw, max_lag, float(pos.max()), n_boot=n_boot) if best is not None else None
    ad = adl(d["x"], d["y"], min_lag=1)
    gr = granger(d["x"], d["y"], ad.get("q", best or 1)) if ad else None
    roll = rolling_corr(xw, yw, best or 1)
    # breaks on the ADL regression
    brk = {}
    if ad:
        cols = {f"y_l{i}": d["y"].shift(i) for i in range(1, ad["p"] + 1)}
        cols.update({f"x_l{j}": d["x"].shift(j) for j in range(1, ad["q"] + 1)})
        m = pd.concat([d["y"], pd.DataFrame(cols)], axis=1).dropna()
        Y, X = m["y"].values, sm.add_constant(m.drop(columns="y")).values
        sf = sup_f(Y, X, n_boot=199)
        if sf:
            sf["break_date"] = str(m.index[sf["break_index"]].date())
        brk["quandt_andrews"] = sf
        chows = {}
        for bd in break_dates or []:
            if not (m.index[0] < pd.Timestamp(bd) < m.index[-1]):
                continue
            k = int((m.index <= pd.Timestamp(bd)).sum())
            if X.shape[1] + 2 < k < len(m) - X.shape[1] - 2:
                from scipy import stats as st
                F = chow(Y, X, k)
                chows[bd] = {"F": float(F), "p": float(1 - st.f.cdf(F, X.shape[1], len(m) - 2 * X.shape[1]))}
        brk["chow"] = chows
    crit = {
        "ccf_significant": best is not None and abs(best_corr) > band,
        "placebo_p_lt_0.05": plac is not None and plac < 0.05,
        "granger_p_lt_0.05": gr is not None and gr < 0.05,
        "not_reverse_dominated": rev is not None and best is not None and abs(best_corr) > rev,
    }
    verdict = (f"{xname} leads {yname} by {best} month(s) (robust)" if all(crit.values())
               else f"no robust lead of {xname} over {yname}")
    return LeadLagResult(
        x=xname, y=yname, n=n, transform_x=tx, transform_y=ty, ar_order_prewhiten=p,
        ccf_raw={int(k): float(v) for k, v in raw.items()},
        ccf_prewhitened={int(k): float(v) for k, v in pw.items()},
        band95=float(band), best_lead=best, best_lead_corr=best_corr, best_reverse_corr=rev,
        placebo_p=plac, granger_p=gr, adl=ad,
        rolling={str(k.date()): float(v) for k, v in roll.items()},
        breaks=brk, verdict=verdict, criteria=crit,
    )
