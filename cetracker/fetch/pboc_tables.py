"""PBoC statistics tables (www.pbc.gov.cn/diaochatongjisi/116219/116319/): full monthly history.

Navigation: statistics index -> per-year category pages (社会融资规模 / 货币统计概览 /
金融机构信贷收支统计) -> table rows -> HTML version of each table (an .htm attachment).

Parsers (values returned in the table's own unit; callers convert):
    tsf_flow_table      社会融资规模增量统计表: rows = months, columns = total + components (亿元)
    tsf_stock_table     社会融资规模存量统计表: rows = components, columns = (stock 万亿元, y/y %) per month
    month_columns_table 货币供应量 / 金融机构人民币信贷收支表: rows = items, columns = months (亿元)
"""
from __future__ import annotations

import re

import pandas as pd
from bs4 import BeautifulSoup

PERIOD = re.compile(r"^(20\d\d)\.(\d{1,2})$")
NUM = re.compile(r"^-?\d+(\.\d+)?$")

TSF_COLS = {
    "社会融资规模增量": "flow", "人民币贷款": "rmb_loans", "外币贷款": "fx_loans", "委托贷款": "entrusted",
    "信托贷款": "trust", "未贴现银行承兑汇票": "undiscounted_ba", "企业债券": "corp_bonds", "政府债券": "govt_bonds",
    "非金融企业境内股票": "equity", "存款类金融机构资产支持证券": "abs", "贷款核销": "writeoffs",
    "地方政府专项债券": "lg_special_bonds",   # 2018 vintage: only LG special bonds were in TSF
}


def _tsf_key(c: str) -> str | None:
    key = next((v for k, v in TSF_COLS.items() if c.startswith(k)), None)
    if key is None and c.startswith("社会融资规模"):   # 2013-14 tables: plain "社会融资规模" = flow
        key = "flow"
    return key


def decode(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.find_all("tr"):
        cells = [re.sub(r"\s+", "", c.get_text(strip=True)) for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c != ""]
        if cells:
            out.append(cells)
    return out


def _me(y: str, m: str) -> pd.Timestamp:
    return pd.Timestamp(int(y), int(m), 1) + pd.offsets.MonthEnd(0)


def tsf_flow_table(html: str) -> pd.DataFrame:
    """Monthly TSF flows (亿元) by component.

    A page can hold several blocks, each introduced by a '单位：' (unit) row: the year's flows,
    and (2019 page) 表1 = flows for 2017-19 restated to the current definition, and 表2 = the same
    as percentage shares. Only 亿元 blocks are read; for a month present in several blocks the
    later block (the restated one) wins and the row is flagged in the 'restated' column."""
    rows = _rows(html)
    blocks, unit, header, pending = [], None, None, None
    for r in rows:
        if r[0].startswith("单位"):
            unit = "yi" if "亿元" in r[0] else "pct" if "%" in r[0] else "other"
            header, pending = None, None
            blocks.append({"unit": unit, "rows": {}, "header": None})
            continue
        if not blocks:
            continue
        b = blocks[-1]
        if b["header"] is None:
            keys = [k for k in (_tsf_key(c) for c in r) if k]
            if pending is not None:
                keys = pending + [k for k in keys if k not in pending]
            if "rmb_loans" in keys and "flow" in keys:
                b["header"] = keys
            elif "flow" in keys:
                pending = keys            # header split over two rows (total, then components)
            continue
        m = PERIOD.match(r[0])
        if m and len(r) - 1 == len(b["header"]) and all(NUM.match(x) for x in r[1:]):
            b["rows"][_me(*m.groups())] = [float(x) for x in r[1:]]
    frames = []
    for i, b in enumerate(blocks):
        if b["unit"] != "yi" or not b["rows"] or not b["header"]:
            continue
        df = pd.DataFrame.from_dict(b["rows"], orient="index", columns=b["header"]).sort_index()
        df["restated"] = i > 0 and any(bb["unit"] == "yi" and bb["rows"] for bb in blocks[:i])
        frames.append(df)
    if not frames:
        raise ValueError("no TSF flow rows in 亿元 blocks")
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    # magnitude sanity: monthly TSF is thousands of 亿元; a share block (≤100) must never pass
    if (out["flow"].abs().median() < 1000):
        raise ValueError("TSF flow magnitudes implausible (looks like a % share block)")
    return out


def tsf_stock_table(html: str) -> pd.DataFrame:
    """Long frame: period, item, stock (万亿元), yoy (%)."""
    rows = _rows(html)
    periods = None
    for r in rows:
        ps = [PERIOD.match(c) for c in r]
        if len(r) >= 2 and all(ps):
            periods = [_me(*p.groups()) for p in ps]
            break
    if periods is None:
        raise ValueError("TSF stock period header not found")
    out = []
    for r in rows:
        key = next((v for k, v in TSF_COLS.items() if r[0].startswith(k) or r[0].startswith(k.replace("增量", "存量"))), None)
        if r[0].startswith("社会融资规模存量"):
            key = "stock"
        if key is None:
            continue
        vals = r[1:]
        for i, p in enumerate(periods):
            if 2 * i + 1 < len(vals) and NUM.match(vals[2 * i]) and NUM.match(vals[2 * i + 1]):
                out.append({"period": p, "item": key, "stock": float(vals[2 * i]), "yoy": float(vals[2 * i + 1])})
    return pd.DataFrame(out)


def month_columns_table(html: str) -> tuple[list[pd.Timestamp], list[tuple[str, list[float | None]]]]:
    """(periods, [(row label, values)]) for tables laid out as items x months.

    Only the first period header block is used (the M1 table appends a restated-2024 block
    after a footnote; it is returned separately by money_supply())."""
    rows = _rows(html)
    periods, items = None, []
    for r in rows:
        if periods is None:
            ps = [PERIOD.match(c) for c in r[1:]]
            if len(r) > 2 and all(ps):
                periods = [_me(*p.groups()) for p in ps]
            continue
        if r[0].startswith("注"):
            break
        vals = r[1:]
        # the current year's table has a 12-month header but rows end at the latest month published
        if 0 < len(vals) <= len(periods) and NUM.match(vals[0]) and all(NUM.match(v) or v in ("-", "—", "") for v in vals):
            vals = vals + [""] * (len(periods) - len(vals))
            items.append((r[0], [float(v) if NUM.match(v) else None for v in vals]))
    if periods is None:
        raise ValueError("period header not found")
    return periods, items


def money_supply(html: str) -> dict[str, pd.Series]:
    """M2/M1/M0 levels (亿元). The 2025 table also carries, after its footnote, the 2024 M1 restated
    on the new (Jan-2025) definition; it is returned as 'M1_restated' so 2025 y/y can be rebuilt."""
    periods, items = month_columns_table(html)
    out = {}
    for label, vals in items:
        for code in ("M2", "M1", "M0"):
            if f"（{code}）" in label or f"({code})" in label:
                out[code] = pd.Series(vals, index=periods, dtype=float)
    rows = _rows(html)
    note = next((i for i, r in enumerate(rows) if r[0].startswith("注") and "M1" in "".join(r)), None)
    if note is not None:
        rp = None
        for r in rows[note + 1:]:
            ps = [PERIOD.match(c) for c in r]
            if len(r) >= 6 and all(ps):
                rp = [_me(*p.groups()) for p in ps]
            elif rp and r[0].startswith("余额") and len(r) - 1 == len(rp):
                vals = [float(v) if NUM.match(v) else None for v in r[1:]]
                out["M1_restated"] = pd.Series(vals, index=rp, dtype=float).dropna()
                break
    return out


LOAN_KEYS = {
    # exact row names (numbering and English gloss stripped) -> key, with required sector context
    ("各项贷款", None): "total",
    ("住户贷款", None): "hh_total",
    ("短期贷款", "hh"): "hh_short",
    ("中长期贷款", "hh"): "hh_medlong",
    ("企（事）业单位贷款", None): "corp_total",
    ("非金融企业及机关团体贷款", None): "corp_total",
    ("非金融企业及其他部门贷款", None): "corp_total",   # 2012-14 layout
    ("短期贷款", "corp"): "corp_short",
    ("中长期贷款", "corp"): "corp_medlong",
    ("票据融资", "corp"): "corp_bills",
    ("融资租赁", "corp"): "corp_leases",
    ("各项垫款", "corp"): "corp_advances",
    ("非银行业金融机构贷款", None): "nbfi",
}
# 2012-14 layout splits household loans by purpose; tenor totals are sums of these rows
HH_OLD = {"短期消费性贷款": "hh_short", "短期经营性贷款": "hh_short",
          "中长期消费性贷款": "hh_medlong", "中长期经营性贷款": "hh_medlong"}
CORP_NAMES = ("企（事）业单位贷款", "非金融企业及机关团体贷款", "非金融企业及其他部门贷款")


def credit_rmb_loans(html: str) -> dict[str, pd.Series]:
    """Loan stocks by sector and tenor from 金融机构人民币信贷收支表 (亿元), uses side only.

    Row names are matched exactly after stripping numbering and the English gloss, within the
    household / corporate context given by the table's nesting. Handles the 2012-14 layout
    (household loans split by purpose; corporate sector named 非金融企业及其他部门贷款)."""
    periods, items = month_columns_table(html)
    out, ctx, in_uses = {}, None, False
    hh_old = {}
    for label, vals in items:
        name = re.sub(r"^[（(]?[一二三四五六七八九十\d]+[）)、.]?", "", label)
        name = re.sub(r"[A-Za-z].*$", "", name)          # drop the English gloss
        if name == "各项贷款":
            in_uses = True
        if not in_uses:
            continue
        if name == "住户贷款":
            ctx = "hh"
        elif name in CORP_NAMES:
            ctx = "corp"
        elif name.startswith(("非银行业金融机构贷款", "境外贷款", "债券投资", "有价证券")):
            ctx = None
        ser = pd.Series(vals, index=periods, dtype=float)
        if ctx == "hh" and name in HH_OLD:
            k = HH_OLD[name]
            hh_old[k] = hh_old[k] + ser if k in hh_old else ser
        key = LOAN_KEYS.get((name, ctx)) or LOAN_KEYS.get((name, None))
        if key and key not in out:
            out[key] = ser
        if name.startswith("境外贷款"):
            break
    for k, ser in hh_old.items():
        out.setdefault(k, ser)
    return out
