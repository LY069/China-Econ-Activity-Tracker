"""Nowcasts with pseudo real-time out-of-sample evaluation.

Real-time discipline
--------------------
Each predictor carries a publication lag L (months): at the nowcast date for target month
t only x_{t-L} and earlier are used. Lags (docs/methods.md, "Release calendar"):
    bond issuance (ChinaBond / MOF daily-to-monthly)   L = 0  (month t complete at t+1 day 1)
    PBoC credit / money (released ~10th-15th of t+1)   L = 1  (conservative: may miss t)
    NDRC approvals (press conference, t+1 mid/late)    L = 1
    NBS activity of month t (~15th-17th of t+1)        L = 1  (target's own lag)
The nowcast date is the first day of t+1, before any NBS/PBoC release for month t.

"Pseudo" real-time: until the vintage store has accumulated history, estimation uses the
latest vintage (revised data) with the publication-lag ragged edge replicated. This is
standard but optimistic when revisions are large; the store records every vintage from
the first build so true real-time evaluation becomes possible over time, and every live
nowcast is logged before release (nowcast_log.csv) and scored after.

Evaluation: expanding window, first origin after `min_train` observations; one-step
nowcast at each origin; RMSE vs AR(1) benchmark; Diebold-Mariano test with the
Harvey-Leybourne-Newbold small-sample correction (squared-error loss, h = 1).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

from ..transform.ytd import fill_janfeb


def _me(s: pd.Series) -> pd.Series:
    """Month-end index; Jan filled from Jan-Feb for NBS combined-release series (fill_janfeb)."""
    s = s.copy()
    s.index = pd.to_datetime(s.index) + pd.offsets.MonthEnd(0)
    return fill_janfeb(s.sort_index())   # no-op for quarterly series and where January exists


def design(target: pd.Series, predictors: dict[str, tuple[pd.Series, int]], use: list[str], ar: int = 1) -> pd.DataFrame:
    y = _me(target).rename("y")
    cols = {"y": y}
    for i in range(1, ar + 1):
        cols[f"y_l{i}"] = y.shift(i)
    for name in use:
        s, lag = predictors[name]
        s = _me(s)
        s = s.reindex(s.index.union(y.index))
        cols[name] = s.shift(lag).reindex(y.index)
    return pd.DataFrame(cols)


def dm_test(e_model: np.ndarray, e_bench: np.ndarray, h: int = 1) -> tuple[float, float]:
    """Diebold-Mariano with HLN correction. Negative stat => model more accurate."""
    d = e_model ** 2 - e_bench ** 2
    n = len(d)
    if n < 8:
        return np.nan, np.nan
    dbar = d.mean()
    gamma0 = np.var(d, ddof=0)
    var = gamma0
    for k in range(1, h):
        var += 2 * np.cov(d[k:], d[:-k], ddof=0)[0, 1]
    dm = dbar / np.sqrt(var / n)
    hln = np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    stat = dm * hln
    p = 2 * (1 - stats.t.cdf(abs(stat), df=n - 1))
    return float(stat), float(p)


def evaluate(target: pd.Series, predictors: dict[str, tuple[pd.Series, int]], models: dict[str, list[str]],
             min_train: int = 48, ar: int = 1, exclude: tuple[str, str] | None = None) -> dict:
    """Expanding-window pseudo real-time evaluation of each model vs the AR benchmark.

    Each model is compared with the benchmark on their own common sample (predictor coverage
    differs a lot: NDRC ends in 2021, MOF local bonds start in 2022), so a short-history model
    does not shrink everyone else's sample. Models without min_train + 8 usable months are
    reported as insufficient. exclude: optional (start, end) window dropped from estimation;
    forecasts are still made and scored for those months, and errors are also reported without them.
    """
    bench = design(target, predictors, [], ar)
    out = {"models": {}, "forecasts": {}, "actual": {}}

    def run(D: pd.DataFrame, origins) -> dict:
        fc = {}
        for t in origins:
            train = D.loc[D.index < t].dropna()
            if exclude:
                train = train[(train.index < exclude[0]) | (train.index > exclude[1])]
            X = sm.add_constant(train.drop(columns="y"), has_constant="add")
            res = sm.OLS(train["y"], X).fit()
            xt = sm.add_constant(D.loc[[t]].drop(columns="y"), has_constant="add")
            fc[t] = float(np.asarray(res.predict(xt))[0])
        return fc

    def score(e: np.ndarray, origins) -> tuple[float, float]:
        rmse = float(np.sqrt(np.mean(e ** 2)))
        mask = np.ones(len(origins), bool)
        if exclude:
            mask = ~((origins >= exclude[0]) & (origins <= exclude[1]))
        return rmse, (float(np.sqrt(np.mean(e[mask] ** 2))) if mask.any() else np.nan)

    for m, use in models.items():
        D = design(target, predictors, use, ar)
        common = D.dropna().index.intersection(bench.dropna().index).sort_values()
        if len(common) < min_train + 8:
            out["models"][m] = {"predictors": use, "status": f"insufficient data ({len(common)} common months)"}
            continue
        origins = common[min_train:]
        f_m = run(D.loc[common], origins)
        f_b = run(bench.loc[common], origins)
        actual = bench.loc[origins, "y"]
        e_m = actual.values - np.array([f_m[t] for t in origins])
        e_b = actual.values - np.array([f_b[t] for t in origins])
        r_m, r_m_ex = score(e_m, origins)
        r_b, r_b_ex = score(e_b, origins)
        stat, p = dm_test(e_m, e_b)
        out["models"][m] = {
            "predictors": use, "n_oos": len(origins), "oos_start": str(origins[0].date()), "oos_end": str(origins[-1].date()),
            "rmse": r_m, "rmse_ar_same_sample": r_b, "rmse_ex_excluded_window": r_m_ex,
            "rel_rmse_vs_ar": r_m / r_b, "dm_stat": stat, "dm_p": p,
            "beats_ar": bool(r_m < r_b and p == p and p < 0.10),
        }
        out["forecasts"][m] = {str(t.date()): v for t, v in f_m.items()}
        out["forecasts"].setdefault("AR(1) benchmark", {}).update({str(t.date()): v for t, v in f_b.items()})
        out["actual"].update({str(t.date()): float(v) for t, v in actual.items()})
    ok = [v for v in out["models"].values() if "n_oos" in v]
    if not ok:
        out["error"] = "no model has enough data: " + "; ".join(f"{k}: {v['status']}" for k, v in out["models"].items())
    else:
        out["n_oos"] = max(v["n_oos"] for v in ok)
        out["oos_start"] = min(v["oos_start"] for v in ok)
        out["oos_end"] = max(v["oos_end"] for v in ok)
    return out


def live_nowcast(target: pd.Series, predictors: dict[str, tuple[pd.Series, int]], use: list[str],
                 ar: int = 1, exclude: tuple[str, str] | None = None) -> dict | None:
    """Nowcast for the first target month not yet published, using all data available now."""
    y = _me(target).dropna()
    t_next = y.index.max() + pd.offsets.MonthEnd(1)
    D = design(y, predictors, use, ar)
    # append the target month: lagged predictors and the AR term are known before its release
    row = {"y": np.nan, **{f"y_l{i}": y.iloc[-i] for i in range(1, ar + 1)}}
    for name in use:
        s, lag = predictors[name]
        s = _me(s)
        src = t_next - pd.offsets.MonthEnd(lag)
        row[name] = float(s[src]) if src in s.index else np.nan
    D = pd.concat([D, pd.DataFrame([row], index=[t_next])])
    train = D.dropna()
    if exclude:
        train = train[(train.index < exclude[0]) | (train.index > exclude[1])]
    row = D.loc[[t_next]].drop(columns="y")
    if row.isna().any(axis=1).iloc[0] or len(train) < 24:
        return None
    X = sm.add_constant(train.drop(columns="y"), has_constant="add")
    res = sm.OLS(train["y"], X).fit()
    pred = res.get_prediction(sm.add_constant(row, has_constant="add"))
    sf = pred.summary_frame(alpha=0.2)
    return {"target_period": str(t_next.date()), "nowcast": float(sf["mean"].iloc[0]),
            "lo80": float(sf["obs_ci_lower"].iloc[0]), "hi80": float(sf["obs_ci_upper"].iloc[0]),
            "predictors": use, "n_train": int(len(train))}


def bridge_quarterly(monthly: pd.Series, months_available: int) -> pd.Series:
    """Quarter average of the first `months_available` months of each quarter (1..3)."""
    m = _me(monthly)
    pos = (m.index.month - 1) % 3 + 1
    q = m[pos <= months_available]
    return q.groupby(q.index + pd.offsets.QuarterEnd(0)).mean()
