"""Parsers for official release *texts* (press releases), the second raw route.

Several figures exist only in release text, not in table form: PBoC loan splits by sector
and tenor in the monthly 金融统计数据报告, MOF's monthly local-bond issuance by type,
NDRC approval counts, NBS tier-level housing commentary and infrastructure FAI growth.
Each parser returns a dict of {field: value}; unparsed fields are absent (never guessed).
Numbers are RMB 100m (亿元) in the texts; callers convert to RMB bn (/10).
"""
from __future__ import annotations

import re

NUM = r"(-?\d+(?:\.\d+)?)"
CN_NUM = r"(-?\d+(?:\.\d+)?)\s*(万亿|亿)元"


def _to_yi(v: str, unit: str) -> float:
    x = float(v)
    return x * 10000 if unit == "万亿" else x


def _signed(verb: str, v: float) -> float:
    return -v if verb in ("减少", "下降") else v


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("，", ",").replace("；", ";").replace("：", ":").replace("（", "(").replace("）", ")")


PERIOD_PHRASE = r"(前(?:\d{1,2}|[一二两三四五六七八九十]{1,2})个?月|\d{1,2}月份|一季度|上半年|前三季度|全年)"
CN_MONTHS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
             "十一": 11, "十二": 12}


def period_scope(phrase: str) -> tuple[str, int | None]:
    """('ytd'|'month', month count or month number) from a period phrase."""
    if phrase.endswith("月份"):
        return "month", int(phrase[:-2])
    if phrase in ("一季度", "上半年", "前三季度", "全年"):
        return "ytd", {"一季度": 3, "上半年": 6, "前三季度": 9, "全年": 12}[phrase]
    if re.fullmatch(r"\d{4}年", phrase):           # annual report: '2025年社会融资规模增量累计为…'
        return "ytd", 12
    if phrase.startswith("前"):
        n = phrase[1:].rstrip("月").rstrip("个")
        return "ytd", int(n) if n.isdigit() else CN_MONTHS.get(n)
    return "ytd", {"一季度": 3, "上半年": 6, "前三季度": 9, "全年": 12}.get(phrase)


PERIOD_ANY = re.compile(r"(\d{1,2}[-—–至]\d{1,2}月(?:份)?|前(?:\d{1,2}|[一二两三四五六七八九十]{1,2})个?月|"
                        r"(?<![-—–至\d])\d{1,2}月(?:份|末)?|一季度|上半年|前三季度|全年|今年以来|"
                        r"\d{4}年(?=社会融资规模增量|人民币贷款增加|人民币存款增加))")


def chunks_by_period(t: str) -> list[tuple[str, int | None, str]]:
    """Split cleaned text into (scope, n, chunk) at each period phrase.

    scope: 'month' (a single month), 'ytd' (Jan..n), 'stock' (month-end), or 'other'."""
    marks = [(m.start(), m.group(1)) for m in PERIOD_ANY.finditer(t)]
    out = []
    for i, (pos, ph) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(t)
        chunk = t[pos:end]
        if re.fullmatch(r"\d{1,2}[-—–至](\d{1,2})月(?:份)?", ph):
            scope, n = "ytd", int(re.findall(r"\d+", ph)[-1])
        elif ph.endswith("月末"):
            scope, n = "stock", int(ph[:-2])
        elif re.fullmatch(r"\d{1,2}月(?:份)?", ph):
            scope, n = "month", int(re.findall(r"\d+", ph)[0])
        elif ph == "今年以来":
            scope, n = "other", None
        else:
            scope, n = period_scope(ph)
        out.append((scope, n, chunk))
    return out


def pboc_financial_statistics(text: str) -> dict:
    """PBoC '金融统计数据报告': RMB loan flows by sector/tenor (亿元), M1/M2 y/y.

    The report gives the sector/tenor breakdown after "分部门看" for the period named in the
    preceding sentence (usually year-to-date, e.g. "前8个月"); single-month sector flows are
    then YTD differences, computed downstream. Each block is returned with its scope.
    """
    t = _clean(text)
    out = {"blocks": []}
    m = re.search(r"(\d{4})年(\d{1,2})月", t)
    if m:
        out["year"], out["month"] = int(m.group(1)), int(m.group(2))
    rx = re.compile(PERIOD_PHRASE + r",?人民币贷款(增加|减少)" + CN_NUM + r"[^。]*。(分部门看,[^。]*。)?")
    for mm in rx.finditer(t):
        scope, n = period_scope(mm.group(1))
        blk = {"scope": scope, "n": n, "loans_total": _signed(mm.group(2), _to_yi(mm.group(3), mm.group(4)))}
        seg = mm.group(5) or ""
        for sector, key in [("住户贷款", "hh"), (r"企\(事\)业单位贷款", "corp"), ("非银行业金融机构贷款", "nbfi")]:
            sm_ = re.search(sector + r"(增加|减少)" + CN_NUM + r"([^;。]*)", seg)
            if not sm_:
                continue
            blk[f"{key}_total"] = _signed(sm_.group(1), _to_yi(sm_.group(2), sm_.group(3)))
            tail = sm_.group(4)
            for sub, k in [("短期贷款", "short"), ("中长期贷款", "medlong"), ("票据融资", "bills")]:
                s2 = re.search(sub + r"(增加|减少)" + CN_NUM, tail)
                if s2:
                    blk[f"{key}_{k}"] = _signed(s2.group(1), _to_yi(s2.group(2), s2.group(3)))
        out["blocks"].append(blk)
    for code, key in [("M2", "m2_yoy"), ("M1", "m1_yoy"), ("M0", "m0_yoy")]:
        # M1 fell y/y through Apr-Dec 2024 ('同比下降5%'): the sign must be read, not assumed
        mm = re.search(r"\(" + code + r"\)余额" + CN_NUM + r",同比(增长|下降)" + NUM + "%", t)
        if mm:
            out[key] = _sgn(mm.group(3), mm.group(4))
            out[key.replace("_yoy", "_level")] = _to_yi(mm.group(1), mm.group(2))
    return out


TSF_COMPONENTS = {
    "对实体经济发放的人民币贷款": "rmb_loans",
    "对实体经济发放的外币贷款折合人民币": "fx_loans",
    "委托贷款": "entrusted",
    "信托贷款": "trust",
    "未贴现的银行承兑汇票": "undiscounted_ba",
    "企业债券净融资": "corp_bonds",
    "政府债券净融资": "govt_bonds",
    "非金融企业境内股票融资": "equity",
}


def pboc_tsf(text: str) -> dict:
    """PBoC '社会融资规模增量统计数据报告': TSF flow and components (亿元), by period scope."""
    t = _clean(text)
    out = {"blocks": []}
    m = re.search(r"(\d{4})年(\d{1,2})月", t)
    if m:
        out["year"], out["month"] = int(m.group(1)), int(m.group(2))
    for scope, n, chunk in chunks_by_period(t):
        blk = {}
        mm = re.search(r"社会融资规模增量(?:累计)?为" + CN_NUM, chunk)
        if mm:
            blk["tsf_flow"] = _to_yi(mm.group(1), mm.group(2))
        mm = re.search(r"社会融资规模存量为" + CN_NUM + r",同比增长" + NUM + "%", chunk)
        if mm:
            blk["tsf_stock"] = _to_yi(mm.group(1), mm.group(2))
            blk["tsf_stock_yoy"] = float(mm.group(3))
        for cn, k in TSF_COMPONENTS.items():
            mm = re.search(re.escape(cn) + r"(增加|减少)?" + NUM + r"(万亿|亿)元", chunk)
            if mm:
                blk[k] = _signed(mm.group(1) or "增加", _to_yi(mm.group(2), mm.group(3)))
        if blk:
            out["blocks"].append({"scope": scope, "n": n, **blk})
    return out


def mof_local_bonds(text: str) -> dict:
    """MOF '地方政府债券发行和债务余额情况' (亿元), blocks by period scope (month / ytd)."""
    t = _clean(text)
    out = {"blocks": []}
    m = re.search(r"(\d{4})年(\d{1,2})月", t)
    if m:
        out["year"], out["month"] = int(m.group(1)), int(m.group(2))
    pats = {
        # '发行新增债券845亿元' and, in 2022-23, '发行新增地方政府债券322亿元'
        "new": r"发行新增(?:地方政府)?债券" + NUM + r"亿元(?:,其中一般债券" + NUM + r"亿元、专项债券" + NUM + r"亿元)?",
        "refi": r"发行再融资(?:地方政府)?债券" + NUM + r"亿元(?:,其中一般债券" + NUM + r"亿元、专项债券" + NUM + r"亿元)?",
    }
    for scope, n, chunk in chunks_by_period(t):
        blk = {}
        for k, p in pats.items():
            mm = re.search(p, chunk)
            if mm:
                blk[f"{k}_total"] = float(mm.group(1))
                if mm.group(2) is not None:
                    blk[f"{k}_general"], blk[f"{k}_special"] = float(mm.group(2)), float(mm.group(3))
        # months with only one use: '发行一般债券702.43亿元,发行专项债券7148.21亿元;按用途划分,全部为新增债券'
        # (or '…全部为再融资债券', as in Jan-Feb 2021): the other use is zero
        allof = re.search(r"按用途划分,全部为(新增|再融资)债券", chunk)
        if allof and "new_total" not in blk and "refi_total" not in blk:
            mm = re.search(r"发行一般债券" + NUM + r"亿元,(?:发行)?专项债券" + NUM + r"亿元", chunk)
            if mm:
                g, sp = float(mm.group(1)), float(mm.group(2))
                use, other = ("new", "refi") if allof.group(1) == "新增" else ("refi", "new")
                blk.update({f"{use}_total": round(g + sp, 2), f"{use}_general": g, f"{use}_special": sp,
                            f"{other}_total": 0.0, f"{other}_general": 0.0, f"{other}_special": 0.0})
        for k, p in {"total": r"发行地方政府债券(?:合计)?" + NUM + "亿元",
                     "redemption": r"到期偿还本金" + NUM + "亿元",
                     "swap_refi": r"置换存量隐性债务[^,;。]{0,12}?" + NUM + "亿元"}.items():
            mm = re.search(p, chunk)
            if mm:
                blk[k] = float(mm.group(1))
        if blk:
            out["blocks"].append({"scope": scope, "n": n, **blk})
    return out


def ndrc_approvals(text: str) -> list[dict]:
    """NDRC press transcripts: '<period>共审批核准固定资产投资项目N个,总投资X亿元'.

    Returns all matches with the period phrase preceding each, so monthly vs cumulative
    statements are distinguishable (caller keeps single-month statements only)."""
    t = _clean(text)
    out = []
    rx = re.compile(r"((?:\d{4}年)?(?:\d{1,2}[-—至]\d{1,2}月|\d{1,2}月份?|今年以来|一季度|上半年|前三季度))?"
                    r"[^。;]{0,30}?审批核准(?:固定资产投资)?项目" + NUM + r"个,总投资" + CN_NUM)
    for mm in rx.finditer(t):
        out.append({"period_phrase": mm.group(1) or "", "count": int(float(mm.group(2))),
                    "investment_yi": _to_yi(mm.group(3), mm.group(4))})
    return out


def nbs_housing_tiers(text: str) -> dict:
    """NBS 70-city release text: tier m/m and y/y (%) for new and second-hand homes."""
    t = _clean(text)
    out = {}
    m = re.search(r"(\d{4})年(\d{1,2})月", t)
    if m:
        out["year"], out["month"] = int(m.group(1)), int(m.group(2))
    rx = re.compile(r"(一线|二线|三线)城市(新建商品住宅|二手住宅)销售价格([^。]*)")
    for mm in rx.finditer(t):
        key = {"一线": "tier1", "二线": "tier2", "三线": "tier3"}[mm.group(1)]
        sk = "new" if mm.group(2) == "新建商品住宅" else "used"
        seg = re.split(r"(?:一线|二线|三线)城市", mm.group(3))[0]
        for horizon, hk in [("环比", "mom"), ("同比", "yoy")]:
            h = re.search(horizon + r"(上涨|下降|持平)(\d+(?:\.\d+)?)?", seg)
            if h and f"{key}_{sk}_{hk}" not in out:
                v = 0.0 if h.group(1) == "持平" else float(h.group(2))
                out[f"{key}_{sk}_{hk}"] = -v if h.group(1) == "下降" else v
    return out


def nbs_infra_fai(text: str) -> dict:
    """NBS FAI release: YTD y/y (%) for infrastructure / manufacturing / real estate investment."""
    t = _clean(text)
    out = {}
    for cn, k in [(r"基础设施投资(?:\([^)]{0,20}\))?", "infra_ytd_yoy"), (r"(?<!高技术)(?<!装备)制造业投资", "mfg_ytd_yoy"),
                  ("房地产开发投资", "re_ytd_yoy")]:
        # December releases say '比上年增长'
        mm = re.search(cn + r"(?:同比|比上年)?(增长|下降)" + NUM + "%", t)
        if mm:
            v = float(mm.group(2))
            out[k] = -v if mm.group(1) == "下降" else v
    return out


def nbs_70city_tables(html: str) -> dict:
    """City-level indices from the NBS monthly 70-city release HTML.

    Uses 表1 (new commercial residential) and 表2 (second-hand residential): each row holds two
    cities, columns [city, m/m (prev=100), y/y (same month last year=100), YTD avg] x 2.
    Returns {"new": {city: (mom, yoy)}, "used": {...}}. Tables are identified by their
    caption text in document order, not by position.
    """
    from bs4 import BeautifulSoup

    from ..transform.housing import tiers

    known = set(sum(tiers().values(), []))
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for tbl in soup.find_all("table"):
        # caption text is split across several nodes: gather the ~120 characters before the table
        parts, n = [], 0
        for st in tbl.find_all_previous(string=True):
            parts.append(str(st))
            n += len(str(st).strip())
            if n > 120:
                break
        cap = re.sub(r"\s+", "", "".join(reversed(parts)))
        cap = cap[cap.rfind("表"):] if "表" in cap else cap
        seg = "new" if "新建商品住宅" in cap else "used" if "二手住宅" in cap else None
        if seg is None or seg in out:
            continue
        rows = [[re.sub(r"[\s\u3000]+", "", c.get_text()) for c in tr.find_all(["td", "th"])] for tr in tbl.find_all("tr")]
        # each row holds two cities; columns per city = 4 (m/m, y/y, YTD avg) or 3 in January
        data = {}
        num = re.compile(r"\d+(\.\d+)?")
        for r in rows:
            if len(r) not in (6, 8) or not r[0] or num.fullmatch(r[0]):
                continue
            step = len(r) // 2
            for k in (0, step):
                city, mom, yoy = r[k], r[k + 1], r[k + 2]
                if city in known and num.fullmatch(mom) and num.fullmatch(yoy):
                    data[city] = (float(mom), float(yoy))
        if len(data) >= 60:
            out[seg] = data
    return out


def nbs_fai_total(text: str):
    """(YTD level in RMB bn, YTD y/y %) from '固定资产投资（不含农户）293092亿元，同比下降7.2%'."""
    t = _clean(text)
    mm = re.search(r"固定资产投资\(不含农户\)" + NUM + r"亿元,(?:比上年|同比)(增长|下降)" + NUM + "%", t)
    if not mm:
        return None
    g = float(mm.group(3))
    return float(mm.group(1)) / 10, (-g if mm.group(2) == "下降" else g)


# ----------------------------------------------------------------------------- NBS releases
def _sgn(verb: str, v: str) -> float:
    return -float(v) if verb in ("下降", "减少") else float(v)


def _first(rx: str, t: str):
    return re.search(rx, t)


def nbs_retail(text: str) -> dict:
    """YTD and single-month retail sales (RMB bn, y/y %).

    Forms: '1—9月份，社会消费品零售总额370001亿元，同比增长1.1%' (YTD);
    '2021年，社会消费品零售总额440823亿元，比上年增长12.5%' (full year = December YTD);
    '1—2月份，…' (Jan-Feb: YTD and the single February observation coincide);
    '9月份，社会消费品零售总额42433亿元，同比增长1.1%[，环比增长0.06%]' (single month)."""
    t = _clean(text)
    out = {}
    # quarter-end releases state YTD as '一季度,…' / '上半年,…' / '前三季度,…'
    m = (_first(r"(?:1[—\-–]\d{1,2}月份|一季度|上半年|前三季度),社会消费品零售总额" + NUM + r"亿元,(?:同比|比上年)?(增长|下降)" + NUM + "%", t)
         or _first(r"(?:20\d\d年|全年),社会消费品零售总额" + NUM + r"亿元,(?:同比|比上年)?(增长|下降)" + NUM + "%", t))
    if m:
        out["retail.ytd"], out["retail.ytd_yoy"] = float(m.group(1)) / 10, _sgn(m.group(2), m.group(3))
    jf = _first(r"1[—\-–]2月份,社会消费品零售总额" + NUM + r"亿元,(?:同比)?(增长|下降)" + NUM + "%", t)
    if jf:
        out["retail.level"], out["retail.yoy"] = float(jf.group(1)) / 10, _sgn(jf.group(2), jf.group(3))
    m = _first(r"(?<![—\-–\d])\d{1,2}月份,社会消费品零售总额" + NUM + r"亿元,(?:同比)?(增长|下降)" + NUM + r"%(?:,环比(增长|下降)" + NUM + "%)?", t)
    if m:
        out["retail.level"], out["retail.yoy"] = float(m.group(1)) / 10, _sgn(m.group(2), m.group(3))
        if m.group(4):
            out["retail.mom_sa_nbs"] = _sgn(m.group(4), m.group(5))
    return out


def nbs_ip(text: str) -> dict:
    t = _clean(text)
    out = {}
    m = _first(r"(?<![—\-–\d])\d{1,2}月份,(?:全国)?规模以上工业增加值同比(?:实际)?(增长|下降)" + NUM + "%", t)
    if m:
        out["ip.yoy"] = _sgn(m.group(1), m.group(2))
    m = _first(r"规模以上工业增加值比上月(增长|下降)" + NUM + "%", t) or _first(r"规模以上工业增加值同比(?:实际)?(?:增长|下降)[\d.]+%,环比(增长|下降)" + NUM + "%", t)
    if m:
        out["ip.mom_sa_nbs"] = _sgn(m.group(1), m.group(2))
    m = (_first(r"(?:1[—\-–]\d{1,2}月份|一季度|上半年|前三季度),(?:全国)?规模以上工业增加值(?:同比)?(?:实际)?(增长|下降)" + NUM + "%", t)
         or _first(r"(?:20\d\d年|全年),(?:全国)?规模以上工业增加值(?:比上年|同比)?(?:实际)?(增长|下降)" + NUM + "%", t))
    if m:
        out["ip.ytd_yoy"] = _sgn(m.group(1), m.group(2))
    jf = _first(r"1[—\-–]2月份,(?:全国)?规模以上工业增加值(?:同比)?(?:实际)?(增长|下降)" + NUM + "%", t)
    if jf and "ip.yoy" not in out:      # Jan-Feb release: the single (February) observation = Jan-Feb growth
        out["ip.yoy"] = _sgn(jf.group(1), jf.group(2))
    for name, sid in [("水泥", "proxy.cement_yoy"), ("钢材", "proxy.steel_yoy"), ("发电量", "proxy.power_yoy")]:
        m = _first(name + NUM + r"(?:万吨|亿千瓦时),(?:同比)?(增长|下降)" + NUM + "%", t)
        if m:
            out[sid] = _sgn(m.group(2), m.group(3))
    return out


def nbs_property(text: str) -> dict:
    """YTD levels (RMB bn; mn sqm) and YTD y/y from '全国房地产市场基本情况'."""
    t = _clean(text)
    out = {}
    pats = [
        (r"全国房地产开发投资" + NUM + r"亿元,(?:比上年|同比)(增长|下降)" + NUM, "fai.re_ytd", 0.1),
        (r"房屋新开工面积" + NUM + r"万平方米,(?:同比)?(增长|下降)" + NUM, "re.starts_ytd", 0.01),
        (r"房屋竣工面积" + NUM + r"万平方米,(?:同比)?(增长|下降)" + NUM, "re.completions_ytd", 0.01),
        (r"(?:新建)?商品房销售面积" + NUM + r"万平方米,(?:比上年|同比)?(增长|下降)" + NUM, "re.sales_ytd", 0.01),
    ]
    for rx, sid, sc in pats:
        m = _first(rx, t)
        if m:
            out[sid] = float(m.group(1)) * sc
            out[f"{sid}_yoy"] = _sgn(m.group(2), m.group(3))
    m = _first(r"国房景气指数为?" + NUM, t)
    if m:
        out["re.climate"] = float(m.group(1))
    return out


def nbs_cpi(text: str) -> dict:
    t = _clean(text)
    out = {}
    m = _first(r"全国居民消费价格同比(上涨|下降)" + NUM + "%", t)
    if m:
        out["prices.cpi_yoy"] = _sgn(m.group(1), m.group(2))
    m = _first(r"全国居民消费价格同比持平", t)
    if m and "prices.cpi_yoy" not in out:
        out["prices.cpi_yoy"] = 0.0
    m = _first(r"消费品价格(上涨|下降)" + NUM + "%", t)
    if m:
        out["prices.cpi_goods_yoy"] = _sgn(m.group(1), m.group(2))
    return out


def nbs_ppi(text: str) -> dict:
    t = _clean(text)
    # forms: '全国工业生产者出厂价格同比下降2.2%'; '…出厂价格和购进价格同比均下降…';
    # '…出厂价格同比由上月下降0.9%转为上涨0.5%'; a lead sentence on m/m followed by
    # '工业生产者出厂价格同比下降3.6%' (no 全国)
    m = (_first(r"全国工业生产者出厂价格同比(上涨|下降)" + NUM + "%", t)
         or _first(r"全国工业生产者出厂价格和购进价格同比(?:均|分别)?(上涨|下降)" + NUM + "%", t)
         or _first(r"全国工业生产者出厂价格同比由上月[^,;。]*?转为(上涨|下降)" + NUM + "%", t)
         or _first(r"(?<![,、])工业生产者出厂价格同比(上涨|下降)" + NUM + "%", t))
    if m:
        return {"prices.ppi_yoy": _sgn(m.group(1), m.group(2))}
    if _first(r"全国工业生产者出厂价格同比持平", t):
        return {"prices.ppi_yoy": 0.0}
    return {}


QUARTER_WORD = {"一季度": 1, "二季度": 2, "三季度": 3, "四季度": 4}


def nbs_gdp(text: str) -> dict:
    """GDP block in the quarterly '国民经济' release (Jan / Apr / Jul / Oct).

    Returns keys with the quarter they refer to resolved by the caller:
      ytd_nominal, ytd_real_yoy, sector_{1,2,3}_ytd_nominal / _ytd_real_yoy,
      q_real_yoy (latest quarter), q_sa_qoq (latest quarter)."""
    t = _clean(text)
    out = {}
    m = _first(r"初步核算,(?:20\d\d年)?(一季度|上半年|前三季度|全年)?国内生产总值" + NUM + r"亿元,按不变价格计算,(?:比上年|同比)(增长|下降)" + NUM + "%", t)
    if m:
        out["ytd_nominal"], out["ytd_real_yoy"] = float(m.group(2)) / 10, _sgn(m.group(3), m.group(4))
        if m.group(1) == "一季度":                    # Q1: the quarter is the year to date
            out["q_index"], out["q_real_yoy"] = 1, out["ytd_real_yoy"]
    # Q1 releases: '…同比增长5.3%,比上年四季度环比增长1.6%' (or '比2021年四季度环比增长1.3%')
    m = _first(r"比(?:上年|20\d\d年)四季度环比(增长|下降)" + NUM + "%", t)
    if m:
        out["q_sa_qoq"] = _sgn(m.group(1), m.group(2))
        out.setdefault("q_index", 1)
    for i, name in enumerate(["第一产业", "第二产业", "第三产业"], 1):
        m = _first(name + r"增加值" + NUM + r"亿元,(?:比上年|同比)?(增长|下降)" + NUM + "%", t)
        if m:
            out[f"s{i}_ytd_nominal"], out[f"s{i}_ytd_real_yoy"] = float(m.group(1)) / 10, _sgn(m.group(2), m.group(3))
    # quarterly y/y: '分季度看,一季度国内生产总值同比增长5.4%,二季度增长5.2%,三季度增长4.8%'
    seg = _first(r"分季度看,([^。]*)", t)
    if seg:
        qs = re.findall(r"(一季度|二季度|三季度|四季度)(?:国内生产总值)?(?:同比)?(增长|下降)" + NUM + "%", seg.group(1))
        if qs:
            q, verb, v = qs[-1]
            out["q_index"], out["q_real_yoy"] = QUARTER_WORD[q], _sgn(verb, v)
    m = _first(r"从环比看,(一季度|二季度|三季度|四季度)国内生产总值(增长|下降)" + NUM + "%", t)
    if m:
        out["q_sa_qoq"] = _sgn(m.group(2), m.group(3))
        out.setdefault("q_index", QUARTER_WORD[m.group(1)])
    return out


def nbs_gdp_prelim(text: str) -> dict:
    """NBS '…国内生产总值初步核算结果' (tables flattened to text).

    Table 1: GDP and sector value added, current price (亿元) and real growth (%), for the quarter and
    the year to date ('二季度 上半年 二季度 上半年': 4 numbers) or, in Q1, one column each (2 numbers).
    Table 2: real y/y by quarter for every year shown; Table 3: SA q/q by quarter (this release's
    vintage of the whole history, so revisions are captured). Returns
    {'current': {...}, 'real_yoy': {(year, q): v}, 'sa_qoq': {(year, q): v}}."""
    t = re.sub(r"\s+", " ", text)
    out = {"current": {}, "real_yoy": {}, "sa_qoq": {}}
    num = r"(-?\d+(?:\.\d+)?)"
    t1 = re.search(r"表 ?1(.*?)注[:：]", t)
    if t1:
        body = t1.group(1).split("比上年同期增长", 1)[-1]
        for label, key in (("GDP", "gdp"), ("第一产业", "s1"), ("第二产业", "s2"), ("第三产业", "s3")):
            m = re.search(r"(?<![A-Za-z])" + label + r" " + num + r" " + num + r"(?: " + num + r" " + num + r")?(?= |$)", body)
            if not m:
                continue
            if m.group(3) is not None:            # quarter, YTD, quarter growth, YTD growth
                qn, yn, qg, yg = (float(m.group(i)) for i in range(1, 5))
            else:                                  # Q1: quarter = YTD
                qn = yn = float(m.group(1))
                qg = yg = float(m.group(2))
            if key == "gdp":
                out["current"].update(q_nominal=qn / 10, ytd_nominal=yn / 10, q_real_yoy=qg, ytd_real_yoy=yg)
            else:
                out["current"].update({f"{key}_ytd_nominal": yn / 10, f"{key}_ytd_real_yoy": yg})
    for key, cap in (("real_yoy", r"表 ?2 GDP ?同比增长速度"), ("sa_qoq", r"表 ?3 GDP ?环比增长速度")):
        seg = re.search(cap + r"(.*?)注[:：]", t)
        if not seg:
            continue
        rows = seg.group(1).split("4 季度", 1)[-1]
        for y, vals in re.findall(r"(20\d\d)((?: -?\d+\.\d+){1,4})", rows):
            for q, v in enumerate(vals.split(), 1):
                out[key][(int(y), q)] = float(v)
    return out


def nbs_tiers_interpretation(text: str) -> dict:
    """Tier m/m and y/y (%) from the NBS urban-department interpretation of the 70-city data."""
    t = _clean(text)
    out = {}
    segs = {"新建商品住宅": "new", "二手住宅": "used"}
    tiers = {"一线": "tier1", "二线": "tier2", "三线": "tier3"}
    for seg_cn, seg in segs.items():
        for h_cn, h in (("环比", "mom"), ("同比", "yoy")):
            # combined statements: '二、三线…分别下降0.3%和0.2%', '一、二、三线…分别下降1.4%、2.7%和3.5%',
            # '二、三线…由上月持平分别转为下降0.3%和0.2%', '…由上月上涨0.1%和0.2%转为持平', '…均下降1.0%'.
            # The clause ends at the first comma (the next clause is the other horizon or the change in pace);
            # each number takes the most recent 上涨/下降 before it.
            for pre, tks in (("一、二、三线", ("tier1", "tier2", "tier3")), ("(?<!、)二、三线", ("tier2", "tier3"))):
                m = _first(pre + r"城市" + seg_cn + r"销售价格" + h_cn + r"([^,;。]*)", t)
                if not m:
                    continue
                tail = m.group(1).rsplit("转为", 1)[-1]
                vals, verb = [], ""
                for v, num in re.findall(r"(上涨|下降)?(\d+(?:\.\d+)?)%", tail):
                    verb = v or verb
                    vals.append(_sgn(verb, num))
                if len(vals) == len(tks):
                    pass
                elif len(vals) == 1 and "均" in tail:
                    vals = vals * len(tks)
                elif not vals and re.match(r"(均)?持平", tail):
                    vals = [0.0] * len(tks)
                else:
                    continue
                for tk, v in zip(tks, vals):
                    out.setdefault(f"{tk}_{seg}_{h}", v)
            for tcn, tk in tiers.items():
                key = f"{tk}_{seg}_{h}"
                if key in out:
                    continue
                # '三线城市' must not match inside the combined '二、三线城市' statement
                m = _first(r"(?<!、)" + tcn + r"城市" + seg_cn + r"销售价格" + h_cn + r"([^,;。]*)", t)
                if not m:
                    continue
                phrase = m.group(1)
                z = re.search(r"转为(上涨|下降)" + NUM + "%", phrase)
                if z:
                    out[key] = _sgn(z.group(1), z.group(2))
                elif re.match(r"持平|由[^,]*转为持平", phrase):
                    out[key] = 0.0
                else:
                    z = re.match(r"(上涨|下降)" + NUM + "%", phrase)
                    if z:
                        out[key] = _sgn(z.group(1), z.group(2))
    return out
