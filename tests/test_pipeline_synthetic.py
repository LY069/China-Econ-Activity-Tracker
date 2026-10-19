"""Integration test of derive -> validation -> analytics on synthetic data with planted properties."""
import pandas as pd
import pytest

from cetracker import store, derive
from cetracker.validate import checks
from cetracker.analytics import lags
from tests.synthetic import make


@pytest.fixture(scope="module")
def pipeline():
    obs = make()
    der = derive.Deriver(obs).run()
    allrows = pd.concat([obs, der], ignore_index=True)
    store.validate(allrows)
    lat = store.latest(allrows)
    cache = {}

    def get(sid):
        if sid not in cache:
            cache[sid] = store.series(lat, sid)
        return cache[sid]
    return get, checks.run(get)


def _check(vals, cid):
    return next(v for v in vals if v["id"] == cid)


@pytest.mark.parametrize("cid", ["id:hh_loan_stock", "id:corp_loan_stock", "id:loans_sector", "id:lgb_total", "id:lgb_refi",
                                 "id:retail_month_vs_ytd"])
def test_identities_pass(pipeline, cid):
    _, vals = pipeline
    assert _check(vals, cid)["status"] == "PASS", _check(vals, cid)


def test_published_yoy_reproduced(pipeline):
    _, vals = pipeline
    v = _check(vals, "yoy_repro:retail.yoy")
    assert v["status"] == "PASS" and v["n"] > 100, v


def test_naive_gap_logged(pipeline):
    """Base revisions planted -> level-based y/y must differ from published y/y (gap is logged)."""
    _, vals = pipeline
    v = _check(vals, "yoy_gap_log:fai.ytd")
    assert v["status"] == "WARN" and v["n_exceptions"] > 0


def test_tier_reproduction(pipeline):
    _, vals = pipeline
    for t in ("tier1", "tier2", "tier3"):
        assert _check(vals, f"tier_repro:hp.new.{t}.mom")["status"] == "PASS"


def test_missing_inputs_not_testable(pipeline):
    _, vals = pipeline
    assert _check(vals, "recon:lgb_mof_vs_celma")["status"] == "NOT_TESTABLE"


def test_planted_lead_recovered(pipeline):
    get, _ = pipeline
    raw = lags.lead_lag(get("ndrc.approvals_value"), get("fai.ytd~cyoy"), "ndrc", "fai", n_boot=199)
    assert raw.best_lead == 3 and "robust" in raw.verdict, raw.criteria
    # Smoothing before inference distorts timing (prewhitening an MA-smoothed series is
    # imperfect): this is why PAIRS uses unsmoothed series and 3mma is display-only.


def test_derived_rows_have_lineage(pipeline):
    get, _ = pipeline
    assert not get("fai.ytd~cyoy").empty and not get("tsf.flow_ex_govt~impulse").empty


def test_gdp_quarters_from_ytd(pipeline):
    get, _ = pipeline
    q, ytd = get("gdp.nominal_q"), get("gdp.nominal_ytd")
    assert abs(q.groupby(q.index.year).sum().iloc[1] - ytd[ytd.index.month == 12].iloc[1]) < 1e-6
    assert not get("gdp.contrib_secondary").empty


def test_loan_flows_from_stocks(pipeline):
    get, _ = pipeline
    f, st = get("loans.hh_total"), get("loans_stock.hh_total")
    assert abs(f.iloc[-1] - (st.iloc[-1] - st.iloc[-2])) < 1e-9
