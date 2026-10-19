"""Synthetic data with KNOWN properties, for testing the pipeline machinery only.

Nothing here is real data and it is never written to data/store or site/data.
Planted properties the tests check:
  * YTD/levels/y/y relationships mimic NBS publication (no January, comparable-basis y/y
    differing from level-based y/y because the prior-year base is revised each January)
  * accounting identities hold exactly (loans, local bonds)
  * NDRC approvals lead single-month FAI growth by exactly 3 months
  * tier aggregates are means of city indices; "release text" tier values are rounded
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cetracker import store
from cetracker.transform.housing import tiers

MONTHS = pd.date_range("2012-01-31", "2026-08-31", freq="ME")


def _rows(sid, s, flags=""):
    prov = store.Provenance("SYNTH", "primary", "synthetic://test", "synthetic", "2026-10-01T00:00:00Z", "2026-10-01")
    return store.make_rows(sid, s, prov, flags)


def make(seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(MONTHS)
    out = []
    seas = np.tile([0.85, 0.80, 1.00, 0.97, 1.00, 1.05, 0.98, 1.00, 1.03, 1.05, 1.06, 1.20], n // 12 + 1)[:n]

    # --- NDRC approvals: AR(1) driver; FAI single-month growth responds with a 3-month lag
    a = np.zeros(n)
    for t in range(1, n):
        a[t] = 0.5 * a[t - 1] + rng.normal()
    ndrc = pd.Series(100 + 15 * a, MONTHS)
    growth = np.zeros(n)
    for t in range(1, n):
        growth[t] = 0.3 * growth[t - 1] + (1.5 * a[t - 3] if t >= 3 else 0) + rng.normal(0, 0.8)
    # FAI single-month levels: trend * seasonality * (1 + growth vs last year)
    fai_sm = np.zeros(n)
    for t in range(n):
        base = 3000 * seas[t] if t < 12 else fai_sm[t - 12]
        fai_sm[t] = base * (1 + (0.05 + growth[t] / 100))
    fai_sm = pd.Series(fai_sm, MONTHS)
    out.append(_rows("ndrc.approvals_value", ndrc))
    out.append(_rows("proxy.cement_yoy", pd.Series(0.8 * growth + rng.normal(0, 1, n), MONTHS)))

    def ytd_publish(sm: pd.Series, prefix: str, revise: float):
        ytd = sm.groupby(sm.index.year).cumsum()
        # comparable basis: prior-year base revised by factor (1-revise) each January
        lag = ytd.shift(12)
        comp_base = lag * (1 - revise)
        yoy = (ytd / comp_base - 1) * 100
        pub = ytd[ytd.index.month != 1]
        out.append(_rows(f"{prefix}.ytd", pub))
        out.append(_rows(f"{prefix}.ytd_yoy", yoy[yoy.index.month != 1].dropna()))
        return ytd, comp_base

    ytd_fai, _ = ytd_publish(fai_sm, "fai", 0.03)

    # retail: single-month published (no Jan; Feb = Jan+Feb), ytd, both y/y
    r_sm = pd.Series(3500 * seas * np.exp(0.006 * np.arange(n)) * (1 + rng.normal(0, 0.01, n)), MONTHS)
    ytd_r, base_r = ytd_publish(r_sm, "retail", 0.01)
    lvl = r_sm.copy()
    jf = lvl.index.month == 2
    lvl[jf] = r_sm[jf].values + r_sm[lvl.index.month == 1].values
    out.append(_rows("retail.level", lvl[lvl.index.month != 1]))
    sm_base = base_r - base_r.groupby(base_r.index.year).shift(1).fillna(0)
    r_yoy = (r_sm / sm_base - 1) * 100
    r_yoy[jf] = ((ytd_r / base_r - 1) * 100)[jf]
    out.append(_rows("retail.yoy", r_yoy[r_yoy.index.month != 1].dropna()))

    out.append(_rows("prices.cpi_goods_yoy", pd.Series(1 + rng.normal(0, 0.5, n), MONTHS)))
    out.append(_rows("prices.ppi_yoy", pd.Series(-1 + np.cumsum(rng.normal(0, 0.3, n)) * 0.2, MONTHS)))
    out.append(_rows("ip.yoy", pd.Series(5 + 0.5 * growth + rng.normal(0, 0.5, n), MONTHS)))

    # --- GDP quarterly
    q = pd.date_range("2012-03-31", "2026-06-30", freq="QE")
    gnom = pd.Series(130000 * np.exp(0.02 * np.arange(len(q))), q)
    out.append(_rows("gdp.nominal_q", gnom))
    out.append(_rows("gdp.real_yoy", pd.Series(5 + rng.normal(0, 0.3, len(q)), q)))
    for p, sh in [("primary", 0.07), ("secondary", 0.38), ("tertiary", 0.55)]:
        out.append(_rows(f"gdp.{p}_nominal_q", gnom * sh))
        out.append(_rows(f"gdp.{p}_real_yoy", pd.Series(5 + rng.normal(0, 0.5, len(q)), q)))

    # --- credit (RMB bn): exact identities
    hs, hm = rng.normal(50, 80, n), rng.normal(300, 150, n)
    cs, cm, cb = rng.normal(200, 300, n), rng.normal(600, 200, n), rng.normal(50, 200, n)
    nb = rng.normal(20, 60, n)
    out += [_rows("loans.hh_short", pd.Series(hs, MONTHS)), _rows("loans.hh_medlong", pd.Series(hm, MONTHS)),
            _rows("loans.hh_total", pd.Series(hs + hm, MONTHS)),
            _rows("loans.corp_short", pd.Series(cs, MONTHS)), _rows("loans.corp_medlong", pd.Series(cm, MONTHS)),
            _rows("loans.corp_bills", pd.Series(cb, MONTHS)), _rows("loans.corp_total", pd.Series(cs + cm + cb, MONTHS)),
            _rows("loans.nbfi", pd.Series(nb, MONTHS)), _rows("loans.total", pd.Series(hs + hm + cs + cm + cb + nb, MONTHS))]
    comps = {k: rng.normal(mu, sd, n) for k, mu, sd in [("rmb_loans", 1300, 500), ("fx_loans", 0, 30),
             ("entrusted", -20, 50), ("trust", -10, 50), ("undiscounted_ba", 0, 200), ("corp_bonds", 200, 150),
             ("govt_bonds", 600, 300), ("equity", 50, 20)]}
    for k, v in comps.items():
        out.append(_rows(f"tsf.{k}", pd.Series(v, MONTHS)))
    out.append(_rows("tsf.flow", pd.Series(sum(comps.values()) + rng.normal(30, 5, n), MONTHS)))
    out.append(_rows("money.m1_yoy", pd.Series(5 + np.cumsum(rng.normal(0, 0.4, n)) * 0.3, MONTHS)))
    out.append(_rows("money.m2_yoy", pd.Series(9 + rng.normal(0, 0.3, n), MONTHS)))

    # --- local bonds (RMB bn)
    g_, s_ = np.abs(rng.normal(60, 30, n)), np.abs(rng.normal(300, 150, n)) * (seas > 0.9)
    rg, rs = np.abs(rng.normal(80, 30, n)), np.abs(rng.normal(60, 30, n))
    red = np.abs(rng.normal(250, 60, n))
    out += [_rows("lgb.general_new", pd.Series(g_, MONTHS)), _rows("lgb.special_new", pd.Series(s_, MONTHS)),
            _rows("lgb.refi_general", pd.Series(rg, MONTHS)), _rows("lgb.refi_special", pd.Series(rs, MONTHS)),
            _rows("lgb.refi_total", pd.Series(rg + rs, MONTHS)), _rows("lgb.total", pd.Series(g_ + s_ + rg + rs, MONTHS)),
            _rows("lgb.redemption", pd.Series(red, MONTHS))]

    # --- 70 cities (index, previous month = 100) + rounded "release text" tier figures
    cities = sum(tiers().values(), [])
    tier_of = {c: t for t, cs_ in tiers().items() for c in cs_}
    drift = {"tier1": 0.1, "tier2": -0.1, "tier3": -0.2}
    hm_ = MONTHS[MONTHS >= "2015-01-31"]
    city = {}
    for c in cities:
        v = np.round(100 + drift[tier_of[c]] + rng.normal(0, 0.3, len(hm_)), 1)
        city[c] = pd.Series(v, hm_)
        out.append(_rows(f"city70.new.mom.{c}", city[c]))
    df = pd.DataFrame(city)
    for t, cs_ in tiers().items():
        out.append(_rows(f"nbs_text.hp.new.{t}.mom", (df[cs_] - 100).mean(axis=1).round(1)))
    return pd.concat(out, ignore_index=True)
