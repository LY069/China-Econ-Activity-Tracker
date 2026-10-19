"""External validation: identities, cross-source reconciliation, published-y/y reproduction,
physical-proxy agreement. Each check returns a uniform record:

    {id, group, description, status: PASS|FAIL|WARN|NOT_TESTABLE, n, n_exceptions,
     tolerance, detail, exceptions: [...]}

NOT_TESTABLE is used when inputs are missing; it is never counted as a pass.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..transform import ytd as Y, credit as C, fiscal as F


def _rec(cid, group, desc, status, n=0, nexc=0, tol="", detail="", exceptions=None):
    return {"id": cid, "group": group, "description": desc, "status": status, "n": int(n),
            "n_exceptions": int(nexc), "tolerance": tol, "detail": detail,
            "exceptions": (exceptions or [])[:50]}


def _missing(cid, group, desc, need):
    return _rec(cid, group, desc, "NOT_TESTABLE", detail=f"missing inputs: {', '.join(need)}")


def _exc_list(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    d = df[df["exception"]]
    return [{"period": str(i.date()), **{c: (None if pd.isna(d.loc[i, c]) else round(float(d.loc[i, c]), 3)) for c in cols}}
            for i in d.index]


def tsf_annual(get):
    cid, group = "recon:tsf_annual", "Cross-source reconciliation"
    desc = "TSF annual total: sum of 12 monthly flows in the PBoC table vs full-year figure in the December release text"
    t, ytd = get("tsf.flow"), get("tsf_text.flow_ytd")
    if t.empty or ytd.empty:
        return _missing(cid, group, desc, [x for x, s in (("tsf.flow", t), ("tsf_text.flow_ytd", ytd)) if s.empty])
    dec = ytd[ytd.index.month == 12]
    rows = {}
    for d, v in dec.items():
        yr = t[t.index.year == d.year]
        if len(yr) == 12:
            rows[d] = (yr.sum(), v)
    if not rows:
        return _missing(cid, group, desc, ["no complete year in both"])
    df = pd.DataFrame(rows, index=["computed", "published"]).T
    df["diff"] = df["computed"] - df["published"]
    # tolerance = half the last printed digit: 0.01 tn (RMB 10bn) normally, 0.1 tn when the annual
    # report rounds to one decimal (a value that is a multiple of RMB 100bn is treated as such)
    # The December figure is a first release; PBoC later revises annual totals slightly (2023: 35.59 trn
    # first printed, 35.58 trn implied by the 2024 report's '比上年少3.32万亿'). Beyond rounding but within
    # 0.1% of the total is a revision (WARN); more than that is a parsing or unit error (FAIL).
    df["tol"] = [50.0 if round(v) % 100 == 0 else 10.0 for v in df["published"]]
    df["exception"] = df["diff"].abs() > df["tol"]
    hard = (df["diff"].abs() > df["tol"] + 0.001 * df["published"].abs()).any()
    n = int(df["exception"].sum())
    st = "PASS" if n == 0 else ("FAIL" if hard else "WARN")
    return _rec(cid, group, desc, st, len(df), n, "rounding (10 or 50 bn); revisions up to 0.1% of the total WARN",
                f"max |diff| {df['diff'].abs().max():.1f} bn over {len(df)} year(s)", _exc_list(df, ["computed", "published", "diff"]))


def identity(get, cid, desc, total, parts, tol_abs, tol_rel=0.0, group="Accounting identity", warn_only=False):
    need = [total, *parts]
    if any(get(s).empty for s in need):
        return _missing(cid, group, desc, [s for s in need if get(s).empty])
    df = C.sum_identity(get(total), {p: get(p) for p in parts}, tol_abs, tol_rel)
    if df.empty:
        return _missing(cid, group, desc, ["no overlapping periods"])
    nexc = int(df["exception"].sum())
    status = "PASS" if nexc == 0 else ("WARN" if warn_only else "FAIL")
    return _rec(cid, group, desc, status, len(df), nexc, f"abs {tol_abs} bn / rel {tol_rel:.0%}",
                f"max |diff| {df['diff'].abs().max():.2f} bn", _exc_list(df, ["total", "sum_parts", "diff"]))


def pair_check(get, cid, group, desc, a_id, b_id, tol_abs, warn_only=False):
    a, b = get(a_id), get(b_id)
    if a.empty or b.empty:
        return _missing(cid, group, desc, [x for x, s in ((a_id, a), (b_id, b)) if s.empty])
    idx = a.index.intersection(b.index)
    if not len(idx):
        return _missing(cid, group, desc, ["no overlapping periods"])
    df = pd.DataFrame({"computed": a[idx], "published": b[idx]})
    df["diff"] = df["computed"] - df["published"]
    df["exception"] = df["diff"].abs() > tol_abs
    n = int(df["exception"].sum())
    st = "PASS" if n == 0 else ("WARN" if warn_only else "FAIL")
    return _rec(cid, group, desc, st, len(df), n, f"{tol_abs}", f"mean |diff| {df['diff'].abs().mean():.3f}; max {df['diff'].abs().max():.3f}",
                _exc_list(df, ["computed", "published", "diff"]))


def coverage(get, sid, janfeb: bool):
    """Months missing between a series' first and last observation (January excluded for NBS
    Jan-Feb releases). A gap is a missing release or a parse failure; it is reported, not filled."""
    cid, desc = f"coverage:{sid}", f"No missing months between first and last observation: {sid}"
    s = get(sid)
    if s.empty:
        return _missing(cid, "Coverage", desc, [sid])
    full = pd.date_range(s.index.min(), s.index.max(), freq="ME")
    if janfeb:
        full = full[full.month != 1]
    miss = full.difference(s.index)
    return _rec(cid, "Coverage", desc, "PASS" if len(miss) == 0 else "WARN", len(full), len(miss), "0 missing",
                f"{s.index.min().date()} to {s.index.max().date()}", [{"period": str(m.date())} for m in miss])


PLAUSIBLE = {   # (series, min |median|, max |value|) in RMB bn: catches unit / block errors
    "tsf.flow": (500, 20000), "tsf.rmb_loans": (300, 15000), "loans_stock.total": (20000, 600000),
    "money.m2": (50000, 1000000), "retail.level": (1500, 10000), "fai.ytd": (1000, 70000),
    "lgb.total": (50, 3000), "gdp.nominal_ytd": (10000, 200000),
}


def plausibility(get) -> list[dict]:
    """Magnitude bounds for key series plus a robust outlier screen (|x - median| > 10 MAD of
    month-on-month changes). Out-of-bounds magnitudes FAIL; outliers WARN for review."""
    out = []
    for sid, (lo_med, hi_abs) in PLAUSIBLE.items():
        cid, desc = f"plausible:{sid}", f"Magnitude plausible (median |x| >= {lo_med}, all |x| <= {hi_abs} RMB bn): {sid}"
        s = get(sid)
        if s.empty:
            out.append(_missing(cid, "Plausibility", desc, [sid]))
            continue
        bad = s[(s.abs() > hi_abs)]
        med_ok = s.abs().median() >= lo_med
        # windows of 12 months with an implausibly small median (a block in the wrong unit)
        roll = s.abs().rolling(12, min_periods=6).median()
        small = roll[roll < lo_med / 5]
        exc = [{"period": str(t.date()), "value": round(float(v), 3)} for t, v in bad.items()]
        exc += [{"period": str(t.date()), "rolling_median_abs": round(float(v), 3)} for t, v in small.items()]
        ok = med_ok and not len(bad) and not len(small)
        out.append(_rec(cid, "Plausibility", desc, "PASS" if ok else "FAIL", len(s), len(exc), f"[{lo_med}, {hi_abs}]",
                        f"median |x| {s.abs().median():.1f}", exc))
    return out


def ytd_roundtrip(get, ytd_id):
    """Single months (Jan/Feb split) must cumulate back to published YTD. Only months whose
    whole within-year chain is present are testable; months after a data gap are counted and
    reported (a missing release is a coverage problem, not an identity failure)."""
    desc = f"Single months (Jan/Feb split) sum back to published YTD: {ytd_id}"
    cid = f"ytd_roundtrip:{ytd_id}"
    ytd = get(ytd_id)
    if ytd.empty:
        return _missing(cid, "YTD consistency", desc, [ytd_id])
    sm, fl = Y.single_month_from_ytd(ytd)
    sp, _ = Y.split_janfeb(sm, fl)
    testable = []
    for t in ytd.index:
        months = pd.date_range(pd.Timestamp(t.year, 1, 31), t, freq="ME")
        if all(m in sp.index and pd.notna(sp[m]) for m in months):
            testable.append(t)
    gaps = len(ytd) - len(testable)
    if not testable:
        return _missing(cid, "YTD consistency", desc, ["no month with a complete YTD chain"])
    recon = sp.groupby(sp.index.year).cumsum()
    idx = pd.DatetimeIndex(testable)
    df = pd.DataFrame({"published": ytd[idx], "rebuilt": recon[idx]})
    df["diff"] = (df["rebuilt"] - df["published"]).abs()
    df["exception"] = df["diff"] > 1e-6 * df["published"].abs().clip(lower=1)
    nexc = int(df["exception"].sum())
    return _rec(cid, "YTD consistency", desc, "PASS" if nexc == 0 else "FAIL", len(df), nexc, "exact (1e-6 rel)",
                f"{gaps} month(s) not testable (missing earlier release in the same year)",
                _exc_list(df, ["published", "rebuilt", "diff"]))


def yoy_reproduction(get, ytd_id, ytd_yoy_id, pub_yoy_id, tol_pp=0.15):
    cid = f"yoy_repro:{pub_yoy_id}"
    desc = f"Published single-month y/y reproduced from YTD levels + YTD y/y (comparable basis): {pub_yoy_id}"
    need = [ytd_id, ytd_yoy_id, pub_yoy_id]
    if any(get(s).empty for s in need):
        return _missing(cid, "Published y/y reproduction", desc, [s for s in need if get(s).empty])
    ytd, g = get(ytd_id), get(ytd_yoy_id)
    cy = Y.comparable_single_month_yoy(ytd, g)
    pub = get(pub_yoy_id)
    idx = cy.index.intersection(pub.index)
    idx = idx[idx.month > 2]
    # Published YTD growth is rounded to 0.1pp, so each implied base B = YTD/(1+g) carries an
    # error up to B*0.0005/(1+g). The single-month base B_t - B_{t-1} inherits both errors; the
    # tolerance is that propagated bound plus the 0.05pp rounding of the published figure.
    base = Y.implied_base(ytd, g)
    tol = {}
    for t in idx:
        prev = t - pd.offsets.MonthEnd(1)
        if prev in base.index and t in base.index:
            sm_base = base[t] - base[prev]
            err = (base[t] * 0.0005 / (1 + g[t] / 100) + base[prev] * 0.0005 / (1 + g[prev] / 100))
            tol[t] = (err / sm_base) * (1 + cy[t] / 100) * 100 + 0.05
    df = pd.DataFrame({"computed": cy[idx], "published": pub[idx]})
    df["diff"] = df["computed"] - df["published"]
    df["tol"] = pd.Series(tol).reindex(idx).fillna(tol_pp)
    df["exception"] = df["diff"].abs() > df["tol"]
    nexc = int(df["exception"].sum())
    status = "PASS" if nexc == 0 else ("WARN" if nexc / max(len(df), 1) < 0.1 else "FAIL")
    return _rec(cid, "Published y/y reproduction", desc, status, len(df), nexc,
                "rounding bound propagated from 0.1pp-rounded YTD growth (+0.05pp)",
                f"mean |diff| {df['diff'].abs().mean():.3f} pp; mean tolerance {df['tol'].mean():.3f} pp",
                _exc_list(df, ["computed", "published", "diff", "tol"]))


def naive_gap(get, level_id, pub_yoy_id, tol_pp=0.5):
    """Logged, not pass/fail: how far naive y/y from levels is from published y/y."""
    cid = f"yoy_gap_log:{level_id}"
    desc = f"Gap between y/y computed from published levels and published y/y: {level_id}"
    if get(level_id).empty or get(pub_yoy_id).empty:
        return _missing(cid, "Published y/y reproduction", desc, [level_id, pub_yoy_id])
    df = Y.yoy_gap_log(get(level_id), get(pub_yoy_id), tol_pp)
    df = df[df.index.month > 2]
    return _rec(cid, "Published y/y reproduction", desc, "WARN" if df["exception"].any() else "PASS",
                len(df), int(df["exception"].sum()), f"{tol_pp} pp (logged; NBS comparable-basis adjustments expected)",
                f"mean gap {df['gap_pp'].mean():.2f} pp, max |gap| {df['gap_pp'].abs().max():.2f} pp",
                _exc_list(df.rename(columns={"gap_pp": "diff"}), ["computed_yoy", "published_yoy", "diff"]))


def tier_reproduction(get, tol_pp=0.1):
    out = []
    for seg in ("new", "used"):
        for tier in ("tier1", "tier2", "tier3"):
            for h in ("mom", "yoy"):
                built, text = f"hp.{seg}.{tier}.{h}", f"nbs_text.hp.{seg}.{tier}.{h}"
                cid = f"tier_repro:{built}"
                desc = f"Tracker tier aggregate vs NBS release text: {built}"
                if get(built).empty or get(text).empty:
                    out.append(_missing(cid, "Published y/y reproduction", desc, [s for s in (built, text) if get(s).empty]))
                    continue
                a, b = get(built), get(text)
                idx = a.index.intersection(b.index)
                df = pd.DataFrame({"computed": a[idx].round(2), "published": b[idx]})
                df["diff"] = df["computed"] - df["published"]
                df["exception"] = df["diff"].abs() > tol_pp
                nexc = int(df["exception"].sum())
                out.append(_rec(cid, "Published y/y reproduction", desc, "PASS" if nexc == 0 else "FAIL", len(df), nexc,
                                f"{tol_pp} pp", f"mean |diff| {df['diff'].abs().mean():.3f}",
                                _exc_list(df, ["computed", "published", "diff"])))
    return out


def cgb_reconciliation(get, tol_rel=0.15):
    """Implied CGB net financing (TSF govt bonds - MOF local net) vs NPC central quotas, annual."""
    cid = "recon:tsf_govt_vs_mof_lgb"
    desc = ("PBoC TSF government-bond line minus MOF local-bond net issuance = implied central net financing; "
            "annual total vs NPC central deficit + special CGB quotas")
    need = ["tsf.govt_bonds", "lgb.total", "lgb.redemption"]
    if any(get(s).empty for s in need):
        return _missing(cid, "Cross-source reconciliation", desc, [s for s in need if get(s).empty])
    imp = get("tsf.govt_bonds") - (get("lgb.total") - get("lgb.redemption"))
    imp = imp.dropna()
    ann = imp.groupby(imp.index.year).agg(["sum", "count"])
    rows = []
    for yr, r in ann.iterrows():
        if r["count"] < 12:
            continue
        q = F.quotas().get(int(yr), {})
        target = sum(v for k, v in q.items() if k in ("cgb_deficit", "ultra_long_special_cgb", "bank_recap_special_cgb")
                     and v is not None) + sum((q.get("extra") or {}).values())
        if not target or q.get("cgb_deficit") is None:
            continue
        rows.append({"period": str(yr), "implied_cgb_net": round(r["sum"], 1), "npc_central_quota": target,
                     "rel_diff": round(r["sum"] / target - 1, 3)})
    if not rows:
        return _rec(cid, "Cross-source reconciliation", desc, "NOT_TESTABLE", detail="no complete year with quota")
    exc = [r for r in rows if abs(r["rel_diff"]) > tol_rel]
    return _rec(cid, "Cross-source reconciliation", desc, "PASS" if not exc else "WARN", len(rows), len(exc),
                f"{tol_rel:.0%} (quota != net financing exactly: CGB net also funds rollovers of savings bonds etc.)",
                "; ".join(f"{r['period']}: {r['implied_cgb_net']} vs {r['npc_central_quota']}" for r in rows), exc)


def stock_flow_consistency(get, tol_rel=0.02):  # noqa: ARG001
    cid = "recon:tsf_stock_yoy"
    desc = "TSF stock y/y computed from published stocks vs published stock y/y (PBoC comparable basis)"
    if get("tsf.stock").empty or get("tsf.stock_yoy").empty:
        return _missing(cid, "Cross-source reconciliation", desc, ["tsf.stock", "tsf.stock_yoy"])
    calc = C.stock_yoy(get("tsf.stock"))
    pub = get("tsf.stock_yoy")
    idx = calc.index.intersection(pub.index)
    df = pd.DataFrame({"computed": calc[idx], "published": pub[idx]})
    df["diff"] = df["computed"] - df["published"]
    df["exception"] = df["diff"].abs() > 0.3
    return _rec(cid, "Cross-source reconciliation", desc, "PASS" if not df["exception"].any() else "WARN",
                len(df), int(df["exception"].sum()), "0.3 pp", f"mean |diff| {df['diff'].abs().mean():.2f} pp",
                _exc_list(df, ["computed", "published", "diff"]))


def proxy_agreement(get, target="fai.ytd~cyoy", proxies=("proxy.cement_yoy", "proxy.steel_yoy", "proxy.power_yoy"),
                    window=36, min_share=0.6):
    """Direction agreement of 3-month changes (3mma) between the investment signal and physical proxies."""
    out = []
    t = get(target)
    for p in proxies:
        cid = f"proxy:{p}"
        desc = f"Direction of 3m change agrees: {target} vs {p} (last {window} months)"
        if t.empty or get(p).empty:
            out.append(_missing(cid, "Physical proxies", desc, [s for s in (target, p) if get(s).empty]))
            continue
        a = t.rolling(3).mean().diff(3)
        b = get(p).rolling(3).mean().diff(3)
        d = pd.concat({"t": a, "p": b}, axis=1).dropna().iloc[-window:]
        if len(d) < 12:
            out.append(_missing(cid, "Physical proxies", desc, ["<12 overlapping months"]))
            continue
        agree = (np.sign(d["t"]) == np.sign(d["p"]))
        share = float(agree.mean())
        recent = d.iloc[-6:]
        div = recent[np.sign(recent["t"]) != np.sign(recent["p"])]
        out.append(_rec(cid, "Physical proxies", desc, "PASS" if share >= min_share else "WARN", len(d), int((~agree).sum()),
                        f"agreement >= {min_share:.0%}",
                        f"agreement {share:.0%}; corr of 3m changes {d['t'].corr(d['p']):.2f}; "
                        f"{len(div)} divergent month(s) in last 6 -> explain in commentary",
                        [{"period": str(i.date()), "target_3m_chg": round(r['t'], 2), "proxy_3m_chg": round(r['p'], 2)}
                         for i, r in div.iterrows()]))
    return out


def run(get) -> list[dict]:
    R = []
    R.append(identity(get, "id:hh_loan_stock", "Household loan stock = short + medium/long (PBoC credit table)",
                      "loans_stock.hh_total", ["loans_stock.hh_short", "loans_stock.hh_medlong"], tol_abs=0.1))
    R.append(identity(get, "id:corp_loan_stock",
                      "Corporate loan stock = short + medium/long + bills + leases + advances (PBoC credit table)",
                      "loans_stock.corp_total", ["loans_stock.corp_short", "loans_stock.corp_medlong", "loans_stock.corp_bills",
                                                 "loans_stock.corp_leases", "loans_stock.corp_advances"], tol_abs=0.1))
    R.append(identity(get, "id:loans_sector",
                      "Domestic RMB loans: total vs household + corporate + NBFI (residual = overseas loans)",
                      "loans_stock.total", ["loans_stock.hh_total", "loans_stock.corp_total", "loans_stock.nbfi"],
                      tol_abs=50, tol_rel=0.02, warn_only=True))
    R.append(identity(get, "id:tsf_components",
                      "TSF flow = published components (residual = PBoC's unpublished 'other' category: WARN, logged)",
                      "tsf.flow", ["tsf.rmb_loans", "tsf.fx_loans", "tsf.entrusted", "tsf.trust", "tsf.undiscounted_ba",
                                   "tsf.corp_bonds", "tsf.govt_bonds", "tsf.equity", "tsf.abs", "tsf.writeoffs"],
                      tol_abs=50, tol_rel=0.05, warn_only=True))
    R.append(pair_check(get, "recon:loans_table_vs_text", "Cross-source reconciliation",
                        "New household loans: PBoC credit-table stock change vs PBoC release text (YTD-differenced)",
                        "loans.hh_total", "loans_text.hh_total", tol_abs=30, warn_only=True))
    R.append(pair_check(get, "recon:loans_corp_table_vs_text", "Cross-source reconciliation",
                        "New corporate loans: PBoC credit-table stock change vs PBoC release text",
                        "loans.corp_total", "loans_text.corp_total", tol_abs=50, warn_only=True))
    # The text is PBoC's first release; the table is the current vintage, in which PBoC re-allocates
    # flows across months of a year. Monthly differences are revisions (logged); the annual total
    # must agree (December YTD in the text is rounded to RMB 10bn).
    R.append(pair_check(get, "recon:tsf_table_vs_text", "Cross-source reconciliation",
                        "TSF monthly flow: current-vintage PBoC table vs first-release text (differences = revisions, logged)",
                        "tsf.flow", "tsf_text.flow", tol_abs=10, warn_only=True))
    R.append(tsf_annual(get))
    R.append(pair_check(get, "recon:m2_yoy", "Published y/y reproduction",
                        "M2 y/y computed from PBoC money-supply levels vs published", "money.m2~yoy_calc", "money.m2_yoy",
                        tol_abs=0.15, warn_only=True))
    R.append(pair_check(get, "recon:m1_yoy", "Published y/y reproduction",
                        "M1 y/y computed from levels vs published (2025 redefinition: 2025 exceptions expected)",
                        "money.m1~yoy_calc", "money.m1_yoy", tol_abs=0.15, warn_only=True))
    R.append(identity(get, "id:lgb_total", "Local bonds: new + refinancing = total issuance", "lgb.total",
                      ["lgb.new_total", "lgb.refi_total"], tol_abs=1.0))
    R.append(identity(get, "id:lgb_new", "Local bonds: new general + new special = new total reported by MOF",
                      "lgb.new_total_reported", ["lgb.general_new", "lgb.special_new"], tol_abs=0.1))
    R.append(identity(get, "id:lgb_refi", "Local bonds: refinancing general + special = refinancing total",
                      "lgb.refi_total", ["lgb.refi_general", "lgb.refi_special"], tol_abs=1.0))
    R.append(pair_check(get, "id:retail_month_vs_ytd", "Accounting identity",
                        "Published single-month retail sales = change in published YTD retail sales (months 3-12)",
                        "retail.ytd~sm", "retail.level", tol_abs=0.15))
    for sid, janfeb in (("retail.ytd", True), ("fai.ytd", True), ("ip.yoy", True), ("prices.cpi_yoy", False),
                        ("prices.ppi_yoy", False), ("fai.re_ytd", True), ("re.sales_ytd", True), ("tsf.flow", False),
                        ("loans_stock.total", False), ("money.m2", False), ("lgb.total", False)):
        R.append(coverage(get, sid, janfeb))
    R.extend(plausibility(get))
    R.append(yoy_reproduction(get, "retail.ytd", "retail.ytd_yoy", "retail.yoy"))
    R.append(naive_gap(get, "retail.level", "retail.yoy"))
    R.append(naive_gap(get, "fai.ytd", "fai.ytd_yoy"))
    R.extend(tier_reproduction(get))
    R.append(cgb_reconciliation(get))
    R.append(stock_flow_consistency(get))
    R.append(_rec("recon:lgb_mof_vs_celma", "Cross-source reconciliation",
                  "MOF monthly local-bond totals vs bond-by-bond CELMA/ChinaBond sum", "NOT_TESTABLE",
                  detail="bond-level source not ingested (see source status)"))
    R.extend(proxy_agreement(get))
    return R
