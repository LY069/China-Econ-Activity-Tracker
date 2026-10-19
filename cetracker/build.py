"""End-to-end build: ingest -> derive -> analytics -> validation -> export for the app.

    python -m cetracker.build              # full refresh (network)
    python -m cetracker.build --offline    # recompute from the existing store only
    python -m cetracker.build --replay     # re-parse everything from data/raw, no network

Outputs (site/data/):
    catalog.json        every series: meta, values, provenance, flags, SA notes
    analytics.json      lead-lag results, nowcast evaluation, live nowcasts
    validation.json     all checks (also rendered to docs/validation_report.md)
    breaks.json         structural-break register
    bonds.json          issuance pace vs quota tables
    source_status.json  reachability + per-source ingest result
    meta.json           build time, counts
and data/nowcast_log.csv (append-only pre-release nowcast log, scored on later builds).
"""
from __future__ import annotations

import argparse
import os
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import store, derive, registry
from .analytics import lags, nowcast
from .transform import fiscal as F
from .validate import checks

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DATA = SITE / "data"
NOWCAST_LOG = ROOT / "data" / "nowcast_log.csv"

# (x, y, label): candidate leading indicator -> official target. Inference uses unsmoothed
# series (smoothing shifts estimated lead times); 3mma variants are for display only.
PAIRS = [
    ("tsf.govt_bonds~impulse_stk", "fai.ytd~cyoy", "Govt bond net financing impulse -> FAI"),
    ("lgb.new_total~impulse_stk", "fai.ytd~cyoy", "New local bonds impulse -> FAI"),
    ("tsf.flow_ex_govt~impulse_stk", "fai.ytd~cyoy", "Credit impulse ex-govt -> FAI"),
    ("tsf.flow~impulse_stk", "ip.yoy", "Credit impulse -> IP"),
    ("tsf.flow_ex_govt~impulse_stk", "retail.yoy", "Credit impulse ex-govt -> retail sales"),
    ("money.m1_m2_gap", "re.sales_ytd~cyoy", "M1-M2 gap -> property sales"),
    ("money.m1_m2_gap", "ip.yoy", "M1-M2 gap -> IP"),
    ("ndrc.approvals_value", "fai.ytd~cyoy", "NDRC approvals -> FAI"),
]
# publication lags (months) used by the nowcast ragged edge; see analytics/nowcast.py
# MOF's monthly local-bond release appears 24-31 days after month end, i.e. after NBS activity
# data for the same month: L = 1 (not 0) to avoid look-ahead.
PUB_LAG = {"lgb.new_total~impulse_stk": 1, "tsf.govt_bonds~impulse_stk": 1,
           "tsf.flow_ex_govt~impulse_stk": 1, "tsf.flow~impulse_stk": 1, "ndrc.approvals_value": 1,
           "money.m1_m2_gap": 1}
COVID = ("2020-01-31", "2021-12-31")            # nowcast: rows excluded from estimation
# lead/lag robustness: China's disruption ran to the end of zero-COVID (Dec 2022), including the
# Apr-May 2022 Shanghai lockdown; a robust lead must survive dropping all of it
COVID_LEADLAG = ("2020-01-31", "2022-12-31")
# NBS release history starts Nov 2021 (~58 monthly obs by late 2026): low power is reported, not hidden.
MIN_OBS = 40
NOWCAST_MIN_TRAIN = 36


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "isoformat"):
        return o.isoformat()[:10]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if (math.isnan(o) or math.isinf(o)) else round(float(o), 6)
    return o


def dump(name: str, obj):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / name).write_text(json.dumps(_clean(obj), ensure_ascii=False, separators=(",", ":")))


def breaks() -> list[dict]:
    raw = yaml.safe_load((ROOT / "config" / "breaks.yaml").read_text())
    return [{k: (str(v) if k in ("date", "end") else v) for k, v in b.items()} for b in raw]


def run_analytics(get) -> dict:
    out = {"lead_lag": [], "nowcast": {}, "live": {}}
    bdates = [str(b["date"]) for b in breaks() if str(b["date"]) not in ("annual",)]
    for x, y, label in PAIRS:
        xs, ys = get(x), get(y)
        rec = {"x": x, "y": y, "label": label}
        if xs.dropna().shape[0] < MIN_OBS or ys.dropna().shape[0] < MIN_OBS:
            rec["status"] = "insufficient data"
            rec["n_x"], rec["n_y"] = int(xs.dropna().shape[0]), int(ys.dropna().shape[0])
            out["lead_lag"].append(rec)
            continue
        try:
            full = lags.lead_lag(xs, ys, x, y, break_dates=bdates).to_dict()
            ex = None
            try:
                mask = lambda s: s[(s.index < COVID_LEADLAG[0]) | (s.index > COVID_LEADLAG[1])]
                # ex-COVID robustness: drop the window then re-run on the remaining span
                ex = lags.lead_lag(mask(xs), mask(ys), x, y, n_boot=199).to_dict()
                ex = {k: ex[k] for k in ("verdict", "best_lead", "best_lead_corr", "placebo_p", "granger_p", "criteria", "n")}
            except Exception as e:
                ex = {"error": str(e)[:200]}
            # a robust lead must also hold without the COVID window
            full["criteria"]["survives_ex_covid"] = bool(ex and "no robust" not in str(ex.get("verdict", ""))
                                                         and "robust" in str(ex.get("verdict", ""))
                                                         and ex.get("best_lead") == full.get("best_lead"))
            if not all(full["criteria"].values()):
                full["verdict"] = f"no robust lead of {x} over {y}"
            out["lead_lag"].append({**rec, "status": "ok", **full, "ex_covid": ex})
        except lags.InsufficientData as e:
            out["lead_lag"].append({**rec, "status": f"insufficient data: {e}"})
        except Exception as e:
            out["lead_lag"].append({**rec, "status": f"error: {type(e).__name__}: {str(e)[:200]}"})
    # FAI nowcast
    target = get("fai.ytd~cyoy")
    preds = {k: (get(k), PUB_LAG[k]) for k in PUB_LAG if not get(k).empty}
    models = {}
    for name, use in [("AR + govt bond impulse", ["tsf.govt_bonds~impulse_stk"]),
                      ("AR + new local bonds", ["lgb.new_total~impulse_stk"]),
                      ("AR + NDRC approvals", ["ndrc.approvals_value"]),
                      ("AR + credit impulse ex-govt", ["tsf.flow_ex_govt~impulse_stk"]),
                      ("AR + govt bonds + credit", ["tsf.govt_bonds~impulse_stk", "tsf.flow_ex_govt~impulse_stk"])]:
        if all(u in preds for u in use):
            models[name] = use
    if len(target.dropna()) >= NOWCAST_MIN_TRAIN + 12 and models:
        try:
            out["nowcast"]["fai"] = nowcast.evaluate(target, preds, models, min_train=NOWCAST_MIN_TRAIN, exclude=COVID)
            out["nowcast"]["fai"]["target"] = "fai.ytd~cyoy"
            live = {}
            for name, use in models.items():
                lv = nowcast.live_nowcast(target, preds, use, exclude=COVID)
                if lv:
                    live[name] = lv
            out["live"]["fai"] = live
        except Exception as e:
            out["nowcast"]["fai"] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    else:
        out["nowcast"]["fai"] = {"error": "insufficient data", "n_target": int(len(target.dropna())),
                                 "available_predictors": list(preds)}
    # GDP bridge (quarterly): quarter-average of monthly indicators available by month 2 of the quarter
    g = get("gdp.real_yoy")
    if len(g.dropna()) >= 24:
        try:
            qp = {}
            for sid in ("ip.yoy", "retail.yoy~real", "fai.ytd~cyoy~real", "tsf.flow~impulse_stk"):
                s = get(sid)
                if not s.empty:
                    qp[sid] = (nowcast.bridge_quarterly(s, 1), 0)
            gq = g.copy()
            qmodels = {f"AR + {k}": [k] for k in qp}
            if len(qp) > 1:
                qmodels["AR + all monthly (1st month of quarter)"] = list(qp)
            out["nowcast"]["gdp"] = nowcast.evaluate(gq, qp, qmodels, min_train=12, exclude=("2020-03-31", "2021-12-31"))
            out["nowcast"]["gdp"]["target"] = "gdp.real_yoy"
        except Exception as e:
            out["nowcast"]["gdp"] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    return out


def update_nowcast_log(live: dict, get, built_at: str) -> pd.DataFrame:
    cols = ["made_at", "target", "target_period", "model", "nowcast", "lo80", "hi80", "actual", "error", "scored_at"]
    log = pd.read_csv(NOWCAST_LOG) if NOWCAST_LOG.exists() else pd.DataFrame(columns=cols)
    for tgt, models in live.items():
        tid = "fai.ytd~cyoy" if tgt == "fai" else tgt
        for model, r in models.items():
            dup = (log["target"] == tid) & (log["target_period"] == r["target_period"]) & (log["model"] == model)
            if dup.any():
                continue  # first pre-release nowcast for a period is the one scored
            log.loc[len(log)] = [built_at, tid, r["target_period"], model, r["nowcast"], r["lo80"], r["hi80"],
                                 np.nan, np.nan, ""]
    for i, r in log.iterrows():
        if pd.isna(r["actual"]):
            s = get(r["target"])
            p = pd.Timestamp(r["target_period"])
            if p in s.index:
                log.at[i, "actual"] = float(s[p])
                log.at[i, "error"] = float(s[p]) - float(r["nowcast"])
                log.at[i, "scored_at"] = built_at
    NOWCAST_LOG.parent.mkdir(parents=True, exist_ok=True)
    log.to_csv(NOWCAST_LOG, index=False)
    return log


def bond_tables(get) -> dict:
    out = {}
    for sid, cat in [("lgb.special_new", "lg_special_new"), ("lgb.general_new", "lg_general_new"),
                     # the swap proxy (all special refinancing since 2024-11) also contains ordinary rollover of
                     # maturing special bonds, so it is shown in RMB bn, never as a share of the RMB2trn swap quota
                     ("lgb.refi_swap_proxy", "lg_refi_swap_proxy"), ("lgb.refi_total", "lg_refi_total")]:
        s = get(sid)
        if s.empty:
            continue
        tbl = F.pace_table(s, cat)
        yr = int(s.index.max().year)
        cmp_ = F.same_date_comparison(s, cat, yr)
        out[sid] = {"category": cat, "pace": tbl.to_dict(orient="records"),
                    "same_date": {"year": yr, "unit": cmp_.attrs.get("unit"),
                                  "rows": cmp_.reset_index().rename(columns={"index": "month"}).to_dict(orient="records")}}
    out["quotas"] = F.quotas()
    return out


def export_catalog(obs: pd.DataFrame, notes: dict) -> dict:
    """Per series: values plus per-point provenance. 'src' lists the distinct sources
    (url, raw file, vintage, tier, lineage) and 'si' gives each point's index into it, so every
    plotted value traces to the exact raw response it came from."""
    lat = store.latest(obs)
    nvint = obs.groupby("series_id")["vintage_date"].nunique()
    cat = {}
    for sid, d in lat.groupby("series_id"):
        d = d.sort_values("period")
        m = registry.meta(sid)
        last = d.iloc[-1]
        keys = list(zip(d["source_url"], d["raw_file"], d["vintage_date"], d["source_tier"], d["lineage"]))
        uniq = list(dict.fromkeys(keys))
        pos = {k: i for i, k in enumerate(uniq)}
        cat[sid] = {
            "meta": {k: m.get(k) for k in ("name", "unit", "freq", "group", "kind")},
            "t": d["period"].tolist(), "v": d["value"].round(6).tolist(),
            "flags": {p: f for p, f in zip(d["period"], d["flags"]) if f},
            "src": [{"url": u, "raw": r, "vintage": v, "tier": t, "lineage": l} for u, r, v, t, l in uniq],
            "si": [pos[k] for k in keys],
            "prov": {"source": last["source"], "tier": last["source_tier"], "url": last["source_url"],
                     "raw_file": last["raw_file"], "retrieved_at": last["retrieved_at"],
                     "vintage": last["vintage_date"], "lineage": last["lineage"],
                     "n_vintages": int(nvint.get(sid, 1)),
                     "tiers_used": sorted(d["source_tier"].unique().tolist())},
            "note": notes.get(sid, "") or m.get("note", ""),
        }
    return cat


def write_validation_md(vals: list[dict], built_at: str):
    lines = ["# Validation report", "", f"Generated by `python -m cetracker.build` at {built_at} (UTC).", "",
             "Status meanings: PASS = within tolerance; WARN = outside tolerance but explained/expected or logged-only; "
             "FAIL = must be resolved; NOT_TESTABLE = inputs missing (never counted as a pass).", ""]
    counts = pd.Series([v["status"] for v in vals]).value_counts().to_dict()
    lines.append("**Summary:** " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    lines.append("")
    for grp in dict.fromkeys(v["group"] for v in vals):
        lines += [f"## {grp}", "", "| Check | Status | n | Exceptions | Tolerance | Detail |", "|---|---|---|---|---|---|"]
        for v in [x for x in vals if x["group"] == grp]:
            lines.append(f"| {v['description']} | **{v['status']}** | {v['n']} | {v['n_exceptions']} | {v['tolerance']} | "
                         f"{v['detail'].replace('|', '/')} |")
        lines.append("")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "validation_report.md").write_text("\n".join(lines))


def standalone_html():
    """Single-file app with all JSON inlined (for sharing / offline)."""
    html = (SITE / "index.html").read_text()
    blobs = {p.stem: json.loads(p.read_text()) for p in DATA.glob("*.json")}
    inline = "<script>window.__INLINE_DATA__=" + json.dumps(blobs, ensure_ascii=False, separators=(",", ":")) + ";</script>"
    (SITE / "tracker_standalone.html").write_text(html.replace("<!--INLINE_DATA-->", inline))


def main(offline: bool = False):
    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = {}
    if not offline:
        from . import ingest
        new = ingest.run_all()
        if len(new):
            store.append_fetched(new)
        status = ingest.STATUS
    else:
        status = json.loads((DATA / "source_status.json").read_text()) if (DATA / "source_status.json").exists() else {}
    obs = store.load()
    derive.NOTES.clear()
    dv = derive.Deriver(obs)
    der = dv.run()
    if len(obs) or len(der):
        obs = store.replace_derived(der)
    lat = store.latest(obs)
    cache = {}

    def get(sid):
        if sid not in cache:
            cache[sid] = store.series(lat, sid)
        return cache[sid]

    analytics = run_analytics(get)
    vals = checks.run(get)
    vals.append(checks.vintage_order(lat))
    log = update_nowcast_log(analytics.get("live", {}), get, built_at)
    catalog = export_catalog(obs, derive.NOTES)
    dump("catalog.json", catalog)
    dump("analytics.json", {**analytics, "nowcast_log": log.to_dict(orient="records")})
    dump("validation.json", vals)
    dump("breaks.json", breaks())
    dump("bonds.json", bond_tables(get))
    dump("source_status.json", status)
    dump("meta.json", {"built_at": built_at, "n_series": len(catalog), "n_rows": int(len(obs)),
                       "n_raw_files": int(obs["raw_file"].replace("", np.nan).nunique()) if len(obs) else 0,
                       "derive_notes": derive.NOTES, "offline": offline})
    write_validation_md(vals, built_at)
    if (SITE / "index.html").exists():
        standalone_html()
    print(f"built {len(catalog)} series, {len(obs)} rows; validation: "
          f"{pd.Series([v['status'] for v in vals]).value_counts().to_dict()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--replay", action="store_true")
    a = ap.parse_args()
    if a.replay:
        os.environ["CETRACKER_REPLAY"] = "1"
    main(offline=a.offline)
