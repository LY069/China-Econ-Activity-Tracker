"""Series registry: what each series_id means, its unit, frequency and where it comes from.

`nbs` specs: (dbcode, node-path regex, child-cname regex). Node paths are matched against
the portal's own indicator tree, so a spec that matches nothing fails loudly in the
source-status report instead of silently picking another indicator. Patterns are
verified on first live fetch; the resolved code + cname is written into lineage.
"""
from __future__ import annotations

S = {}


def reg(sid, name, unit, freq, group, kind, **kw):
    S[sid] = {"id": sid, "name": name, "unit": unit, "freq": freq, "group": group, "kind": kind, **kw}


# ---------------- Official: national accounts ----------------
reg("gdp.nominal_q", "GDP, nominal, quarter", "RMB bn", "Q", "GDP", "level",
    nbs=("hgjd", r"国民经济核算/国内生产总值$", r"^国内生产总值_当季值"), scale=0.1)
reg("gdp.real_yoy", "GDP real growth, y/y", "%", "Q", "GDP", "yoy",
    nbs=("hgjd", r"国内生产总值指数", r"^国内生产总值指数\(上年同期=100\)_当季值"), offset=-100)
reg("gdp.primary_real_yoy", "Primary industry VA real growth, y/y", "%", "Q", "GDP", "yoy",
    nbs=("hgjd", r"国内生产总值指数", r"^第一产业增加值指数\(上年同期=100\)_当季值"), offset=-100)
reg("gdp.secondary_real_yoy", "Secondary industry VA real growth, y/y", "%", "Q", "GDP", "yoy",
    nbs=("hgjd", r"国内生产总值指数", r"^第二产业增加值指数\(上年同期=100\)_当季值"), offset=-100)
reg("gdp.tertiary_real_yoy", "Tertiary industry VA real growth, y/y", "%", "Q", "GDP", "yoy",
    nbs=("hgjd", r"国内生产总值指数", r"^第三产业增加值指数\(上年同期=100\)_当季值"), offset=-100)
reg("gdp.primary_nominal_q", "Primary industry VA, nominal", "RMB bn", "Q", "GDP", "level",
    nbs=("hgjd", r"国民经济核算/国内生产总值$", r"^第一产业增加值_当季值"), scale=0.1)
reg("gdp.secondary_nominal_q", "Secondary industry VA, nominal", "RMB bn", "Q", "GDP", "level",
    nbs=("hgjd", r"国民经济核算/国内生产总值$", r"^第二产业增加值_当季值"), scale=0.1)
reg("gdp.tertiary_nominal_q", "Tertiary industry VA, nominal", "RMB bn", "Q", "GDP", "level",
    nbs=("hgjd", r"国民经济核算/国内生产总值$", r"^第三产业增加值_当季值"), scale=0.1)
reg("gdp.sa_qoq", "GDP real growth, q/q SA (NBS)", "%", "Q", "GDP", "mom",
    nbs=("hgjd", r"环比", r"国内生产总值环比增长速度"))
reg("gdp.contrib_consumption", "Final consumption contribution to GDP growth", "pp", "A", "GDP", "contrib",
    nbs=("hgnd", r"三大需求", r"最终消费支出拉动"))
reg("gdp.contrib_investment", "Gross capital formation contribution to GDP growth", "pp", "A", "GDP", "contrib",
    nbs=("hgnd", r"三大需求", r"资本形成总额拉动"))
reg("gdp.contrib_netexports", "Net exports contribution to GDP growth", "pp", "A", "GDP", "contrib",
    nbs=("hgnd", r"三大需求", r"货物和服务净出口拉动"))

reg("gdp.nominal_ytd", "GDP, nominal, year-to-date", "RMB bn", "Q", "GDP", "ytd_level")
reg("gdp.real_ytd_yoy", "GDP real growth, year-to-date y/y", "%", "Q", "GDP", "ytd_yoy")
for _s, _n in (("primary", "Primary"), ("secondary", "Secondary"), ("tertiary", "Tertiary")):
    reg(f"gdp.{_s}_nominal_ytd", f"{_n} industry VA, nominal, YTD", "RMB bn", "Q", "GDP", "ytd_level")
    reg(f"gdp.{_s}_real_ytd_yoy", f"{_n} industry VA real growth, YTD y/y", "%", "Q", "GDP", "ytd_yoy")
    reg(f"gdp.contrib_{_s}", f"{_n} industry contribution to YTD real GDP growth (tracker est.)", "pp", "Q", "GDP", "contrib")

# ---------------- Official: activity ----------------
reg("ip.yoy", "Industrial value-added, y/y", "%", "M", "Industry", "yoy",
    nbs=("hgyd", r"工业/工业增加值增长速度", r"^工业增加值_同比增长"))
reg("ip.ytd_yoy", "Industrial value-added, YTD y/y", "%", "M", "Industry", "ytd_yoy",
    nbs=("hgyd", r"工业/工业增加值增长速度", r"^工业增加值_累计增长"))
reg("ip.mom_sa_nbs", "Industrial value-added, m/m SA (NBS)", "%", "M", "Industry", "mom")
reg("retail.mom_sa_nbs", "Retail sales, m/m SA (NBS)", "%", "M", "Consumption", "mom")
reg("retail.level", "Retail sales, month", "RMB bn", "M", "Consumption", "level",
    nbs=("hgyd", r"国内贸易/社会消费品零售总额", r"^社会消费品零售总额_当期值"), scale=0.1)
reg("retail.ytd", "Retail sales, YTD", "RMB bn", "M", "Consumption", "ytd_level",
    nbs=("hgyd", r"国内贸易/社会消费品零售总额", r"^社会消费品零售总额_累计值"), scale=0.1)
reg("retail.yoy", "Retail sales, y/y", "%", "M", "Consumption", "yoy",
    nbs=("hgyd", r"国内贸易/社会消费品零售总额", r"^社会消费品零售总额_同比增长"))
reg("retail.ytd_yoy", "Retail sales, YTD y/y", "%", "M", "Consumption", "ytd_yoy",
    nbs=("hgyd", r"国内贸易/社会消费品零售总额", r"^社会消费品零售总额_累计增长"))
reg("fai.ytd", "Fixed-asset investment (ex rural households), YTD", "RMB bn", "M", "Investment", "ytd_level",
    nbs=("hgyd", r"固定资产投资.*/固定资产投资额", r"^固定资产投资额_累计值"), scale=0.1)
reg("fai.ytd_yoy", "Fixed-asset investment, YTD y/y", "%", "M", "Investment", "ytd_yoy",
    nbs=("hgyd", r"固定资产投资.*/固定资产投资额", r"^固定资产投资额_累计增长"))
reg("fai.mfg_ytd_yoy", "Manufacturing FAI, YTD y/y", "%", "M", "Investment", "ytd_yoy",
    nbs=("hgyd", r"固定资产投资.*(行业|分行业)", r"^制造业固定资产投资额_累计增长"))
reg("fai.infra_ytd_yoy", "Infrastructure FAI (ex power, NBS def.), YTD y/y", "%", "M", "Investment", "ytd_yoy",
    text="nbs_infra_fai")
reg("fai.re_ytd", "Real-estate development investment, YTD", "RMB bn", "M", "Property", "ytd_level",
    nbs=("hgyd", r"房地产/房地产开发投资", r"^房地产投资_累计值|^房地产开发投资额_累计值"), scale=0.1)
reg("fai.re_ytd_yoy", "Real-estate development investment, YTD y/y", "%", "M", "Property", "ytd_yoy",
    nbs=("hgyd", r"房地产/房地产开发投资", r"^房地产投资_累计增长|^房地产开发投资额_累计增长"))
reg("re.climate", "National Real Estate Climate Index (国房景气指数)", "index", "M", "Property", "index",
    nbs=("hgyd", r"房地产", r"国房景气指数"))
reg("re.starts_ytd", "Housing floor space started, YTD", "mn sqm", "M", "Property", "ytd_level",
    nbs=("hgyd", r"房地产/.*(施工|新开工)", r"^房屋新开工面积_累计值"), scale=1.0)
reg("re.starts_ytd_yoy", "Floor space started, YTD y/y", "%", "M", "Property", "ytd_yoy",
    nbs=("hgyd", r"房地产/.*(施工|新开工)", r"^房屋新开工面积_累计增长"))
reg("re.completions_ytd", "Floor space completed, YTD", "mn sqm", "M", "Property", "ytd_level",
    nbs=("hgyd", r"房地产/.*(竣工)", r"^房屋竣工面积_累计值"), scale=1.0)
reg("re.completions_ytd_yoy", "Floor space completed, YTD y/y", "%", "M", "Property", "ytd_yoy",
    nbs=("hgyd", r"房地产/.*(竣工)", r"^房屋竣工面积_累计增长"))
reg("re.sales_ytd", "Commodity building floor space sold, YTD", "mn sqm", "M", "Property", "ytd_level",
    nbs=("hgyd", r"房地产/.*销售", r"^商品房销售面积_累计值"), scale=1.0)
reg("re.sales_ytd_yoy", "Floor space sold, YTD y/y", "%", "M", "Property", "ytd_yoy",
    nbs=("hgyd", r"房地产/.*销售", r"^商品房销售面积_累计增长"))

# ---------------- Prices (deflators) ----------------
reg("prices.cpi_yoy", "CPI, y/y", "%", "M", "Prices", "yoy",
    nbs=("hgyd", r"价格指数/居民消费价格指数.*上年同月", r"^居民消费价格指数\(上年同月=100\)$"), offset=-100)
reg("prices.cpi_goods_yoy", "CPI consumer goods, y/y", "%", "M", "Prices", "yoy",
    nbs=("hgyd", r"价格指数/居民消费价格指数.*上年同月", r"^消费品价格指数\(上年同月=100\)$"), offset=-100)
reg("prices.ppi_yoy", "PPI, y/y", "%", "M", "Prices", "yoy",
    nbs=("hgyd", r"价格指数/工业生产者出厂价格指数.*上年同月", r"^工业生产者出厂价格指数\(上年同月=100\)$"), offset=-100)

# ---------------- Physical proxies (validation only) ----------------
reg("proxy.cement_yoy", "Cement output, y/y", "%", "M", "Proxies", "yoy",
    nbs=("hgyd", r"工业/.*产品产量.*", r"^水泥产量_同比增长"))
reg("proxy.steel_yoy", "Steel products output, y/y", "%", "M", "Proxies", "yoy",
    nbs=("hgyd", r"工业/.*产品产量.*", r"^粗钢产量_同比增长"))
reg("proxy.power_yoy", "Power generation, y/y", "%", "M", "Proxies", "yoy",
    nbs=("hgyd", r"(工业|能源)/.*(产品产量|发电量).*", r"^发电量_同比增长"))
reg("proxy.excavator_yoy", "Excavator sales, y/y (CCMA)", "%", "M", "Proxies", "yoy", text="ccma")

# ---------------- Money and credit ----------------
reg("money.m2", "M2, end-period", "RMB bn", "M", "Credit", "stock",
    nbs=("hgyd", r"金融/货币供应量", r"^货币和准货币\(M2\)供应量_期末值"), scale=0.1)
reg("money.m2_yoy", "M2, y/y", "%", "M", "Credit", "yoy",
    nbs=("hgyd", r"金融/货币供应量", r"^货币和准货币\(M2\)供应量_同比增长"))
reg("money.m1", "M1, end-period", "RMB bn", "M", "Credit", "stock",
    nbs=("hgyd", r"金融/货币供应量", r"^货币\(M1\)供应量_期末值"), scale=0.1)
reg("money.m1_yoy", "M1, y/y", "%", "M", "Credit", "yoy",
    nbs=("hgyd", r"金融/货币供应量", r"^货币\(M1\)供应量_同比增长"))
for k, n in [("tsf.flow", "TSF flow, total"), ("tsf.rmb_loans", "TSF: RMB loans to real economy"),
             ("tsf.fx_loans", "TSF: FX loans"), ("tsf.entrusted", "TSF: entrusted loans"),
             ("tsf.trust", "TSF: trust loans"), ("tsf.undiscounted_ba", "TSF: undiscounted bankers' acceptances"),
             ("tsf.corp_bonds", "TSF: corporate bonds"), ("tsf.govt_bonds", "TSF: government bonds"),
             ("tsf.equity", "TSF: equity financing"), ("tsf.abs", "TSF: depository-institution ABS"),
             ("tsf.writeoffs", "TSF: loan write-offs")]:
    reg(k, n, "RMB bn", "M", "Credit", "flow", pboc="tsf_flow")
reg("tsf.stock", "TSF stock", "RMB bn", "M", "Credit", "stock", pboc="tsf_stock")
reg("tsf.stock_yoy", "TSF stock, y/y", "%", "M", "Credit", "yoy", pboc="tsf_stock")
for k, n in [("loans.total", "New RMB loans, total"), ("loans.hh_total", "Household loans"),
             ("loans.hh_short", "Household short-term loans"), ("loans.hh_medlong", "Household medium/long-term loans"),
             ("loans.corp_total", "Corporate loans"), ("loans.corp_short", "Corporate short-term loans"),
             ("loans.corp_medlong", "Corporate medium/long-term loans"), ("loans.corp_bills", "Corporate bill financing"),
             ("loans.nbfi", "Loans to non-bank financial institutions")]:
    reg(k, n, "RMB bn", "M", "Credit", "flow", pboc="loans")

reg("money.m0", "M0, end-period", "RMB bn", "M", "Credit", "stock")
reg("money.m1_restated", "M1 2024 restated on the Jan-2025 definition (PBoC)", "RMB bn", "M", "Credit", "stock")
reg("tsf.lg_special_bonds", "TSF: local-government special bonds (2018 definition)", "RMB bn", "M", "Credit", "flow")
reg("tsf.govt_bonds_stock", "TSF stock: government bonds", "RMB bn", "M", "Credit", "stock")
for k in ("total", "hh_total", "hh_short", "hh_medlong", "corp_total", "corp_short", "corp_medlong", "corp_bills",
          "corp_leases", "corp_advances", "nbfi"):
    reg(f"loans_stock.{k}", f"RMB loan stock: {k} (PBoC credit table)", "RMB bn", "M", "Credit", "stock", pboc="table")
    reg(f"loans_text.{k}", f"New RMB loans as reported in PBoC release text: {k}", "RMB bn", "M", "Credit", "flow")
reg("loans.corp_leases", "Corporate financial leases (flow)", "RMB bn", "M", "Credit", "flow")
reg("loans.corp_advances", "Corporate advances (flow)", "RMB bn", "M", "Credit", "flow")
for k in ("flow", "rmb_loans", "govt_bonds", "stock", "stock_yoy"):
    reg(f"tsf_text.{k}", f"TSF {k} as reported in PBoC release text", "RMB bn" if k != "stock_yoy" else "%", "M", "Credit", "flow")

# ---------------- High-frequency: government bonds ----------------
for k, n in [("cgb.regular", "Central govt bonds: regular, gross issuance"),
             ("cgb.ultra_long_special", "Ultra-long special CGB, gross issuance"),
             ("cgb.bank_recap_special", "Special CGB for bank recapitalisation, gross issuance"),
             ("lgb.general_new", "Local govt bonds: new general"),
             ("lgb.special_new", "Local govt bonds: new special"),
             ("lgb.refi_total", "Local govt bonds: refinancing, total"),
             ("lgb.refi_general", "Local govt bonds: refinancing, general"),
             ("lgb.refi_special", "Local govt bonds: refinancing, special"),
             ("lgb.refi_debt_swap", "Local govt bonds: debt-swap refinancing"),
             ("lgb.refi_regular", "Local govt bonds: regular refinancing"),
             ("lgb.total", "Local govt bonds: total issuance"),
             ("lgb.redemption", "Local govt bonds: principal redemptions")]:
    reg(k, n, "RMB bn", "M", "Bonds", "flow", bonds=True)

# ---------------- High-frequency: projects ----------------
reg("ndrc.approvals_count", "NDRC approved FAI projects, count", "projects", "M", "Projects", "flow", text="ndrc")
reg("ndrc.approvals_value", "NDRC approved FAI projects, total investment", "RMB bn", "M", "Projects", "flow", text="ndrc")

# ---------------- Housing prices (70 cities) ----------------
for seg, segn in [("new", "new commercial residential"), ("used", "second-hand residential")]:
    for tier in ("tier1", "tier2", "tier3", "all70"):
        reg(f"hp.{seg}.{tier}.mom", f"Home prices {segn}, {tier}, m/m", "%", "M", "Property", "mom", city70=seg)
        reg(f"hp.{seg}.{tier}.yoy", f"Home prices {segn}, {tier}, y/y", "%", "M", "Property", "yoy", city70=seg)
        reg(f"hp.{seg}.{tier}.level", f"Home prices {segn}, {tier}, chained index 2015=100", "index", "M",
            "Property", "index", city70=seg)
    reg(f"hp.{seg}.breadth_down", f"Cities with m/m decline ({segn})", "cities of 70", "M", "Property", "count", city70=seg)


IMPULSE_NOTE = "Δ12m of 12-month flow, % of TSF stock 12 months earlier (GDP-free)"


def meta(sid: str) -> dict:
    if sid.endswith("~impulse_stk"):
        base = S.get(sid.split("~")[0], {"name": sid})
        return {"id": sid, "name": f"{base['name']}: impulse (% of TSF stock)", "unit": "%", "freq": "M",
                "group": base.get("group", "Credit"), "kind": "impulse", "note": IMPULSE_NOTE}
    if sid in S:
        return S[sid]
    base = sid.split("~")[0]
    return {**S.get(base, {"id": sid, "name": sid, "unit": "", "freq": "M", "group": "Derived", "kind": "derived"}), "id": sid}
