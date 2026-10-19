"""Lead/lag analysis that survives scrutiny.

All operations run on a full monthly index: a missing month (e.g. January for NBS Jan-Feb
releases, or a month NDRC did not report) stays missing. Nothing is filled, and every lag,
difference and cross-correlation is time-based, so no value is ever paired with the wrong month
and no two-month change masquerades as a one-month change.

Pipeline for a pair (x = candidate leading indicator, y = official target):

1. Stationarity: ADF + KPSS on the observed values; if either says non-stationary the series
   is differenced month-on-month (a difference exists only where both months are observed).
2. Prewhitening: AR(p) for x by OLS on lagged values (p by AIC on a common sample, max 12);
   both x and y are filtered with the same AR polynomial; residuals are cross-correlated
   using all months where both are observed (pairwise).
3. ADL: y_t = a + sum_{i=1..p} b_i y_{t-i} + sum_{j=1..q} c_j x_{t-j}; (p, q) by BIC on a
   single common sample; HAC (Newey-West) standard errors; sum(c) and its p-value.
4. Granger: F-test of the x lags in the ADL design (restricted = own lags only, same rows).
5. Rolling 36-month prewhitened correlation at the best lead (min 24 pairs per window).
6. Breaks: Chow F at register dates inside the sample; Quandt-Andrews sup-F with a fixed-
   regressor bootstrap p-value (Hansen 2000) on the ADL rows.
7. Placebo: (a) circular block bootstrap of x (block 12) -> distribution of max |CCF| over
   leads; (b) the best lead must beat the best reverse-direction correlation.

A "x leads y by k months" verdict requires all four: prewhitened CCF outside the 95% band at
k, placebo p < 0.05, Granger p < 0.05, not dominated by the reverse direction.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats as st
from statsmodels.tsa.stattools import adfuller, kpss

warnings.filterwarnings("ignore", category=FutureWarning)


class InsufficientData(ValueError):
    pass


def _monthly(s: pd.Series) -> pd.Series:
    s = s.dropna().copy()
    s.index = pd.to_datetime(s.index) + pd.offsets.MonthEnd(0)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.empty:
        return s
    return s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="ME"))


def _align(x: pd.Series, y: pd.Series) -> pd.DataFrame:
    """x and y on a common full monthly index (overlap range); missing months stay NaN."""
    x, y = _monthly(x), _monthly(y)
    if x.empty or y.empty:
        return pd.DataFrame(columns=["x", "y"], dtype=float)
    lo, hi = max(x.index.min(), y.index.min()), min(x.index.max(), y.index.max())
    if lo > hi:
        return pd.DataFrame(columns=["x", "y"], dtype=float)
    idx = pd.date_range(lo, hi, freq="ME")
    return pd.DataFrame({"x": x.reindex(idx), "y": y.reindex(idx)})


def stationarity(s: pd.Series) -> dict:
    v = s.dropna()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        adf_p = adfuller(v, autolag="AIC")[1]
        kpss_p = kpss(v, regression="c", nlags="auto")[1]
    return {"adf_p": float(adf_p), "kpss_p": float(kpss_p), "stationary": bool(adf_p < 0.05 and kpss_p > 0.05)}


def make_stationary(s: pd.Series) -> tuple[pd.Series, str]:
    s = _monthly(s)
    if stationarity(s)["stationary"]:
        return s, "level"
    return s.diff(), "diff1"          # NaN unless both consecutive months are observed


def _lagmat(s: pd.Series, lags) -> pd.DataFrame:
    return pd.DataFrame({f"l{k}": s.shift(k) for k in lags}, index=s.index)


def prewhiten(x: pd.Series, y: pd.Series, maxlag: int = 12) -> tuple[pd.Series, pd.Series, int]:
    """Filter x and y with the AR polynomial fitted to x (time-based lags, NaNs propagate)."""
    x = x - x.mean()
    y = y - y.mean()
    maxlag = int(min(maxlag, max(1, x.notna().sum() // 6)))
    L = _lagmat(x, range(1, maxlag + 1))
    base = pd.concat([x.rename("x"), L], axis=1).dropna()
    best = (np.inf, 0)
    if len(base) > maxlag + 10:
        for p in range(0, maxlag + 1):
            X = sm.add_constant(base[[f"l{k}" for k in range(1, p + 1)]], has_constant="add")
            aic = sm.OLS(base["x"], X).fit().aic
            if aic < best[0]:
                best = (aic, p)
    p = best[1]
    if p == 0:
        return x, y, 0
    X = sm.add_constant(base[[f"l{k}" for k in range(1, p + 1)]], has_constant="add")
    phi = sm.OLS(base["x"], X).fit().params.values[1:]

    def filt(v: pd.Series) -> pd.Series:
        out = v.copy()
        for k in range(1, p + 1):
            out = out - phi[k - 1] * v.shift(k)
        return out

    return filt(x), filt(y), p


def ccf(x: pd.Series, y: pd.Series, max_lag: int = 12) -> tuple[pd.Series, pd.Series]:
    """corr(x_{t-k}, y_t), k in [-max_lag, max_lag], pairwise over observed months; returns
    (correlations, number of pairs). k > 0 means x leads y."""
    out, n = {}, {}
    for k in range(-max_lag, max_lag + 1):
        m = pd.concat([x.shift(k), y], axis=1).dropna()
        n[k] = len(m)
        out[k] = m.iloc[:, 0].corr(m.iloc[:, 1]) if len(m) > 10 else np.nan
    return pd.Series(out), pd.Series(n)


def adl(x: pd.Series, y: pd.Series, max_p: int = 6, max_q: int = 12) -> dict:
    """ADL with (p, q) chosen by BIC on one common sample (rows where all max lags exist)."""
    def frame(mp, mq):
        Ly = _lagmat(y, range(1, mp + 1)).add_prefix("y_")
        Lx = _lagmat(x, range(1, mq + 1)).add_prefix("x_")
        return Ly, Lx, pd.concat([y.rename("y"), Ly, Lx], axis=1).dropna()

    Ly, Lx, full = frame(max_p, max_q)
    if len(full) < 30:                       # short samples: smaller maximum lags
        max_p, max_q = 3, 6
        Ly, Lx, full = frame(max_p, max_q)
        if len(full) < 30:
            return {}
    best = None
    for p in range(1, max_p + 1):
        for q in range(1, max_q + 1):
            cols = [f"y_l{i}" for i in range(1, p + 1)] + [f"x_l{j}" for j in range(1, q + 1)]
            res = sm.OLS(full["y"], sm.add_constant(full[cols])).fit()
            if best is None or res.bic < best[0]:
                best = (res.bic, p, q)
    _, p, q = best
    ycols = [f"y_l{i}" for i in range(1, p + 1)]
    xcols = [f"x_l{j}" for j in range(1, q + 1)]
    m = pd.concat([y.rename("y"), Ly[ycols], Lx[xcols]], axis=1).dropna()   # all rows for chosen (p, q)
    hac_lags = int(np.floor(4 * (len(m) / 100) ** (2 / 9)))
    res = sm.OLS(m["y"], sm.add_constant(m[ycols + xcols])).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})
    names = list(res.params.index)
    R = np.zeros((1, len(names)))
    for c in xcols:
        R[0, names.index(c)] = 1
    t_sum = res.t_test(R)
    joint = res.f_test(np.eye(len(names))[[names.index(c) for c in xcols]])
    lr = res.params[xcols].sum() / (1 - res.params[ycols].sum())
    ols_u = sm.OLS(m["y"], sm.add_constant(m[ycols + xcols])).fit()
    ols_r = sm.OLS(m["y"], sm.add_constant(m[ycols])).fit()
    df1, df2 = len(xcols), int(ols_u.df_resid)
    F = ((ols_r.ssr - ols_u.ssr) / df1) / (ols_u.ssr / df2)
    return {
        "p": p, "q": q, "n": int(res.nobs), "r2": float(res.rsquared), "hac_lags": hac_lags,
        "sum_x": float(res.params[xcols].sum()), "sum_x_p": float(np.squeeze(t_sum.pvalue)),
        "joint_x_p": float(np.squeeze(joint.pvalue)), "long_run_multiplier": float(lr),
        "coefs": {c: float(res.params[c]) for c in xcols},
        "granger_F": float(F), "granger_p": float(1 - st.f.cdf(F, df1, df2)),
        "_rows": m, "_cols": ycols + xcols,
    }


def rolling_corr(xw: pd.Series, yw: pd.Series, lag: int, window: int = 36) -> pd.Series:
    d = pd.concat({"x": xw.shift(lag), "y": yw}, axis=1)
    return d["x"].rolling(window, min_periods=24).corr(d["y"]).dropna()


def _ssr(y, X):
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    return float(e @ e), e


def chow(y: np.ndarray, X: np.ndarray, k: int) -> float:
    n, m = X.shape
    s_all, _ = _ssr(y, X)
    s1, _ = _ssr(y[:k], X[:k])
    s2, _ = _ssr(y[k:], X[k:])
    return ((s_all - s1 - s2) / m) / ((s1 + s2) / (n - 2 * m))


def sup_f(y: np.ndarray, X: np.ndarray, trim: float = 0.15, n_boot: int = 199, seed: int = 0) -> dict:
    n, m = X.shape
    lo, hi = max(int(np.floor(n * trim)), m + 1), min(int(np.ceil(n * (1 - trim))), n - m - 1)
    if hi <= lo:
        return {}
    grid = list(range(lo, hi))
    stats_ = np.array([chow(y, X, k) for k in grid])
    kstar, sup = int(grid[int(stats_.argmax())]), float(stats_.max())
    _, e = _ssr(y, X)
    rng = np.random.default_rng(seed)
    sd = e.std(ddof=m)
    cnt = sum(max(chow(rng.normal(0, sd, n), X, k) for k in grid) >= sup for _ in range(n_boot))
    return {"sup_f": sup, "break_index": kstar, "p_boot": (cnt + 1) / (n_boot + 1)}


def block_placebo(x: pd.Series, y: pd.Series, max_lag: int, observed_max: float,
                  n_boot: int = 499, block: int = 12, seed: int = 0) -> float:
    """p-value: share of circular-block-shuffled x (missing months shuffled with it) giving
    max_{k>0}|CCF| >= observed."""
    xv, n = x.values, len(x)
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    cnt = 0
    for _ in range(n_boot):
        starts = rng.integers(0, n, nb)
        idx = np.concatenate([(np.arange(s, s + block) % n) for s in starts])[:n]
        c, _ = ccf(pd.Series(xv[idx], x.index), y, max_lag)
        pos = c[c.index > 0].abs()
        cnt += bool(pos.notna().any() and pos.max() >= observed_max)
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
             break_dates: list[str] | None = None, n_boot: int = 499, min_obs: int = 36) -> LeadLagResult:
    xs, tx = make_stationary(x)
    ys, ty = make_stationary(y)
    d = _align(xs, ys)
    n_pairs = int(d.dropna().shape[0])
    if n_pairs < min_obs:
        raise InsufficientData(f"{n_pairs} months with both series observed (< {min_obs})")
    raw, _ = ccf(d["x"], d["y"], max_lag)
    xw, yw, p = prewhiten(d["x"], d["y"])
    pw, npairs = ccf(xw, yw, max_lag)
    n = int(npairs[0])
    band = 1.96 / np.sqrt(max(n, 1))
    pos, neg = pw[pw.index > 0].abs(), pw[pw.index < 0].abs()
    best = int(pos.idxmax()) if pos.notna().any() else None
    best_corr = float(pw[best]) if best is not None else None
    rev = float(neg.max()) if neg.notna().any() else None
    band_k = 1.96 / np.sqrt(max(int(npairs[best]), 1)) if best is not None else band
    plac = block_placebo(xw, yw, max_lag, float(pos.max()), n_boot=n_boot) if best is not None else None
    ad = adl(d["x"], d["y"])
    gr = ad.get("granger_p") if ad else None
    roll = rolling_corr(xw, yw, best or 1)
    brk = {}
    if ad:
        m, cols = ad.pop("_rows"), ad.pop("_cols")
        Y, X = m["y"].values, sm.add_constant(m[cols]).values
        sf = sup_f(Y, X)
        if sf:
            sf["break_date"] = str(m.index[sf["break_index"]].date())
        brk["quandt_andrews"] = sf
        chows = {}
        for bd in break_dates or []:
            t = pd.Timestamp(bd)
            if not (m.index[0] < t < m.index[-1]):
                continue
            k = int((m.index <= t).sum())
            if X.shape[1] + 2 < k < len(m) - X.shape[1] - 2:
                F = chow(Y, X, k)
                chows[bd] = {"F": float(F), "p": float(1 - st.f.cdf(F, X.shape[1], len(m) - 2 * X.shape[1]))}
        brk["chow"] = chows
    crit = {
        "ccf_significant": bool(best is not None and abs(best_corr) > band_k),
        "placebo_p_lt_0.05": bool(plac is not None and plac < 0.05),
        "granger_p_lt_0.05": bool(gr is not None and gr < 0.05),
        "not_reverse_dominated": bool(rev is not None and best is not None and abs(best_corr) > rev),
    }
    verdict = (f"{xname} leads {yname} by {best} month(s) (robust)" if all(crit.values())
               else f"no robust lead of {xname} over {yname}")
    return LeadLagResult(
        x=xname, y=yname, n=n, transform_x=tx, transform_y=ty, ar_order_prewhiten=p,
        ccf_raw={int(k): (None if pd.isna(v) else float(v)) for k, v in raw.items()},
        ccf_prewhitened={int(k): (None if pd.isna(v) else float(v)) for k, v in pw.items()},
        band95=float(band), best_lead=best, best_lead_corr=best_corr, best_reverse_corr=rev,
        placebo_p=plac, granger_p=gr, adl=ad,
        rolling={str(k.date()): float(v) for k, v in roll.items()},
        breaks=brk, verdict=verdict, criteria=crit,
    )
