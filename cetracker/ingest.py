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
def crawl(source: str, list_url: str, title_rx: str, pages: int = 10, page_fmt: str = "index_{n}.html",
          page_offset: int = 0, stop_when_empty: bool = False) -> list[dict]:
    """Collect {title, url, date} of releases whose link text matches title_rx.

    Page n >= 1 is list_url joined with page_fmt.format(n=n + page_offset) (PBoC numbers its
    second page 2, NBS/MOF/NDRC number it 1). Crawling stops at the first missing page.
    """
    out, seen = [], set()
    rx = re.compile(title_rx)
    for n in range(pages):
        url = list_url if n == 0 else urljoin(list_url, page_fmt.format(n=n + page_offset))
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
        if stop_when_empty and found == 0 and n > 0:
            break
    return out


def docx_text(content: bytes) -> str:
    """Plain text of a .docx (paragraph per line) using only the standard library."""
    import io
    import zipfile

    xml = zipfile.ZipFile(io.BytesIO(content)).read("word/document.xml").decode("utf-8", errors="replace")
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
    return "\n".join(re.sub(r"<[^>]+>", "", p) for p in paras)


def attachments(source: str, url: str, label_rx: str = r"实录") -> list[str]:
    """Absolute URLs of .docx/.doc attachments on a release page whose link text matches label_rx."""
    content, _, _ = fetch(source, url, ext="html")
    soup = BeautifulSoup(content.decode("utf-8", errors="replace"), "lxml")
    out = []
    for a in soup.find_all("a"):
        h = a.get("href") or ""
        if h.lower().endswith((".docx",)) and re.search(label_rx, a.get_text() or ""):
            out.append(urljoin(url, h))
    return out


def release_text(source: str, url: str) -> tuple[str, str, str, str]:
    content, raw, ts = fetch(source, url, ext="html")
    html = content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    text = soup.get_text("\n")
    # publication date: '发布时间：2026-09-14' (NBS/MOF), '文章来源：2026-09-1417:00:00' (PBoC)
    flat = re.sub(r"\s+", "", text)
    m = re.search(r"(?:发布时间|发布日期|文章来源|日期)[:：]?(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})", flat)
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


def ingest_pboc(pages: int = 120) -> pd.DataFrame:
    rows = []
    try:
        lst = crawl("PBoC", "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html",
                    r"(\d{4})年(\d{1,2}月|一季度|上半年|前三季度|全年)(金融统计数据报告|社会融资规模增量统计数据报告)",
                    pages=pages, page_fmt="11040-{n}.html", page_offset=1)
        if not lst:
            raise LookupError("no PBoC releases found on list pages")
        loan_recs, tsf_recs, money = [], [], []
        item_errors = []
        for item in lst:
            try:
                text, raw, ts, pub = release_text("PBoC", item["url"])
            except FetchError as e:
                item_errors.append(str(e)[:160])
                continue
            pub = pub or item["date"]
            # the report period comes from its title ('2026年8月…', '2026年前三季度…'); page text holds
            # navigation dates that must not set the period
            tp = title_period(item["title"], item["date"])
            if tp is None:
                continue
            # since 2023 the TSF section is part of the monthly 金融统计数据报告: parse both on every report
            p = releases.pboc_tsf(text)
            p["year"], p["month"] = tp.year, tp.month
            for b in p.get("blocks", []):
                tsf_recs.append((p.get("year"), p.get("month"), "", b, raw, ts, item["url"], pub))
            if "金融统计数据报告" in item["title"]:
                p = releases.pboc_financial_statistics(text)
                p["year"], p["month"] = tp.year, tp.month
                for b in p.get("blocks", []):
                    loan_recs.append((p.get("year"), p.get("month"), "", b, raw, ts, item["url"], pub))
                if "m1_yoy" in p:
                    money.append((p, raw, ts, item["url"], pub))
        lf = ["loans_total", "hh_total", "hh_short", "hh_medlong", "corp_total", "corp_short", "corp_medlong",
              "corp_bills", "nbfi_total"]
        rows += _rows_from_fields(_blocks_to_monthly(loan_recs, lf),
                                  {f: "loans_text." + f.replace("nbfi_total", "nbfi").replace("loans_total", "total") for f in lf},
                                  "PBoC", "primary")
        tf = ["tsf_flow", *releases.TSF_COMPONENTS.values()]
        rows += _rows_from_fields(_blocks_to_monthly(tsf_recs, tf),
                                  {f: ("tsf_text.flow" if f == "tsf_flow" else f"tsf_text.{f}") for f in tf}, "PBoC", "primary")
        for y, mth, _, b, raw, ts, url, pub in tsf_recs:
            if b.get("scope") == "stock" and "tsf_stock" in b and y:
                per = _month_end(y, mth)
                prov = store.Provenance("PBoC", "primary", url, raw, ts, pub or ts[:10], lineage="release_text:tsf_stock")
                rows.append(store.make_rows("tsf_text.stock", pd.Series([b["tsf_stock"] / 10], index=[per]), prov))
                rows.append(store.make_rows("tsf_text.stock_yoy", pd.Series([b["tsf_stock_yoy"]], index=[per]), prov))
        for p, raw, ts, url, pub in money:
            per = _month_end(p["year"], p["month"])
            for k in ("m1_yoy", "m2_yoy"):
                prov = store.Provenance("PBoC", "primary", url, raw, ts, pub or ts[:10], lineage=f"release_text:{k}")
                rows.append(store.make_rows(f"money.{k}", pd.Series([p[k]], index=[per]), prov))
        _status("PBoC releases", bool(rows), releases=len(lst), item_errors=item_errors)
    except Exception as e:
        _status("PBoC releases", False, error=f"{type(e).__name__}: {str(e)[:300]}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=store.COLUMNS)


def ingest_mof_lgb(pages: int = 40) -> pd.DataFrame:
    rows = []
    try:
        lst = crawl("MOF", "https://yss.mof.gov.cn/zhuantilanmu/dfzgl/sjtj/",
                    r"(\d{4})年(\d{1,2})月(份)?地方政府债券发行和债务余额情况", pages=pages, page_fmt="index_{n}.htm")
        if not lst:
            raise LookupError("no MOF local-bond releases found")
        recs, item_errors = [], []
        for item in lst:
            try:
                text, raw, ts, pub = release_text("MOF", item["url"])
            except FetchError as e:
                item_errors.append(str(e)[:160])
                continue
            p = releases.mof_local_bonds(text)
            for b in p.get("blocks", []):
                recs.append((p.get("year"), p.get("month"), "", b, raw, ts, item["url"], pub or item["date"]))
        fields = ["new_total", "new_general", "new_special", "refi_total", "refi_general", "refi_special", "total",
                  "redemption", "swap_refi"]
        mapping = {"new_total": "lgb.new_total_reported",
                   "new_general": "lgb.general_new", "new_special": "lgb.special_new", "refi_total": "lgb.refi_total",
                   "total": "lgb.total", "redemption": "lgb.redemption", "swap_refi": "lgb.refi_debt_swap",
                   "refi_general": "lgb.refi_general", "refi_special": "lgb.refi_special"}
        rows += _rows_from_fields(_blocks_to_monthly(recs, fields), mapping, "MOF", "primary")
        _status("MOF local bonds", bool(rows), releases=len(lst), item_errors=item_errors)
    except Exception as e:
        _status("MOF local bonds", False, error=f"{type(e).__name__}: {str(e)[:300]}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=store.COLUMNS)


def ingest_ndrc(pages: int = 30) -> pd.DataFrame:
    rows = []
    try:
        lst = crawl("NDRC", "https://www.ndrc.gov.cn/xwdt/xwfb/", r"新闻发布会", pages=pages)
        if not lst:
            raise LookupError("no NDRC press-conference transcripts found")
        item_errors, n_docx = [], 0
        for item in lst:
            try:
                text, raw, ts, pub = release_text("NDRC", item["url"])
                src_url = item["url"]
                # the full transcript is a .docx attachment ("发布会实录"); the page is a summary
                for att in attachments("NDRC", item["url"]):
                    content, raw, ts = fetch("NDRC", att, ext="docx")
                    text = docx_text(content)
                    src_url = att
                    n_docx += 1
                    break
            except Exception as e:
                item_errors.append(f"{item['url']}: {str(e)[:160]}")
                continue
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
                prov = store.Provenance("NDRC", "primary", src_url, raw, ts, pub, lineage="release_text:ndrc_approvals")
                rows.append(store.make_rows("ndrc.approvals_count", pd.Series([hit["count"]], index=[per]), prov))
                rows.append(store.make_rows("ndrc.approvals_value", pd.Series([hit["investment_yi"] / 10], index=[per]), prov))
        _status("NDRC approvals", bool(rows), transcripts=len(lst), docx=n_docx, months=len(rows) // 2,
                item_errors=item_errors[:20])
    except Exception as e:
        _status("NDRC approvals", False, error=f"{type(e).__name__}: {str(e)[:300]}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=store.COLUMNS)


QUARTER_PHRASE = {"一季度": 3, "上半年": 6, "前三季度": 9, "全年": 12}


def title_period(title: str, url_date: str, text: str = "") -> pd.Timestamp | None:
    """Reference period of an NBS release from its title.

    When the title omits the year, the year is taken from the release text (first
    '20XX年[1—]M月' matching the title's month) and only then from the URL date: NBS re-dated
    archived pages during a 2023 site migration, so URL dates of old releases are not
    publication dates (a 2020 interpretation sits under /202302/)."""
    y = re.search(r"(20\d\d)年", title)
    if not y and text:
        ms_ = re.findall(r"(\d{1,2})月", title)
        if ms_:
            y = re.search(r"(20\d\d)年(?:1[—\-–])?" + ms_[-1] + r"月", re.sub(r"\s+", "", text))
    ud = pd.Timestamp(url_date) if url_date else None
    for ph, mth in QUARTER_PHRASE.items():
        if ph in title and not re.search(r"\d{1,2}月", title):
            if not y and text:
                t = re.sub(r"\s+", "", text)
                y = re.search(r"(20\d\d)年" + ph, t)
                if not y:
                    rd = re.search(r"[（(](20\d\d)年(\d{1,2})月\d{1,2}日[）)]", t)   # release-date line
                    if rd:
                        return _month_end(int(rd.group(1)) - (1 if mth == 12 else 0), mth)
            year = int(y.group(1)) if y else (ud.year - 1 if (ud is not None and mth == 12) else ud.year if ud is not None else None)
            return _month_end(year, mth) if year else None
    ms = re.findall(r"(\d{1,2})月", title)
    if not ms:
        return _month_end(int(y.group(1)), 12) if y else None   # annual release, e.g. "2025年国民经济..."
    mth = int(ms[-1])
    year = int(y.group(1)) if y else (ud.year if ud is not None and mth <= ud.month else ud.year - 1 if ud is not None else None)
    return _month_end(year, mth) if year else None


ARCHIVE_GAP_DAYS = 75
NBS_TYPICAL_LAG_DAYS = 17


def release_vintage(per: pd.Timestamp, url_date: str, retrieved_at: str) -> tuple[str, str]:
    """(vintage date, flag). The URL date is the publication date unless it lies far after the
    reference period (pages re-dated in NBS's 2023 site migration); then the vintage is
    estimated as period end + the typical NBS release lag and flagged."""
    if url_date:
        ud = pd.Timestamp(url_date)
        if (ud - per).days <= ARCHIVE_GAP_DAYS:
            return url_date, ""
        return (per + pd.Timedelta(days=NBS_TYPICAL_LAG_DAYS)).strftime("%Y-%m-%d"), "vintage_estimated_archive"
    return retrieved_at[:10], "vintage_from_retrieval"


def ingest_nbs_release_texts(pages: int = 70) -> pd.DataFrame:
    """NBS monthly releases (www.stats.gov.cn/sj/zxfb, archive back to Nov 2021): the primary NBS
    route, because the NBS data portal refuses cloud IPs (HTTP 403). One parser per release type
    (RELEASE_PARSERS); every value carries the release URL and archived raw file."""
    rows, item_errors, counts = [], [], defaultdict(int)
    try:
        lst = crawl("NBS", "https://www.stats.gov.cn/sj/zxfb/", r"(20\d\d年|\d月份|季度|上半年)", pages=pages)
        for item in lst:
            kind = next((k for k, (rx, _) in RELEASE_PARSERS.items() if re.search(rx, item["title"])), None)
            if kind is None or title_period(item["title"], item["date"]) is None:
                continue
            try:
                content, raw, ts = fetch("NBS", item["url"], ext="html")
            except FetchError as e:
                item_errors.append(str(e)[:160])
                continue
            html = content.decode("utf-8", errors="replace")
            per = title_period(item["title"], item["date"], _text(html))
            vint, vflag = release_vintage(per, item["date"], ts)
            prov = store.Provenance("NBS", "primary", item["url"], raw, ts, vint, lineage=f"release_text:{kind}")
            try:
                vals = RELEASE_PARSERS[kind][1](html, per)
            except Exception as e:
                item_errors.append(f"{item['url']}: parse {type(e).__name__}: {str(e)[:120]}")
                continue
            for sid, (p, v) in vals.items():
                rows.append(store.make_rows(sid, pd.Series([v], index=[p]), prov, vflag))
            counts[kind] += 1
        # tier figures published in the urban-department interpretation (validation target)
        for item in crawl("NBS", "https://www.stats.gov.cn/sj/sjjd/", r"商品住宅销售价格变动情况", pages=pages):
            try:
                text, raw, ts, _ = release_text("NBS", item["url"])
            except FetchError as e:
                item_errors.append(str(e)[:160])
                continue
            per = title_period(item["title"], item["date"], text)
            if per is None:
                continue
            vint, vflag = release_vintage(per, item["date"], ts)
            prov = store.Provenance("NBS", "primary", item["url"], raw, ts, vint, lineage="release_text:70city_interpretation")
            for k, v in releases.nbs_tiers_interpretation(text).items():
                tier, seg, h = k.split("_")
                rows.append(store.make_rows(f"nbs_text.hp.{seg}.{tier}.{h}", pd.Series([v], index=[per]), prov, vflag))
            counts["70city_interpretation"] += 1
        _status("NBS releases", bool(rows), releases_listed=len(lst), parsed=dict(counts), item_errors=item_errors[:20])
    except Exception as e:
        _status("NBS releases", False, error=f"{type(e).__name__}: {str(e)[:300]}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=store.COLUMNS)


def _text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    return soup.get_text("\n")


def _at(per, d: dict) -> dict:
    return {k: (per, v) for k, v in d.items()}


def _parse_70city(html: str, per) -> dict:
    out = {}
    for seg, cities in releases.nbs_70city_tables(html).items():
        for city, (mom, yoy) in cities.items():
            out[f"city70.{seg}.mom.{city}"] = (per, mom)
            out[f"city70.{seg}.yoy.{city}"] = (per, yoy)
    return out


def _parse_fai(html: str, per) -> dict:
    t = _text(html)
    out = {f"fai.{k}": v for k, v in releases.nbs_infra_fai(t).items()}
    lv = releases.nbs_fai_total(t)
    if lv:
        out["fai.ytd"], out["fai.ytd_yoy"] = lv
    return _at(per, out)


def _parse_national(html: str, per) -> dict:
    """'国民经济' overview: monthly m/m SA for retail and IP, plus GDP in quarter releases."""
    t = _text(html)
    out = {k: v for k, v in {**releases.nbs_retail(t), **releases.nbs_ip(t)}.items() if k.endswith("mom_sa_nbs")}
    res = _at(per, out)
    g = releases.nbs_gdp(t)
    if g:
        qi = g.get("q_index") or {3: 1, 6: 2, 9: 3, 12: 4}.get(per.month)
        year = per.year if per.month >= 3 * (qi or 1) else per.year - 1
        qend = _month_end(year, 3 * qi) if qi else None
        if qend is not None:
            m = {"q_real_yoy": "gdp.real_yoy", "q_sa_qoq": "gdp.sa_qoq", "ytd_nominal": "gdp.nominal_ytd",
                 "ytd_real_yoy": "gdp.real_ytd_yoy"}
            for i, nm in ((1, "primary"), (2, "secondary"), (3, "tertiary")):
                m[f"s{i}_ytd_nominal"] = f"gdp.{nm}_nominal_ytd"
                m[f"s{i}_ytd_real_yoy"] = f"gdp.{nm}_real_ytd_yoy"
            for k, sid in m.items():
                if k in g:
                    res[sid] = (qend, g[k])
    return res


RELEASE_PARSERS = {
    "70city": (r"70个大中城市.*销售价格变动", _parse_70city),
    "fai": (r"全国固定资产投资", _parse_fai),
    "retail": (r"社会消费品零售总额", lambda h, p: _at(p, releases.nbs_retail(_text(h)))),
    "ip": (r"规模以上工业增加值", lambda h, p: _at(p, releases.nbs_ip(_text(h)))),
    "property": (r"房地产市场基本情况|全国房地产开发投资", lambda h, p: _at(p, releases.nbs_property(_text(h)))),
    "cpi": (r"居民消费价格", lambda h, p: _at(p, releases.nbs_cpi(_text(h)))),
    "ppi": (r"工业生产者出厂价格", lambda h, p: _at(p, releases.nbs_ppi(_text(h)))),
    "national": (r"国民经济", _parse_national),
}


# ------------------------------------------------------------------ PBoC statistics tables
PBOC_STATS = "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html"
PBOC_TABLES = {
    "社会融资规模增量统计表": "tsf_flow", "社会融资规模存量统计表": "tsf_stock",
    "货币供应量": "money", "金融机构人民币信贷收支表": "credit_rmb",
}


def _soup(source, url):
    content, raw, ts = fetch(source, url, ext="html")
    return BeautifulSoup(content.decode("utf-8", errors="replace"), "lxml"), raw, ts


def pboc_table_urls(first_year: int = 2012) -> list[tuple[int, str, str, str]]:
    """[(year, table kind, htm url, category url)] discovered from the PBoC statistics index."""
    soup, _, _ = _soup("PBoC", PBOC_STATS)
    years = {}
    for a in soup.find_all("a"):
        m = re.fullmatch(r"(20\d\d)年统计数据", (a.get_text() or "").strip())
        if m and int(m.group(1)) >= first_year:
            years[int(m.group(1))] = urljoin(PBOC_STATS, a["href"])
    cats = defaultdict(list)
    for a in soup.find_all("a"):
        t = (a.get_text() or "").strip()
        if t in ("社会融资规模", "货币统计概览", "金融机构信贷收支统计"):
            cats[t].append(urljoin(PBOC_STATS, a["href"]))
    out = []
    for yr, yurl in sorted(years.items()):
        prefix = yurl.rsplit("/", 2)[0] + "/" + yurl.rsplit("/", 2)[1] + "/"
        for cname, urls in cats.items():
            for cu in urls:
                if not cu.startswith(prefix):
                    continue
                csoup, _, _ = _soup("PBoC", cu)
                for tr in csoup.find_all("tr"):
                    if tr.find("tr"):
                        continue
                    a = tr.find("a", href=re.compile(r"\.htm$"))
                    if not a:
                        continue
                    label = re.sub(r"\s+", "", tr.get_text(" ", strip=True))
                    kind = next((k for name, k in PBOC_TABLES.items() if label.startswith(name)), None)
                    if kind:
                        out.append((yr, kind, urljoin(cu, a["href"]), cu))
    return out


def ingest_pboc_tables(first_year: int = 2012) -> pd.DataFrame:
    from .fetch import pboc_tables as PT

    rows, errors, counts = [], [], defaultdict(int)
    try:
        urls = pboc_table_urls(first_year)
        if not urls:
            raise LookupError("no PBoC statistics tables discovered")
        for yr, kind, url, cat in urls:
            try:
                content, raw, ts = fetch("PBoC", url, ext="htm")
                html = PT.decode(content)
                prov = store.Provenance("PBoC", "primary", url, raw, ts, ts[:10], lineage=f"pboc_table:{kind}:{yr}")
                if kind == "tsf_flow":
                    df = PT.tsf_flow_table(html)
                    fl = df["restated"].map({True: "restated_current_definition", False: ""})
                    for col in df.columns.drop("restated"):
                        sid = "tsf.flow" if col == "flow" else f"tsf.{col}"
                        rows.append(store.make_rows(sid, df[col].dropna() / 10, prov, fl))
                elif kind == "tsf_stock":
                    df = PT.tsf_stock_table(html)
                    tot = df[df.item == "stock"].set_index("period")
                    rows.append(store.make_rows("tsf.stock", tot["stock"] * 1000, prov))
                    rows.append(store.make_rows("tsf.stock_yoy", tot["yoy"], prov))
                    gov = df[df.item == "govt_bonds"].set_index("period")
                    if len(gov):
                        rows.append(store.make_rows("tsf.govt_bonds_stock", gov["stock"] * 1000, prov))
                elif kind == "money":
                    for code, ser in PT.money_supply(html).items():
                        rows.append(store.make_rows(f"money.{code.lower()}", ser.dropna() / 10, prov))
                elif kind == "credit_rmb":
                    for key, ser in PT.credit_rmb_loans(html).items():
                        rows.append(store.make_rows(f"loans_stock.{key}", ser.dropna() / 10, prov))
                counts[kind] += 1
            except Exception as e:
                errors.append(f"{yr} {kind}: {type(e).__name__}: {str(e)[:120]}")
        for df in rows:   # tables carry no publication date: vintage = retrieval date, flagged
            df["flags"] = df["flags"].where(df["flags"] == "", df["flags"] + ",") + "vintage_from_retrieval"
        _status("PBoC tables", bool(rows), tables=dict(counts), errors=errors[:20])
    except Exception as e:
        _status("PBoC tables", False, error=f"{type(e).__name__}: {str(e)[:300]}")
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
    # ingest_nbs (data portal) is not run: data.stats.gov.cn returns 403 to cloud runners; the
    # release pages carry the same official figures and are the NBS route.
    for fn in (ingest_nbs_release_texts, ingest_pboc_tables, ingest_pboc, ingest_mof_lgb, ingest_ndrc):
        try:
            frames.append(fn())
        except Exception:
            _status(fn.__name__, False, error=traceback.format_exc()[-400:])
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=store.COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    # a value cannot be published before its reference period ends: such rows carry a wrong
    # period or vintage (e.g. a year inferred from a re-dated archive URL) and are rejected
    bad = out["vintage_date"] < out["period"]
    if bad.any():
        STATUS["_rejected_rows"] = {
            "reason": "vintage_date earlier than period end",
            "count": int(bad.sum()),
            "examples": out[bad][["series_id", "period", "vintage_date", "source_url"]].head(20).to_dict(orient="records")}
    return out[~bad].reset_index(drop=True)
