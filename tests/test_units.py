"""Unit tests. Release-text fixtures reproduce the *phrasing* of official releases; their
numbers are illustrative and are never used as data."""
import numpy as np
import pandas as pd
import pytest

from cetracker import store
from cetracker.fetch import releases as R
from cetracker.transform import ytd as Y, seasonal as SA, credit as C, fiscal as F, housing as H
from cetracker.analytics import classify as K, nowcast as N


# ----------------------------------------------------------------- release parsers
def test_pboc_loans_ytd_block():
    t = ("2025年8月金融统计数据报告 8月末，广义货币(M2)余额331.98万亿元，同比增长8.8%。狭义货币(M1)余额111.23万亿元，同比增长6%。"
         "前八个月人民币贷款增加13.46万亿元。分部门看，住户贷款增加3112亿元，其中，短期贷款减少4119亿元，中长期贷款增加7231亿元；"
         "企（事）业单位贷款增加12.25万亿元，其中，短期贷款增加3.8万亿元，中长期贷款增加6.9万亿元，票据融资增加1.31万亿元；"
         "非银行业金融机构贷款增加1427亿元。")
    p = R.pboc_financial_statistics(t)
    b = p["blocks"][0]
    assert b["scope"] == "ytd" and b["n"] == 8
    assert b["hh_short"] == -4119 and b["corp_bills"] == 13100 and b["nbfi_total"] == 1427
    assert p["m1_yoy"] == 6.0 and p["m2_yoy"] == 8.8


def test_mof_month_and_ytd_blocks():
    t = ("2025年8月，全国发行新增债券5000亿元，其中一般债券600亿元、专项债券4400亿元。全国发行再融资债券3000亿元，"
         "其中一般债券1000亿元、专项债券2000亿元。2025年1-8月，全国发行新增债券35000亿元，其中一般债券5000亿元、专项债券30000亿元。")
    p = R.mof_local_bonds(t)
    m = [b for b in p["blocks"] if b["scope"] == "month"][0]
    y = [b for b in p["blocks"] if b["scope"] == "ytd"][0]
    assert m["new_special"] == 4400 and m["refi_total"] == 3000 and y["new_total"] == 35000 and y["n"] == 8


def test_tsf_flow_stock_components():
    t = ("2025年8月末社会融资规模存量为433.66万亿元，同比增长8.8%。2025年前八个月社会融资规模增量累计为26.56万亿元。"
         "2025年8月社会融资规模增量为2.57万亿元。8月，对实体经济发放的人民币贷款减少465亿元；政府债券净融资1.37万亿元")
    p = R.pboc_tsf(t)
    scopes = {b["scope"]: b for b in p["blocks"]}
    assert scopes["stock"]["tsf_stock_yoy"] == 8.8
    month = [b for b in p["blocks"] if b["scope"] == "month"]
    merged = {k: v for b in month for k, v in b.items()}
    assert merged["tsf_flow"] == 25700 and merged["rmb_loans"] == -465 and round(merged["govt_bonds"]) == 13700


def test_ndrc_and_tiers_and_infra():
    assert R.ndrc_approvals("8月份，共审批核准固定资产投资项目12个，总投资1180亿元")[0]["count"] == 12
    h = R.nbs_housing_tiers("一线城市新建商品住宅销售价格环比下降0.1%，同比下降1.0%。二线城市新建商品住宅销售价格环比持平，同比下降2.3%。")
    assert h["tier1_new_mom"] == -0.1 and h["tier2_new_mom"] == 0.0 and h["tier2_new_yoy"] == -2.3
    f = R.nbs_infra_fai("基础设施投资（不含电力）同比增长2.0%，制造业投资增长5.1%，房地产开发投资同比下降12.9%")
    assert f == {"infra_ytd_yoy": 2.0, "mfg_ytd_yoy": 5.1, "re_ytd_yoy": -12.9}


# ----------------------------------------------------------------- YTD
def _idx(*m):
    return pd.to_datetime(list(m)) + pd.offsets.MonthEnd(0)


def test_single_month_janfeb_and_gap():
    ytd = pd.Series([20.0, 30.0, 45.0], _idx("2025-02", "2025-03", "2025-05"))
    sm, fl = Y.single_month_from_ytd(ytd)
    assert sm.iloc[0] == 20 and fl.iloc[0] == "janfeb_combined"
    assert sm.iloc[1] == 10 and np.isnan(sm.iloc[2])        # May follows a missing April: not absorbed


def test_comparable_yoy_formula():
    ytd = pd.Series([200.0, 330.0], _idx("2025-02", "2025-03"))
    g = pd.Series([10.0, 10.0], _idx("2025-02", "2025-03"))
    cy = Y.comparable_single_month_yoy(ytd, g)
    # bases: 200/1.1, 330/1.1 -> March base 118.18; March level 130 -> +10%
    assert cy.iloc[0] == 10.0 and abs(cy.iloc[1] - 10.0) < 1e-9


def test_fill_janfeb():
    s = pd.Series([5.0, 6.0], _idx("2025-02", "2025-03"))
    f = Y.fill_janfeb(s)
    assert f.index[0].month == 1 and f.iloc[0] == 5.0 and len(f) == 3


# ----------------------------------------------------------------- seasonal
def test_cny_dates_in_window():
    for y, d in SA.cny_dates().items():
        assert pd.Timestamp(y, 1, 21) <= d <= pd.Timestamp(y, 2, 20)


def test_cny_regressors_centred():
    idx = pd.date_range("2005-01-31", "2030-12-31", freq="ME")   # centring span
    r = SA.cny_regressors(idx)
    assert (r.groupby(r.index.month).mean().abs() < 1e-9).all().all()
    rj = SA.cny_regressors(idx, janfeb_combined=True)
    assert rj["cny_pre"].std() < 1e-9 and rj["cny_post"].std() > 0


def test_x13_recovers_sa():
    idx = pd.date_range("2012-01-31", "2026-06-30", freq="ME")
    rng = np.random.default_rng(1)
    t = np.arange(len(idx))
    seas = np.tile([-.05, -.15, .02, .01, .02, .04, 0, .01, .03, 0, .02, .05], len(idx) // 12 + 1)[:len(idx)]
    reg = SA.cny_regressors(idx)
    logy = 4 + .006 * t + seas + .3 * reg["cny_on"].values + rng.normal(0, .01, len(idx))
    prim, cross, note = SA.seasonal_adjust(pd.Series(np.exp(logy), idx))
    err = np.log(prim["sa"].values) - (4 + .006 * t)
    assert np.std(err) < 0.02, note


# ----------------------------------------------------------------- credit / fiscal / housing / classify
def test_credit_impulse_realtime_gdp():
    m = pd.date_range("2020-01-31", "2023-12-31", freq="ME")
    flow = pd.Series(100.0, m)
    flow[m >= "2022-01-31"] = 200.0
    gdp = pd.Series(1000.0, pd.date_range("2019-03-31", "2023-12-31", freq="QE"))
    imp = C.credit_impulse(flow, gdp)
    # by Dec-2022 the 12m flow rose by 1200 on 4000 annual GDP -> 30%
    assert abs(imp["2022-12-31"] - 30.0) < 1e-9
    g = C.gdp_4q_monthly(gdp, pd.DatetimeIndex(["2021-03-31", "2021-04-30"]))
    assert g.notna().all()


def test_bond_classification():
    assert F.classify_local_bond("2024年XX省政府再融资专项债券（五期）", use_of_proceeds="置换存量隐性债务") == "lg_refi_debt_swap"
    assert F.classify_local_bond("2025年XX省政府再融资一般债券（一期）") == "lg_refi_regular"
    assert F.classify_local_bond("2025年XX省政府专项债券（十期）") == "lg_special_new"
    assert F.classify_central_bond("2025年超长期特别国债（三期）") == "cgb_ultra_long_special"
    assert F.classify_central_bond("2025年特别国债（一期）") == "cgb_bank_recap_special"


def test_tiers_and_aggregate():
    t = H.tiers()
    assert [len(t[k]) for k in ("tier1", "tier2", "tier3")] == [4, 31, 35]
    df = pd.DataFrame({c: [100.5] for c in t["tier1"]}, index=_idx("2025-01"))
    agg = H.tier_aggregate(df)
    assert abs(agg["tier1"].iloc[0] - 0.5) < 1e-12 and "tier2" not in agg and agg["all70"].isna().all()


def test_six_networks_classifier():
    assert K.classify("XX抽水蓄能电站项目")[0] == "power_energy"
    assert K.classify("XX市城市地下综合管廊工程")[0] == "urban_pipes"
    assert K.classify("XX高速公路扩容")[0] == "transport"
    assert K.classify("某某综合项目")[0] == "unclassified"
    m = K.audit_metrics(pd.Series(["water", "transport", "water"]), pd.Series(["water", "water", "water"]))
    assert m.loc[m.category == "water", "precision"].iloc[0] == pytest.approx(2 / 3)


def test_dm_test_sign():
    rng = np.random.default_rng(0)
    e_b = rng.normal(0, 2, 80)
    e_m = rng.normal(0, 1, 80)
    stat, p = N.dm_test(e_m, e_b)
    assert stat < 0 and p < 0.05


# ----------------------------------------------------------------- store
def test_store_rejects_untraceable_rows():
    s = pd.Series([1.0], _idx("2025-01"))
    bad = store.make_rows("x", s, store.Provenance("NBS", "primary"))
    with pytest.raises(ValueError):
        store.validate(bad)
    bad2 = store.make_rows("x", s, store.Provenance("tracker", "derived"))
    with pytest.raises(ValueError):
        store.validate(bad2)


def test_title_period():
    from cetracker.ingest import title_period
    assert title_period("1—7月份国民经济保持总体平稳", "2026-08-17") == pd.Timestamp("2026-07-31")
    assert title_period("2025年国民经济回升向好", "2026-01-17") == pd.Timestamp("2025-12-31")
    assert title_period("前三季度国民经济运行稳中有进", "2026-10-20") == pd.Timestamp("2026-09-30")
    assert title_period("2026年1—9月份社会消费品零售总额增长1.1%", "2026-10-19") == pd.Timestamp("2026-09-30")


def test_tier_interpretation_phrasing():
    t = ("一线城市新建商品住宅销售价格环比上涨0.1%，涨幅与上月相同。二线城市新建商品住宅销售价格环比由上月下降0.1%转为持平。"
         "二、三线城市新建商品住宅销售价格同比分别下降2.4%和3.9%，降幅分别收窄。")
    p = R.nbs_tiers_interpretation(t)
    assert p["tier1_new_mom"] == 0.1 and p["tier2_new_mom"] == 0.0
    assert p["tier2_new_yoy"] == -2.4 and p["tier3_new_yoy"] == -3.9
    # May 2023 release wording: the combined statement with a 由…转为 transition
    t = ("二、三线城市二手住宅销售价格环比由上月持平分别转为下降0.3%和0.2%。"
         "二、三线城市新建商品住宅销售价格环比由上月上涨0.1%和0.2%转为持平。")
    p = R.nbs_tiers_interpretation(t)
    assert p["tier2_used_mom"] == -0.3 and p["tier3_used_mom"] == -0.2
    assert p["tier2_new_mom"] == 0.0 and p["tier3_new_mom"] == 0.0
    t = "7月份，一、二、三线城市二手住宅销售价格同比分别下降1.4%、2.7%和3.5%，降幅比上月分别扩大1.0、0.3和0.1个百分点。"
    p = R.nbs_tiers_interpretation(t)
    assert (p["tier1_used_yoy"], p["tier2_used_yoy"], p["tier3_used_yoy"]) == (-1.4, -2.7, -3.5)



def test_nowcast_per_model_samples():
    rng = np.random.default_rng(2)
    idx = pd.date_range("2015-01-31", periods=120, freq="ME")
    x = pd.Series(rng.normal(size=120), idx)
    y = pd.Series(0.8 * x.values + rng.normal(0, 0.5, 120), idx)
    short = x.iloc[-20:]
    r = N.evaluate(y, {"x": (x, 0), "short": (short, 0)}, {"m_x": ["x"], "m_short": ["short"]}, min_train=48)
    assert r["models"]["m_x"]["n_oos"] > 50 and r["models"]["m_x"]["beats_ar"]
    assert "insufficient" in r["models"]["m_short"]["status"]



def test_lead_lag_no_janfeb_artefact():
    """Monthly series with January missing: differencing must not create a 2-month change in Feb
    nor copy it into January (review finding 8)."""
    from cetracker.analytics import lags
    idx = pd.date_range("2018-01-31", "2025-12-31", freq="ME")
    s = pd.Series(np.arange(len(idx), dtype=float), idx)       # +1 every month, nonstationary
    s = s[s.index.month != 1]
    d, how = lags.make_stationary(s)
    assert how == "diff1" and d.dropna().eq(1.0).all() and d[d.index.month.isin([1, 2])].isna().all()


def test_period_phrases_quarters_and_annual():
    assert R.period_scope("前三季度") == ("ytd", 9) and R.period_scope("一季度") == ("ytd", 3)
    t = ("初步统计,2025年社会融资规模增量累计为35.6万亿元,比上年多3.34万亿元。"
         "12月末社会融资规模存量为442.12万亿元,同比增长8.3%。")
    b = {x["scope"]: x for x in R.pboc_tsf(t)["blocks"]}
    assert b["ytd"]["n"] == 12 and b["ytd"]["tsf_flow"] == 356000.0
    assert b["stock"]["n"] == 12 and b["stock"]["tsf_stock_yoy"] == 8.3
    b = R.pboc_tsf("2025年前三季度社会融资规模增量累计为30.09万亿元")["blocks"][0]
    assert (b["scope"], b["n"], b["tsf_flow"]) == ("ytd", 9, 300900.0)


def test_quarter_retail_and_ppi_forms():
    r = R.nbs_retail("一季度,社会消费品零售总额108659亿元,同比增长3.3%(以下除特殊说明外均为名义增长)。"
                     "3月份,社会消费品零售总额34233亿元,同比下降3.5%。")
    assert r["retail.ytd"] == 10865.9 and r["retail.ytd_yoy"] == 3.3 and r["retail.yoy"] == -3.5
    assert R.nbs_ppi("2026年3月份,全国工业生产者出厂价格同比由上月下降0.9%转为上涨0.5%;环比上涨1.0%")["prices.ppi_yoy"] == 0.5
    assert R.nbs_ppi("2025年7月份,全国工业生产者出厂价格环比下降0.2%,工业生产者购进价格环比下降0.3%。"
                     "工业生产者出厂价格同比下降3.6%,工业生产者购进价格同比下降4.5%")["prices.ppi_yoy"] == -3.6
