"""NBS data portal (data.stats.gov.cn "easyquery") fetcher.

Indicator codes are *discovered* from the portal's own indicator tree (m=getTree) and
matched by Chinese name, not hard-coded: NBS renumbers codes occasionally, and a wrong
hard-coded code silently returns a different indicator. The resolved code is recorded in
every row's lineage ("nbs:<dbcode>:<code>:<cname>").

Databases used:
    hgyd  national, monthly        hgjd  national, quarterly       hgnd  national, annual
    csyd  70 cities, monthly (housing price indices; region dimension `reg`)
Period codes: monthly "202409"; quarterly "2024A".."2024D" (Q1..Q4); annual "2024".
"""
from __future__ import annotations

import json
import re
import time
from functools import lru_cache

import pandas as pd

from .http import fetch, FetchError

BASE = "https://data.stats.gov.cn/easyquery.htm"
SOURCE = "NBS"
SRC_URL = "https://data.stats.gov.cn/easyquery.htm?cn={db}"


def _json(content: bytes) -> dict | list:
    return json.loads(content.decode("utf-8", errors="replace"))


def get_tree(dbcode: str, node_id: str = "zb") -> list[dict]:
    content, _, _ = fetch(SOURCE, BASE, data={"id": node_id, "dbcode": dbcode, "wdcode": "zb", "m": "getTree"},
                          method="POST", ext="json")
    return _json(content)


@lru_cache(maxsize=8)
def full_tree(dbcode: str, max_nodes: int = 3000) -> tuple[tuple[str, str, str, bool], ...]:
    """Breadth-first walk of the indicator tree: (id, name, parent_path, is_parent)."""
    out, queue = [], [("zb", "")]
    while queue and len(out) < max_nodes:
        nid, path = queue.pop(0)
        for n in get_tree(dbcode, nid):
            p = f"{path}/{n['name']}"
            out.append((n["id"], n["name"], p, bool(n.get("isParent"))))
            if n.get("isParent"):
                queue.append((n["id"], p))
        time.sleep(0.3)
    return tuple(out)


def find_nodes(dbcode: str, pattern: str) -> list[tuple[str, str, str]]:
    """Leaf-parent nodes (the ones QueryData accepts) whose path matches regex `pattern`."""
    rx = re.compile(pattern)
    return [(i, n, p) for i, n, p, par in full_tree(dbcode) if not par and rx.search(p)]


def query(dbcode: str, code: str, period: str = "2005-", wds: list | None = None) -> tuple[pd.DataFrame, str, str]:
    """QueryData for one tree node; returns tidy frame (code, cname, unit, period, value)."""
    params = {
        "m": "QueryData", "dbcode": dbcode, "rowcode": "zb", "colcode": "sj",
        "wds": json.dumps(wds or [], ensure_ascii=False),
        "dfwds": json.dumps([{"wdcode": "zb", "valuecode": code}, {"wdcode": "sj", "valuecode": period}]),
        "k1": str(int(time.time() * 1000)),
    }
    content, raw, ts = fetch(SOURCE, BASE, params=params, ext="json")
    js = _json(content)
    if js.get("returncode") != 200:
        raise FetchError(f"NBS QueryData {dbcode}/{code}: {str(js)[:200]}")
    rd = js["returndata"]
    meta = {}
    for wd in rd["wdnodes"]:
        if wd["wdcode"] == "zb":
            for n in wd["nodes"]:
                meta[n["code"]] = (n["cname"], n.get("unit", ""))
    rows = []
    for dn in rd["datanodes"]:
        if not dn["data"].get("hasdata"):
            continue
        w = {x["wdcode"]: x["valuecode"] for x in dn["wds"]}
        c = w["zb"]
        rows.append({"code": c, "cname": meta.get(c, ("", ""))[0], "unit": meta.get(c, ("", ""))[1],
                     "sj": w["sj"], "reg": w.get("reg", ""), "value": float(dn["data"]["data"])})
    df = pd.DataFrame(rows)
    if len(df):
        df["period"] = df["sj"].map(parse_period)
    return df, raw, ts


def parse_period(sj: str) -> pd.Timestamp:
    if re.fullmatch(r"\d{6}", sj):
        return pd.Timestamp(int(sj[:4]), int(sj[4:]), 1) + pd.offsets.MonthEnd(0)
    if re.fullmatch(r"\d{4}[A-D]", sj):
        q = "ABCD".index(sj[4]) + 1
        return pd.Timestamp(int(sj[:4]), 3 * q, 1) + pd.offsets.MonthEnd(0)
    if re.fullmatch(r"\d{4}", sj):
        return pd.Timestamp(int(sj), 12, 31)
    raise ValueError(sj)


def regions(dbcode: str = "csyd") -> list[dict]:
    content, _, _ = fetch(SOURCE, BASE, data={"m": "getOtherWds", "dbcode": dbcode, "rowcode": "zb",
                                             "colcode": "sj", "wds": "[]", "k1": str(int(time.time() * 1000))},
                          method="POST", ext="json")
    js = _json(content)
    for wd in js.get("returndata", []):
        if wd.get("wdcode") == "reg":
            return wd["nodes"]
    return []
