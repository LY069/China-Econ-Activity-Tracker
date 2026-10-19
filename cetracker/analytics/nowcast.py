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


def _me(s: pd.Series) -> pd.Series:
    s = s.copy()
    s.index = pd.to_datetime(s.index) + pd.offsets.MonthEnd(0)
    return s.sort_index()


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
    """Expanding-window pseudo real-time evaluation of several models vs AR benchmark.

    exclude: optional (start, end) window dropped from *estimation* (e.g. COVID months);
    forecasts are still made and scored for those months but reported separately.
    """
    models = {"AR(1) benchmark": [], **models}
    designs = {m: design(target, predictors, use, ar) for m, use in models.items()}
    # common evaluation sample: rows where every model has complete data
    common = None
    for m, D in designs.items():
        ok = D.dropna().index
        common = ok if common is None else common.intersection(ok)
    common = common.sort_values()
    if len(common) <= min_train + 8:
        return {"error": f"too few common observations ({len(common)})", "models": list(models)}
    origins = common[min_train:]
    fc = {m: {} for m in models}
    for t in origins:
        for m, D in designs.items():
            train = D.loc[common[common < t]].dropna()
            if exclude:
                train = train[(train.index < exclude[0]) | (train.index > exclude[1])]
            X = sm.add_constant(train.drop(columns="y"), has_constant="add")
            res = sm.OLS(train["y"], X).fit()
            xt = sm.add_constant(D.loc[[t]].drop(columns="y"), has_constant="add")
            fc[m][t] = float(np.asarray(res.predict(xt))[0])
    actual = designs["AR(1) benchmark"].loc[origins, "y"]
    out = {"n_oos": len(origins), "oos_start": str(origins[0].date()), "oos_end": str(origins[-1].date()),
           "models": {}, "forecasts": {}}
    bench_e = actual.values - np.array([fc["AR(1) benchmark"][t] for t in origins])
    for m in models:
        f = np.array([fc[m][t] for t in origins])
        e = actual.values - f
        rmse = float(np.sqrt(np.mean(e ** 2)))
        stat, p = (np.nan, np.nan) if m == "AR(1) benchmark" else dm_test(e, bench_e)
        mask = np.ones(len(origins), bool)
        if exclude:
            mask = ~((origins >= exclude[0]) & (origins <= exclude[1]))
        rmse_ex = float(np.sqrt(np.mean(e[mask] ** 2))) if mask.any() else np.nan
        out["models"][m] = {
            "predictors": models[m], "rmse": rmse, "rmse_ex_excluded_window": rmse_ex,
            "rel_rmse_vs_ar": rmse / float(np.sqrt(np.mean(bench_e ** 2))),
            "dm_stat": stat, "dm_p": p,
            "beats_ar": bool(m != "AR(1) benchmark" and rmse < np.sqrt(np.mean(bench_e ** 2)) and p == p and p < 0.10),
        }
        out["forecasts"][m] = {str(t.date()): v for t, v in zip(origins, f)}
    out["actual"] = {str(t.date()): float(v) for t, v in actual.items()}
    return out


def live_nowcast(target: pd.Series, predictors: dict[str, tuple[pd.Series, int]], use: list[str],
                 ar: int = 1, exclude: tuple[str, str] | None = None) -> dict | None:
    """Nowcast for the first target month not yet published, using all data available now."""
    y = _me(target).dropna()
    t_next = y.index.max() + pd.offsets.MonthEnd(1)
    ext = pd.concat([y, pd.Series([np.nan], index=[t_next])])
    D = design(ext, predictors, use, ar)
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
