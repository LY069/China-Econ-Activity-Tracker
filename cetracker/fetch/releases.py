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


PERIOD_PHRASE = r"(前(?:\d{1,2}|[一二三四五六七八九十]{1,2})个?月|\d{1,2}月份|一季度|上半年|前三季度|全年)"
CN_MONTHS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
             "十一": 11, "十二": 12}


def period_scope(phrase: str) -> tuple[str, int | None]:
    """('ytd'|'month', month count or month number) from a period phrase."""
    if phrase.endswith("月份"):
        return "month", int(phrase[:-2])
    if phrase.startswith("前"):
        n = phrase[1:].rstrip("月").rstrip("个")
        return "ytd", int(n) if n.isdigit() else CN_MONTHS.get(n)
    return "ytd", {"一季度": 3, "上半年": 6, "前三季度": 9, "全年": 12}.get(phrase)


PERIOD_ANY = re.compile(r"(\d{1,2}[-—–至]\d{1,2}月(?:份)?|前(?:\d{1,2}|[一二三四五六七八九十]{1,2})个?月|"
                        r"(?<![-—–至\d])\d{1,2}月(?:份|末)?|一季度|上半年|前三季度|全年|今年以来)")


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
        mm = re.search(r"\(" + code + r"\)余额" + CN_NUM + r",同比增长" + NUM + "%", t)
        if mm:
            out[key] = float(mm.group(3))
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
        "new": r"发行新增债券" + NUM + r"亿元(?:,其中一般债券" + NUM + r"亿元、专项债券" + NUM + r"亿元)?",
        "refi": r"发行再融资债券" + NUM + r"亿元(?:,其中一般债券" + NUM + r"亿元、专项债券" + NUM + r"亿元)?",
    }
    for scope, n, chunk in chunks_by_period(t):
        blk = {}
        for k, p in pats.items():
            mm = re.search(p, chunk)
            if mm:
                blk[f"{k}_total"] = float(mm.group(1))
                if mm.group(2) is not None:
                    blk[f"{k}_general"], blk[f"{k}_special"] = float(mm.group(2)), float(mm.group(3))
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
    for cn, k in [(r"基础设施投资(?:\([^)]{0,20}\))?", "infra_ytd_yoy"), ("制造业投资", "mfg_ytd_yoy"),
                  ("房地产开发投资", "re_ytd_yoy")]:
        mm = re.search(cn + r"(?:同比)?(增长|下降)" + NUM + "%", t)
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
