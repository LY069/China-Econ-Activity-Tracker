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
}


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
    """DataFrame index=month-end, columns among TSF_COLS values (亿元)."""
    rows = _rows(html)
    header = None
    for r in rows:
        if any(c.startswith("社会融资规模增量") for c in r) and any("人民币贷款" in c for c in r):
            header = []
            for c in r:
                key = next((v for k, v in TSF_COLS.items() if c.startswith(k)), None)
                if key:
                    header.append(key)
            break
    if not header:
        raise ValueError("TSF flow header not found")
    data = {}
    for r in rows:
        m = PERIOD.match(r[0])
        if m and len(r) - 1 == len(header) and all(NUM.match(x) for x in r[1:]):
            data[_me(*m.groups())] = [float(x) for x in r[1:]]
    if not data:
        raise ValueError("no TSF flow rows")
    return pd.DataFrame.from_dict(data, orient="index", columns=header).sort_index()


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
        if len(vals) == len(periods) and all(NUM.match(v) or v in ("-", "—") for v in vals):
            items.append((r[0], [float(v) if NUM.match(v) else None for v in vals]))
    if periods is None:
        raise ValueError("period header not found")
    return periods, items


def money_supply(html: str) -> dict[str, pd.Series]:
    periods, items = month_columns_table(html)
    out = {}
    for label, vals in items:
        for code in ("M2", "M1", "M0"):
            if f"（{code}）" in label or f"({code})" in label:
                out[code] = pd.Series(vals, index=periods, dtype=float)
    return out


LOAN_KEYS = [
    # (row label prefix after numbering, sector context required, output key)
    ("各项贷款", None, "total"),
    ("住户贷款", None, "hh_total"),
    ("短期贷款", "hh", "hh_short"),
    ("中长期贷款", "hh", "hh_medlong"),
    ("企（事）业单位贷款", None, "corp_total"),
    ("非金融企业及机关团体贷款", None, "corp_total"),
    ("短期贷款", "corp", "corp_short"),
    ("中长期贷款", "corp", "corp_medlong"),
    ("票据融资", "corp", "corp_bills"),
    ("融资租赁", "corp", "corp_leases"),
    ("各项垫款", "corp", "corp_advances"),
    ("非银行业金融机构贷款", None, "nbfi"),
]


def credit_rmb_loans(html: str) -> dict[str, pd.Series]:
    """Loan stocks by sector and tenor from 金融机构人民币信贷收支表 (亿元), first occurrence of each,
    reading the uses side only. The household/corporate context follows the table's nesting."""
    periods, items = month_columns_table(html)
    out, ctx, in_uses = {}, None, False
    for label, vals in items:
        name = re.sub(r"^[（(]?[一二三四五六七八九十\d]+[）)、.]?", "", label)
        name = re.sub(r"[A-Za-z].*$", "", name)          # drop the English gloss
        if name.startswith("各项贷款"):
            in_uses = True
        if not in_uses:
            continue
        if name.startswith("住户贷款"):
            ctx = "hh"
        elif name.startswith(("企（事）业单位贷款", "非金融企业及机关团体贷款")):
            ctx = "corp"
        elif name.startswith(("非银行业金融机构贷款", "境外贷款", "债券投资")):
            ctx = None
        for prefix, need, key in LOAN_KEYS:
            if name.startswith(prefix) and (need is None or need == ctx) and key not in out:
                out[key] = pd.Series(vals, index=periods, dtype=float)
                break
        if name.startswith("境外贷款"):
            break
    return out
