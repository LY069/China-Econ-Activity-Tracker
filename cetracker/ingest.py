"""Ingest: raw sources -> store rows (primary / fallback tiers), plus a source-status report.

Every source runs inside its own try/except: a blocked or changed source is reported in
site/data/source_status.json (and in the app) and never stops the others. Nothing is
filled in for a failed source; the corresponding charts show "no data" with the reason.
"""
from __future__ import annotations

import re
import traceback
from collections import defaultdict
from datetime import date
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

from . import store
from .fetch import nbs, releases
from .fetch.http import fetch, FetchError, reachable
from .registry import S

STATUS: dict[str, dict] = {}


def _status(source: str, ok: bool, **kw):
    STATUS.setdefault(source, {}).update({"ok": ok, **kw})


# ------------------------------------------------------------------ NBS portal
def ingest_nbs(start: str = "2005-") -> pd.DataFrame:
    rows, resolved, failed = [], {}, {}
    cache: dict[tuple, tuple] = {}
    for sid, m in S.items():
        if "nbs" not in m:
            continue
        db, path_rx, cname_rx = m["nbs"]
        try:
            nodes = nbs.find_nodes(db, path_rx)
            if not nodes:
                raise LookupError(f"no tree node matches {path_rx!r} in {db}")
            hit = None
            for code, name, path in nodes:
                key = (db, code)
                if key not in cache:
                    cache[key] = nbs.query(db, code, start)
                df, raw, ts = cache[key]
                if df.empty:
                    continue
                sub = df[df["cname"].str.contains(cname_rx, regex=True)]
                if not sub.empty:
                    hit = (sub, raw, ts, path)
                    break
            if hit is None:
                raise LookupError(f"no child cname matches {cname_rx!r} under {[n[2] for n in nodes][:3]}")
            sub, raw, ts, path = hit
            if sub["code"].nunique() > 1:
                sub = sub[sub["code"] == sub["code"].iloc[0]]
            s = pd.Series(sub["value"].values, index=sub["period"]).sort_index()
            s = s * m.get("scale", 1.0) + m.get("offset", 0.0)
            code, cname = sub["code"].iloc[0], sub["cname"].iloc[0]
            prov = store.Provenance("NBS", "primary", nbs.SRC_URL.format(db=db), raw, ts, ts[:10],
                                    lineage=f"nbs:{db}:{code}:{cname}")
            rows.append(store.make_rows(sid, s, prov))
            resolved[sid] = f"{db}:{code}:{cname} ({path})"
        except Exception as e:
            failed[sid] = f"{type(e).__name__}: {str(e)[:200]}"
    _status("NBS portal", bool(resolved), resolved=resolved, failed=failed)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=store.COLUMNS)


def ingest_nbs_70city(start: str = "2011-") -> pd.DataFrame:
    """City-level 70-city indices (prev month = 100 and same month last year = 100)."""
    rows = []
    try:
        regs = nbs.regions("csyd")
        if not regs:
            raise LookupError("csyd region list empty")
        specs = {
            ("new", "mom"): r"新建商品住宅销售价格指数\(上月=100\)",
            ("new", "yoy"): r"新建商品住宅销售价格指数\(上年同月=100\)",
            ("used", "mom"): r"二手住宅销售价格指数\(上月=100\)",
            ("used", "yoy"): r"二手住宅销售价格指数\(上年同月=100\)",
        }
        nodes = nbs.find_nodes("csyd", r"住宅销售价格")
        if not nodes:
            raise LookupError("no csyd node for housing price indices")
        for r in regs:
            for code, _, _ in nodes:
                df, raw, ts = nbs.query("csyd", code, start, wds=[{"wdcode": "reg", "valuecode": r["code"]}])
                for (seg, h), rx in specs.items():
                    sub = df[df["cname"].str.contains(rx, regex=True)] if len(df) else df
                    if sub.empty:
                        continue
                    s = pd.Series(sub["value"].values, index=sub["period"]).sort_index()
                    prov = store.Provenance("NBS", "primary", nbs.SRC_URL.format(db="csyd"), raw, ts, ts[:10],
                                            lineage=f"nbs:csyd:{sub['code'].iloc[0]}:{r['name']}")
                    rows.append(store.make_rows(f"city70.{seg}.{h}.{r['name']}", s, prov))
        _status("NBS 70-city", bool(rows), cities=len(regs), series=len(rows))
    except Exception as e:
        _status("NBS 70-city", False, error=f"{type(e).__name__}: {str(e)[:300]}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=store.COLUMNS)


# ------------------------------------------------------------------ release-text crawlers
def crawl(source: str, list_url: str, title_rx: str, pages: int = 10, page_fmt: str = "index_{n}.html") -> list[dict]:
    """Collect {title, url, date} of releases whose link text matches title_rx."""
    out, seen = [], set()
    rx = re.compile(title_rx)
    for n in range(pages):
        url = list_url if n == 0 else urljoin(list_url, page_fmt.format(n=n))
        try:
            content, _, _ = fetch(source, url, ext="html", retries=2)
        except FetchError:
            break
        soup = BeautifulSoup(content.decode("utf-8", errors="replace"), "lxml")
        found = 0
        for a in soup.find_all("a"):
            title = (a.get("title") or a.get_text() or "").strip()
            href = a.get("href")
            if not href or not rx.search(title):
                continue
            full = urljoin(url, href)
            if full in seen:
                continue
            seen.add(full)
            m = re.search(r"t(20\d{6})", full)
            out.append({"title": title, "url": full, "date": f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else ""})
            found += 1
        if found == 0 and n > 0:
            break
    return out


def release_text(source: str, url: str) -> tuple[str, str, str, str]:
    content, raw, ts = fetch(source, url, ext="html")
    html = content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    text = soup.get_text("\n")
    m = re.search(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})", " ".join(soup.find_all(string=re.compile("发布时间|日期"))[:1]) or "")
    pub = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""
    return text, raw, ts, pub


def _month_end(y: int, m: int) -> pd.Timestamp:
    return pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)


def _blocks_to_monthly(recs: list[tuple[int, int, str, dict, str, str, str, str]], fields: list[str]):
    """Turn parsed release blocks into monthly flows per field.

    Uses the single-month block when present; otherwise differences consecutive YTD blocks
    (flag `from_ytd_diff`). Returns {field: [(period, value, raw, ts, url, vintage, flag)]}.
    """
    by_field = defaultdict(list)
    ytd = defaultdict(dict)
    month = defaultdict(dict)
    for y, mth, _, blk, raw, ts, url, pub in recs:
        for f in fields:
            if f not in blk:
                continue
            if blk["scope"] == "month" and blk.get("n") == mth:
                month[f][(y, mth)] = (blk[f], raw, ts, url, pub)
            elif blk["scope"] == "ytd" and blk.get("n") == mth:
                ytd[f][(y, mth)] = (blk[f], raw, ts, url, pub)
    for f in fields:
        keys = set(month[f]) | set(ytd[f])
        for (y, mth) in sorted(keys):
            if (y, mth) in month[f]:
                v, raw, ts, url, pub = month[f][(y, mth)]
                by_field[f].append((_month_end(y, mth), v, raw, ts, url, pub, ""))
            elif mth == 1 and (y, 1) in ytd[f]:
                v, raw, ts, url, pub = ytd[f][(y, 1)]
                by_field[f].append((_month_end(y, 1), v, raw, ts, url, pub, ""))
            elif (y, mth) in ytd[f] and (y, mth - 1) in ytd[f]:
                v, raw, ts, url, pub = ytd[f][(y, mth)]
                by_field[f].append((_month_end(y, mth), v - ytd[f][(y, mth - 1)][0], raw, ts, url, pub, "from_ytd_diff"))
    return by_field


def _rows_from_fields(by_field, mapping: dict[str, str], source: str, tier: str, scale: float = 0.1) -> list[pd.DataFrame]:
    rows = []
    for f, sid in mapping.items():
        for (p, v, raw, ts, url, pub, flag) in by_field.get(f, []):
            prov = store.Provenance(source, tier, url, raw, ts, pub or ts[:10], lineage=f"release_text:{f}")
            rows.append(store.make_rows(sid, pd.Series([v * scale], index=[p]), prov, flag))
    return rows


def ingest_pboc(pages: int = 40) -> pd.DataFrame:
    rows = []
    try:
        lst = crawl("PBoC", "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html",
                    r"(\d{4})年(\d{1,2})月(金融统计数据报告|社会融资规模增量统计数据报告)", pages=pages,
                    page_fmt="index{n}.html")
        if not lst:
            raise LookupError("no PBoC releases found on list pages")
        loan_recs, tsf_recs, money = [], [], []
        for item in lst:
            text, raw, ts, pub = release_text("PBoC", item["url"])
            pub = pub or item["date"]
            if "社会融资规模" in item["title"]:
                p = releases.pboc_tsf(text)
                for b in p.get("blocks", []):
                    tsf_recs.append((p.get("year"), p.get("month"), "", b, raw, ts, item["url"], pub))
            else:
                p = releases.pboc_financial_statistics(text)
                for b in p.get("blocks", []):
                    loan_recs.append((p.get("year"), p.get("month"), "", b, raw, ts, item["url"], pub))
                if "m1_yoy" in p:
                    money.append((p, raw, ts, item["url"], pub))
        lf = ["loans_total", "hh_total", "hh_short", "hh_medlong", "corp_total", "corp_short", "corp_medlong",
              "corp_bills", "nbfi_total"]
        rows += _rows_from_fields(_blocks_to_monthly(loan_recs, lf),
                                  {f: "loans." + f.replace("nbfi_total", "nbfi").replace("loans_total", "total") for f in lf},
                                  "PBoC", "primary")
        tf = ["tsf_flow", *releases.TSF_COMPONENTS.values()]
        rows += _rows_from_fields(_blocks_to_monthly(tsf_recs, tf),
                                  {f: ("tsf.flow" if f == "tsf_flow" else f"tsf.{f}") for f in tf}, "PBoC", "primary")
        for y, mth, _, b, raw, ts, url, pub in tsf_recs:
            if b.get("scope") == "stock" and "tsf_stock" in b and y:
                per = _month_end(y, mth)
                prov = store.Provenance("PBoC", "primary", url, raw, ts, pub or ts[:10], lineage="release_text:tsf_stock")
                rows.append(store.make_rows("tsf.stock", pd.Series([b["tsf_stock"] / 10], index=[per]), prov))
                rows.append(store.make_rows("tsf.stock_yoy", pd.Series([b["tsf_stock_yoy"]], index=[per]), prov))
        for p, raw, ts, url, pub in money:
            per = _month_end(p["year"], p["month"])
            for k in ("m1_yoy", "m2_yoy"):
                prov = store.Provenance("PBoC", "primary", url, raw, ts, pub or ts[:10], lineage=f"release_text:{k}")
                rows.append(store.make_rows(f"money.{k}", pd.Series([p[k]], index=[per]), prov))
        _status("PBoC releases", bool(rows), releases=len(lst))
    except Exception as e:
        _status("PBoC releases", False, error=f"{type(e).__name__}: {str(e)[:300]}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=store.COLUMNS)


def ingest_mof_lgb(pages: int = 15) -> pd.DataFrame:
    rows = []
    try:
        lst = crawl("MOF", "https://yss.mof.gov.cn/zhuantilanmu/dfzgl/sjtj/",
                    r"(\d{4})年(\d{1,2})月(份)?地方政府债券发行和债务余额情况", pages=pages, page_fmt="index_{n}.htm")
        if not lst:
            raise LookupError("no MOF local-bond releases found")
        recs = []
        for item in lst:
            text, raw, ts, pub = release_text("MOF", item["url"])
            p = releases.mof_local_bonds(text)
            for b in p.get("blocks", []):
                recs.append((p.get("year"), p.get("month"), "", b, raw, ts, item["url"], pub or item["date"]))
        fields = ["new_general", "new_special", "refi_total", "refi_general", "refi_special", "total", "redemption", "swap_refi"]
        mapping = {"new_general": "lgb.general_new", "new_special": "lgb.special_new", "refi_total": "lgb.refi_total",
                   "total": "lgb.total", "redemption": "lgb.redemption", "swap_refi": "lgb.refi_debt_swap",
                   "refi_general": "lgb.refi_general", "refi_special": "lgb.refi_special"}
        rows += _rows_from_fields(_blocks_to_monthly(recs, fields), mapping, "MOF", "primary")
        _status("MOF local bonds", bool(rows), releases=len(lst))
    except Exception as e:
        _status("MOF local bonds", False, error=f"{type(e).__name__}: {str(e)[:300]}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=store.COLUMNS)


def ingest_ndrc(pages: int = 30) -> pd.DataFrame:
    rows = []
    try:
        lst = crawl("NDRC", "https://www.ndrc.gov.cn/xwdt/xwfb/", r"新闻发布会", pages=pages)
        if not lst:
            raise LookupError("no NDRC press-conference transcripts found")
        for item in lst:
            text, raw, ts, pub = release_text("NDRC", item["url"])
            pub = pub or item["date"]
            if not pub:
                continue
            pubd = pd.Timestamp(pub)
            for hit in releases.ndrc_approvals(text):
                ph = hit["period_phrase"]
                m = re.fullmatch(r"(?:(\d{4})年)?(\d{1,2})月份?", ph)
                if not m:
                    continue  # cumulative / quarterly statements are not single-month flows
                mth = int(m.group(2))
                yr = int(m.group(1)) if m.group(1) else (pubd.year if mth <= pubd.month else pubd.year - 1)
                per = _month_end(yr, mth)
                prov = store.Provenance("NDRC", "primary", item["url"], raw, ts, pub, lineage="release_text:ndrc_approvals")
                rows.append(store.make_rows("ndrc.approvals_count", pd.Series([hit["count"]], index=[per]), prov))
                rows.append(store.make_rows("ndrc.approvals_value", pd.Series([hit["investment_yi"] / 10], index=[per]), prov))
        _status("NDRC approvals", bool(rows), transcripts=len(lst), months=len(rows) // 2)
    except Exception as e:
        _status("NDRC approvals", False, error=f"{type(e).__name__}: {str(e)[:300]}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=store.COLUMNS)


def ingest_nbs_release_texts(pages: int = 20) -> pd.DataFrame:
    """NBS release texts: infrastructure FAI growth and tier housing commentary (for validation)."""
    rows = []
    try:
        lst = crawl("NBS", "https://www.stats.gov.cn/sj/zxfb/",
                    r"(全国固定资产投资|70个大中城市)", pages=pages)
        for item in lst:
            text, raw, ts, pub = release_text("NBS", item["url"])
            pub = pub or item["date"]
            m = re.search(r"(\d{4})年(?:1[—\-–])?(\d{1,2})月", item["title"])
            if not m:
                continue
            per = _month_end(int(m.group(1)), int(m.group(2)))
            prov = store.Provenance("NBS", "primary", item["url"], raw, ts, pub or ts[:10], lineage="release_text")
            if "固定资产投资" in item["title"]:
                p = releases.nbs_infra_fai(text)
                if "infra_ytd_yoy" in p:
                    rows.append(store.make_rows("fai.infra_ytd_yoy", pd.Series([p["infra_ytd_yoy"]], index=[per]), prov))
            else:
                p = releases.nbs_housing_tiers(text)
                for k, v in p.items():
                    if k in ("year", "month"):
                        continue
                    tier, seg, h = k.split("_")
                    rows.append(store.make_rows(f"nbs_text.hp.{seg}.{tier}.{h}", pd.Series([v], index=[per]), prov))
        _status("NBS release texts", bool(rows), releases=len(lst))
    except Exception as e:
        _status("NBS release texts", False, error=f"{type(e).__name__}: {str(e)[:300]}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=store.COLUMNS)


PROBES = {
    "NBS portal": "https://data.stats.gov.cn/",
    "NBS releases": "https://www.stats.gov.cn/",
    "PBoC": "https://www.pbc.gov.cn/",
    "MOF": "https://www.mof.gov.cn/",
    "NDRC": "https://www.ndrc.gov.cn/",
    "ChinaBond": "https://www.chinabond.com.cn/",
    "CELMA": "https://www.celma.org.cn/",
    "Investment approval platform": "https://www.tzxm.gov.cn/",
}


def run_all() -> pd.DataFrame:
    STATUS.clear()
    for name, url in PROBES.items():
        ok, msg = reachable(url)
        STATUS.setdefault("_reachability", {})[name] = {"url": url, "ok": ok, "detail": msg}
    frames = []
    for fn in (ingest_nbs, ingest_nbs_70city, ingest_nbs_release_texts, ingest_pboc, ingest_mof_lgb, ingest_ndrc):
        try:
            frames.append(fn())
        except Exception:
            _status(fn.__name__, False, error=traceback.format_exc()[-400:])
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=store.COLUMNS)
