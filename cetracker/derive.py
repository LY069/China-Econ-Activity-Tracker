"""Derived series: everything computed from fetched rows, with lineage on every row.

Derived ids use "<base>~<transform>" so the app can group them with their source series:
    ~sm            single month from YTD (Feb = Jan-Feb combined, flagged)
    ~sm_split      single month with Jan/Feb split evenly (analytics use)
    ~cyoy          official-consistent (comparable-basis) single-month y/y
    ~nyoy          naive y/y from levels (for the gap log only)
    ~sa / ~sa_mom / ~saar3   own seasonal adjustment (X-13, STL cross-check in ~sa_stl)
    ~real          deflated y/y
    ~3mma          trailing 3-month mean (one-sided, real-time safe)
    ~12m           trailing 12-month sum
    ~impulse       Δ12 of the 12m sum, % of trailing 4Q nominal GDP
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import store
from .transform import ytd as Y, seasonal as SA, credit as C, deflate as D, housing as H

NOTES: dict[str, str] = {}



# months where consecutive PBoC credit-table stocks are on different statistical scopes
STOCK_SCOPE_BREAKS = {"2023-01-31": "PBOC_SCOPE_2023"}
# TSF definition segments (config/breaks.yaml TSF_DEF_2017): flows are on the current definition
# from 2017-01 (PBoC's restated block); stock tables switch definition at 2018-01 and 2019-01, and
# their scope widens at 2023-01 (PBOC_SCOPE_2023: computed 2023 y/y runs ~0.33pp above published)
TSF_FLOW_CURRENT_DEF = pd.Timestamp("2017-01-31")
TSF_STOCK_DEF_STARTS = ("2018-01-31", "2019-01-31", "2023-01-31")

class Deriver:
    def __init__(self, obs: pd.DataFrame):
        # derived inputs come only from this build (self.cache): reading a previous build's derived rows
        # would make the output depend on the store's history (a derived id read before it is computed)
        lat = store.latest(obs)
        self.obs = lat[lat["source_tier"] != "derived"] if len(lat) else lat
        self.rows: list[pd.DataFrame] = []
        self.cache: dict[str, pd.Series] = {}
        self._vser: dict[str, pd.Series] = {}

    def get(self, sid: str) -> pd.Series:
        if sid in self.cache:
            return self.cache[sid]
        return store.series(self.obs, sid)

    def has(self, *sids) -> bool:
        return all(len(self.get(s).dropna()) > 0 for s in sids)

    def vintages(self, sid: str) -> pd.Series:
        """Per-period vintage dates of a series (fetched: from the store; derived: as computed)."""
        if sid in self._vser:
            return self._vser[sid]
        d = self.obs[self.obs.series_id == sid]
        if d.empty:
            return pd.Series(dtype=object)
        v = pd.Series(d["vintage_date"].values, index=pd.to_datetime(d["period"])).sort_index()
        self._vser[sid] = v
        return v

    def put(self, sid: str, s: pd.Series, transform: str, inputs: list[str], flags="", two_sided: bool = False):
        """Store a derived series with a per-period vintage, so as_of() reconstructs what was
        computable on a date:
        - one-sided transforms: the latest vintage among the inputs' values at or before the period;
          for a month an input structurally never has (January of a Jan-Feb release, split from the
          Jan-Feb figure) the vintage of the input's next observation, which the value depends on;
        - two-sided transforms (seasonal adjustment and anything built on it): every period takes the
          latest input vintage, since each value is estimated on the whole sample;
        - never earlier than the day after the period ends."""
        from .analytics.lags import _never_observed_months
        s = s.replace([np.inf, -np.inf], np.nan).dropna()
        if s.empty:
            return
        self.cache[sid] = s
        per = pd.Series("", index=s.index, dtype=object)
        for i in inputs:
            v = self.vintages(i)
            if v.empty:
                continue
            v = v[~v.index.duplicated(keep="last")]
            if two_sided:
                aligned = pd.Series(v.max(), index=s.index)
            else:
                u = v.reindex(v.index.union(s.index))
                aligned = u.ffill().reindex(s.index).fillna("")
                skip = _never_observed_months(self.get(i))
                if skip:
                    nxt = u.bfill().reindex(s.index)
                    m = s.index.month.isin(list(skip)) & ~s.index.isin(v.index) & nxt.notna().values
                    aligned = aligned.where(~m, nxt)
            per = pd.Series(np.where(aligned.values > per.values, aligned.values, per.values), index=s.index)
        floor = pd.Series((s.index + pd.Timedelta(days=1)).strftime("%Y-%m-%d"), index=s.index)
        per = pd.Series(np.where(per.values < floor.values, floor.values, per.values), index=s.index)
        self._vser[sid] = per
        prov = store.Provenance("tracker", "derived", vintage_date="", lineage=f"{transform}({','.join(inputs)})")
        rows = store.make_rows(sid, s, prov, flags)
        rows["vintage_date"] = per.reindex(pd.to_datetime(rows["period"])).values
        self.rows.append(rows)

    # ------------------------------------------------------------------ activity
    def ytd_family(self, base: str):
        if base.endswith("_ytd"):
            ytd_id, yoy_id = base, f"{base}_yoy"
        else:
            ytd_id, yoy_id = f"{base}.ytd", f"{base}.ytd_yoy"
        if not self.has(ytd_id):
            return
        ytd = self.get(ytd_id)
        sm, fl = Y.single_month_from_ytd(ytd)
        self.put(f"{ytd_id}~sm", sm, "single_month_from_ytd", [ytd_id], fl)
        sp, fl2 = Y.split_janfeb(sm, fl)
        self.put(f"{ytd_id}~sm_split", sp, "split_janfeb_even", [f"{ytd_id}~sm"], fl2)
        if self.has(yoy_id):
            cy = Y.comparable_single_month_yoy(ytd, self.get(yoy_id))
            self.put(f"{ytd_id}~cyoy", cy, "comparable_single_month_yoy", [ytd_id, yoy_id])
            self.put(f"{ytd_id}~cyoy~3mma", cy.rolling(3, min_periods=3).mean(), "trailing_mean_3", [f"{ytd_id}~cyoy"])
        self.put(f"{ytd_id}~sm~nyoy", Y.naive_yoy(sm), "naive_yoy_from_levels", [f"{ytd_id}~sm"])
        if self.has(f"{ytd_id}~cyoy"):
            cl = Y.chain_comparable_level(sp, self.get(f"{ytd_id}~cyoy"))
            self.put(f"{ytd_id}~clevel", cl, "chain_comparable_level[seed=first 12 months]",
                     [f"{ytd_id}~sm_split", f"{ytd_id}~cyoy"])
            self.seasonal(f"{ytd_id}~clevel", janfeb=True)

    def seasonal(self, sid: str, janfeb: bool = False):
        s = self.get(sid).dropna()
        if len(s) < 48:
            return
        full = pd.date_range(s.index.min(), s.index.max(), freq="ME")
        if len(full) != len(s):
            # use longest contiguous recent span
            gaps = np.where(np.diff(s.index.values).astype("timedelta64[D]").astype(int) > 31)[0]
            s = s.iloc[gaps[-1] + 1:] if len(gaps) else s
            if len(s) < 48:
                return
        try:
            prim, cross, note = SA.seasonal_adjust(s, janfeb_combined=janfeb)
        except Exception as e:
            NOTES[sid] = f"SA failed: {e}"
            return
        NOTES[f"{sid}~sa"] = note
        self.put(f"{sid}~sa", prim["sa"], "x13" if note.startswith("X-13") else "stl_holiday", [sid], two_sided=True)
        mom, mflags = SA.sa_mom(prim["sa"]), {}
        if janfeb:
            # January and February are an even split of the Jan-Feb figure, so neither month's own m/m
            # means anything: January is masked and February shows the Jan-Feb average vs December
            sa = prim["sa"]
            for t in mom.index[mom.index.month == 2]:
                jan, dec = t - pd.offsets.MonthEnd(1), t - pd.offsets.MonthEnd(2)
                if jan in sa.index and dec in sa.index:
                    mom[t] = ((sa[jan] + sa[t]) / 2 / sa[dec] - 1) * 100
                    mflags[t] = "janfeb_avg_vs_dec"
            mom = mom[mom.index.month != 1]
        self.put(f"{sid}~sa_mom", mom, "pct_change_1[Jan masked; Feb = Jan-Feb avg vs Dec]" if janfeb else "pct_change_1",
                 [f"{sid}~sa"], mflags, two_sided=True)
        self.put(f"{sid}~saar3", SA.saar_3m(prim["sa"]), "saar_3m3m", [f"{sid}~sa"], two_sided=True)
        if cross is not None:
            self.put(f"{sid}~sa_stl", cross["sa"], "stl_holiday_crosscheck", [sid], two_sided=True)

    def activity(self):
        for base in ("retail", "fai", "fai.re_ytd", "re.starts_ytd", "re.completions_ytd", "re.sales_ytd"):
            self.ytd_family(base)
        if self.has("retail.level", "retail.yoy"):
            # published single-month level has Feb = Jan-Feb combined: split evenly, then chain
            sm = self.get("retail.level")
            fl = pd.Series({t: "janfeb_combined" for t in sm.index if t.month == 2 and (t - pd.offsets.MonthEnd(1)) not in sm.index})
            sp, _ = Y.split_janfeb(sm, fl)
            cl = Y.chain_comparable_level(sp, self.get("retail.yoy"))
            self.put("retail.level~clevel", cl, "chain_comparable_level[seed=first 12 months]", ["retail.level", "retail.yoy"])
            self.seasonal("retail.level~clevel", janfeb=True)
        # real growth
        if self.has("retail.yoy", "prices.cpi_goods_yoy"):
            self.put("retail.yoy~real", D.real_yoy(self.get("retail.yoy"), self.get("prices.cpi_goods_yoy")),
                     "real_yoy[deflator:cpi_goods]", ["retail.yoy", "prices.cpi_goods_yoy"])
        if self.has("fai.ytd~cyoy", "prices.ppi_yoy"):
            self.put("fai.ytd~cyoy~real", D.real_yoy(self.get("fai.ytd~cyoy"), self.get("prices.ppi_yoy")),
                     "real_yoy[deflator_proxy:ppi]", ["fai.ytd~cyoy", "prices.ppi_yoy"])
        if self.has("fai.ytd_yoy", "prices.ppi_yoy"):
            self.put("fai.ytd_yoy~real", D.real_yoy(self.get("fai.ytd_yoy"), D.ytd_average_deflator(self.get("prices.ppi_yoy"))),
                     "real_yoy[deflator_proxy:ppi_ytd_avg]", ["fai.ytd_yoy", "prices.ppi_yoy"])
        for sid in ("ip.yoy", "retail.yoy", "proxy.cement_yoy", "proxy.steel_yoy", "proxy.power_yoy"):
            if self.has(sid):
                self.put(f"{sid}~3mma", self.get(sid).rolling(3, min_periods=3).mean(), "trailing_mean_3", [sid])

    # ------------------------------------------------------------------ GDP
    def gdp(self):
        """Quarterly nominal GDP from YTD releases; sector contributions to YTD real growth."""
        def q_from_ytd(sid):
            # time-based: Q1 = YTD(Mar); Qk = YTD(k) - YTD(k-1) only when that previous quarter exists
            y = self.get(sid).dropna()
            y = y.reindex(pd.date_range(y.index.min(), y.index.max(), freq="QE"))
            prev = y.shift(1)
            q = y - prev
            q[y.index.month == 3] = y[y.index.month == 3]
            return q.dropna()
        if self.has("gdp.nominal_ytd"):
            self.put("gdp.nominal_q", q_from_ytd("gdp.nominal_ytd"), "quarter_from_ytd", ["gdp.nominal_ytd"])
        parts = ["primary", "secondary", "tertiary"]
        if self.has("gdp.nominal_ytd", *[f"gdp.{p}_nominal_ytd" for p in parts], *[f"gdp.{p}_real_ytd_yoy" for p in parts]):
            g = self.get("gdp.nominal_ytd")
            for p in parts:
                share = self.get(f"gdp.{p}_nominal_ytd") / g
                share_lag = share.copy()
                share_lag.index = share_lag.index + pd.offsets.QuarterEnd(4)
                c = share_lag.reindex(share.index) * self.get(f"gdp.{p}_real_ytd_yoy")
                self.put(f"gdp.contrib_{p}", c, "contribution[ytd_share_t-4q*ytd_real_yoy]",
                         ["gdp.nominal_ytd", f"gdp.{p}_nominal_ytd", f"gdp.{p}_real_ytd_yoy"])

    # ------------------------------------------------------------------ credit
    def credit(self):
        # new loans by sector/tenor = change in PBoC RMB credit-table stocks (full history)
        for sid in [x for x in self.obs["series_id"].unique() if x.startswith("loans_stock.")]:
            st = self.get(sid)
            full = st.reindex(pd.date_range(st.index.min(), st.index.max(), freq="ME"))
            k = sid.split(".", 1)[1]
            flow, flags, ins = full.diff(), {}, [sid]
            # stock differences across a change of statistical scope are not flows: use the
            # release-text flow for that month, or leave a gap (config/breaks.yaml)
            for d, brk in STOCK_SCOPE_BREAKS.items():
                d = pd.Timestamp(d)
                if d not in flow.index:
                    continue
                txt = self.get(f"loans_text.{k}") if self.has(f"loans_text.{k}") else pd.Series(dtype=float)
                if d in txt.index:
                    flow[d] = txt[d]
                    flags[d] = f"break:{brk},from_release_text"
                    ins = [sid, f"loans_text.{k}"]
                else:
                    flow[d] = np.nan
            self.put("loans." + k, flow, "flow_from_stock[diff]", ins, flags)
        for code in ("m2", "m1"):
            if self.has(f"money.{code}"):
                lvl = self.get(f"money.{code}")
                ins = [f"money.{code}"]
                if code == "m1" and self.has("money.m1_restated"):
                    # Jan-2025 M1 redefinition: 2024 base replaced by PBoC's restated comparable levels
                    base = lvl.copy()
                    rs = self.get("money.m1_restated")
                    base.loc[rs.index] = rs
                    lag = base.copy()
                    lag.index = lag.index + pd.offsets.MonthEnd(12)
                    idx = lvl.index.intersection(lag.index)
                    yoy = (lvl[idx] / lag[idx] - 1) * 100
                    ins.append("money.m1_restated")
                else:
                    yoy = C.stock_yoy(lvl)
                self.put(f"money.{code}~yoy_calc", yoy, "stock_yoy[restated base where published]", ins)
        if self.has("money.m1_yoy", "money.m2_yoy"):
            # published y/y where PBoC's release states it; elsewhere (before the 2021 start of the
            # release archive, months a release was not parsed) y/y from table levels, flagged
            pub = C.gap(self.get("money.m1_yoy"), self.get("money.m2_yoy"))
            ins, flags = ["money.m1_yoy", "money.m2_yoy"], {}
            if self.has("money.m1~yoy_calc", "money.m2~yoy_calc"):
                calc = C.gap(self.get("money.m1~yoy_calc"), self.get("money.m2~yoy_calc"))
                fill = calc.index.difference(pub.index)
                pub = pd.concat([pub, calc[fill]]).sort_index()
                flags = {d: "from_levels" for d in fill}
                ins += ["money.m1~yoy_calc", "money.m2~yoy_calc"]
            self.put("money.m1_m2_gap", pub, "difference[published y/y, else from levels]", ins, flags)
        if self.has("tsf.flow"):
            # months whose TSF definition predates government bonds (PBoC tables before the 2019
            # redefinition) carry no govt-bond column: ex-govt = total there, flagged.
            tot = self.get("tsf.flow")
            gov = self.get("tsf.govt_bonds").reindex(tot.index)
            if self.has("tsf.lg_special_bonds"):      # 2018 vintage: only LG special bonds in TSF
                gov = gov.fillna(self.get("tsf.lg_special_bonds").reindex(tot.index))
            fl = pd.Series(np.where(gov.isna(), "govt_bonds_not_in_definition", ""), index=tot.index)
            self.put("tsf.flow_ex_govt", tot - gov.fillna(0.0), "difference[govt missing=0]",
                     ["tsf.flow", "tsf.govt_bonds"], fl[fl != ""])
        gdp_ok = self.has("gdp.nominal_q")

        def current_def(sid):
            # TSF flows before 2017 are on the pre-2018 definition (PBoC restated 2017 onward only):
            # rolling sums and impulses use the current-definition segment (break TSF_DEF_2017)
            f = self.get(sid)
            return f[f.index >= TSF_FLOW_CURRENT_DEF] if sid.startswith("tsf.") else f

        for sid in ("tsf.flow", "tsf.flow_ex_govt", "tsf.rmb_loans", "loans.total", "tsf.govt_bonds"):
            if not self.has(sid):
                continue
            f12 = C.rolling12(current_def(sid))
            self.put(f"{sid}~12m", f12, "rolling_sum_12", [sid])
            if gdp_ok:
                self.put(f"{sid}~impulse", C.credit_impulse(current_def(sid), self.get("gdp.nominal_q")),
                         "credit_impulse[d12(sum12)/gdp4q]", [sid, "gdp.nominal_q"])
        if self.has("tsf.stock"):
            # stock tables are never restated: each year's table is on that year's definition (jumps at
            # 2018-01 and 2019-01), so y/y from levels is computed only within a definition segment
            st = self.get("tsf.stock")
            yoy = C.stock_yoy(st)
            cross = pd.Series(False, index=yoy.index)
            for b in TSF_STOCK_DEF_STARTS:
                b = pd.Timestamp(b)
                cross |= (yoy.index >= b) & (yoy.index - pd.offsets.MonthEnd(12) < b)
            self.put("tsf.stock~yoy_calc", yoy[~cross], "stock_yoy[within definition segment]", ["tsf.stock"])
            # denominator: the year-ago stock on today's definition = S_t / (1 + published y/y_t), since
            # PBoC's stock growth is on a comparable basis; levels only where no published y/y exists
            # and both ends lie in the current-definition segment
            base = C.comparable_base(st, self.get("tsf.stock_yoy"), TSF_STOCK_DEF_STARTS[-1])
            for sid in ("tsf.flow", "tsf.flow_ex_govt", "tsf.govt_bonds", "lgb.new_total"):
                if self.has(sid):
                    flow = current_def(sid)
                    if sid == "tsf.govt_bonds" and self.has("tsf.lg_special_bonds"):
                        flow = flow.combine_first(current_def("tsf.lg_special_bonds"))
                    self.put(f"{sid}~impulse_stk", C.impulse_vs_base(flow, base),
                             "impulse_vs_stock[d12(sum12)/comparable tsf_stock_t-12]",
                             [sid, "tsf.stock", "tsf.stock_yoy"])

    # ------------------------------------------------------------------ bonds
    def bonds(self):
        if self.has("lgb.refi_special"):
            # MOF never states the hidden-debt swap amount. The 2024-26 swap programme is issued as
            # refinancing special bonds, so from 2024-11 refi_special is shown as a labelled proxy.
            rs = self.get("lgb.refi_special")
            self.put("lgb.refi_swap_proxy", rs[rs.index >= "2024-11-30"], "proxy[refi_special since 2024-11]",
                     ["lgb.refi_special"], "proxy_not_reported_by_mof")
        if self.has("lgb.general_new", "lgb.special_new") or self.has("lgb.new_total_reported"):
            # MOF's stated new-issuance total ("发行新增债券X亿元", from 2018-10); before late 2021 the
            # text does not split it by type, so general + special is only the fallback
            nm = self.get("lgb.general_new").add(self.get("lgb.special_new"), fill_value=np.nan) \
                if self.has("lgb.general_new", "lgb.special_new") else pd.Series(dtype=float)
            rep = self.get("lgb.new_total_reported")
            tot = rep.combine_first(nm) if len(rep) else nm
            self.put("lgb.new_total", tot, "reported[else general+special]",
                     ["lgb.new_total_reported", "lgb.general_new", "lgb.special_new"])
        if self.has("lgb.total", "lgb.redemption"):
            self.put("lgb.net", self.get("lgb.total") - self.get("lgb.redemption"), "difference",
                     ["lgb.total", "lgb.redemption"])
        gdp_ok = self.has("gdp.nominal_q")
        for sid in ("lgb.special_new", "lgb.new_total", "lgb.net"):
            if self.has(sid):
                self.put(f"{sid}~12m", C.rolling12(self.get(sid)), "rolling_sum_12", [sid])
                if gdp_ok:
                    self.put(f"{sid}~impulse", C.credit_impulse(current_def(sid), self.get("gdp.nominal_q")),
                             "fiscal_impulse[d12(sum12)/gdp4q]", [sid, "gdp.nominal_q"])
        if self.has("tsf.govt_bonds", "lgb.net"):
            self.put("cgb.net_implied", self.get("tsf.govt_bonds") - self.get("lgb.net"),
                     "difference[tsf_govt_bonds - lgb_net]", ["tsf.govt_bonds", "lgb.net"])

    # ------------------------------------------------------------------ projects
    def projects(self):
        for sid in ("ndrc.approvals_value", "ndrc.approvals_count"):
            if self.has(sid):
                s = self.get(sid)
                full = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="ME"))
                self.put(f"{sid}~3mma", full.rolling(3, min_periods=2).mean(), "trailing_mean_3(min2)", [sid])
                self.put(f"{sid}~12m", full.rolling(12, min_periods=10).sum() * 12 / full.rolling(12, min_periods=10).count(),
                         "rolling_sum_12(gap-scaled,min10)", [sid])

    # ------------------------------------------------------------------ housing
    def housing(self):
        for seg in ("new", "used"):
            for h in ("mom", "yoy"):
                ids = [s for s in self.obs["series_id"].unique() if s.startswith(f"city70.{seg}.{h}.")]
                if not ids:
                    continue
                df = pd.DataFrame({i.split(".", 3)[3]: self.get(i) for i in ids})
                agg = H.tier_aggregate(df)
                for tier in agg.columns:
                    self.put(f"hp.{seg}.{tier}.{h}", agg[tier], "tier_mean[index-100]", ids)
                if h == "mom":
                    lvl = H.chain_level(agg)
                    for tier in lvl.columns:
                        self.put(f"hp.{seg}.{tier}.level", lvl[tier], "chain_index[2022avg=100]", [f"hp.{seg}.{tier}.mom"])
                    self.put(f"hp.{seg}.breadth_down", H.breadth(df), "count[index<100]", ids)

    def run(self) -> pd.DataFrame:
        # dependency order: credit() uses lgb.new_total from bonds()
        for step in (self.gdp, self.activity, self.bonds, self.credit, self.projects, self.housing):
            try:
                step()
            except Exception as e:
                NOTES[f"step:{step.__name__}"] = f"{type(e).__name__}: {e}"
        return pd.concat(self.rows, ignore_index=True) if self.rows else pd.DataFrame(columns=store.COLUMNS)
