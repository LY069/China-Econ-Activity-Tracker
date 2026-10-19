"""Endpoint probe (round 2): run on a GitHub runner; commits raw responses under probe/.
Development aid only; nothing here feeds the store."""
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

OUT = Path("probe")
OUT.mkdir(exist_ok=True)
for f in OUT.glob("*"):
    f.unlink()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
summary = {}


def get(name, url, ext="html"):
    try:
        r = S.get(url, timeout=40)
        (OUT / f"{name}.{ext}").write_bytes(r.content[:4_000_000])
        summary[name] = {"status": r.status_code, "bytes": len(r.content), "url": r.url,
                         "ctype": r.headers.get("Content-Type", "")}
        return r
    except Exception as e:
        summary[name] = {"error": repr(e)[:300], "url": url}


def links(r, pat=None):
    if r is None or r.status_code != 200:
        return []
    s = BeautifulSoup(r.content.decode("utf-8", "replace"), "lxml")
    out = []
    for a in s.find_all("a"):
        t = (a.get("title") or a.get_text() or "").strip()
        h = a.get("href")
        if h and (pat is None or re.search(pat, t + " " + h)):
            out.append((t, urljoin(r.url, h)))
    return out


# PBoC: 2025 and 2016 category pages, first tables and their attachments
for yr, base in [("2025", "/diaochatongjisi/116219/116319/5570903/"), ("2016", "/diaochatongjisi/116219/116319/3013637/")]:
    for cat, sub in [("money", {"2025": "5570886", "2016": "3013641"}[yr]), ("credit", {"2025": "5570888", "2016": "3013643"}[yr])]:
        r = get(f"pboc_{yr}_{cat}", f"https://www.pbc.gov.cn{base}{sub}/index.html")
        ls = links(r)
        summary[f"pboc_{yr}_{cat}_links"] = [(t[:40], u) for t, u in ls if "/diaochatongjisi/" in u or u.endswith((".xls", ".xlsx", ".htm", ".pdf"))][:80]
        tbl = [(t, u) for t, u in ls if re.search(r"社会融资规模|信贷收支|货币供应", t)][:4]
        for i, (t, u) in enumerate(tbl):
            rr = get(f"pboc_{yr}_{cat}_tbl{i}", u)
            att = [(tt, uu) for tt, uu in links(rr) if re.search(r"\.(xls|xlsx|htm|pdf)$", uu)][:6]
            summary[f"pboc_{yr}_{cat}_tbl{i}_att"] = [t] + att
            for j, (tt, uu) in enumerate(att[:3]):
                ext = uu.rsplit(".", 1)[-1]
                get(f"pboc_{yr}_{cat}_tbl{i}_att{j}", uu, ext)

# NBS: list depth binary search
lo, hi = 50, 150
while hi - lo > 1:
    mid = (lo + hi) // 2
    r = S.get(f"https://www.stats.gov.cn/sj/zxfb/index_{mid}.html", timeout=30)
    lo, hi = (mid, hi) if r.status_code == 200 else (lo, mid)
summary["nbs_zxfb_last_page"] = lo
r = get("nbs_list_last", f"https://www.stats.gov.cn/sj/zxfb/index_{lo}.html")
summary["nbs_list_last_titles"] = [t for t, u in links(r) if re.search(r"20\d\d年", t)][:20]
# one sample of each NBS release type from recent pages
want = {"retail": "社会消费品零售总额", "ip": "规模以上工业增加值", "property": "房地产市场基本情况|房地产开发投资",
        "gdp": "国内生产总值初步核算|国民经济", "cpi": "居民消费价格", "ppi": "工业生产者出厂价格", "energy": "能源生产情况"}
got = {}
for n in range(0, 8):
    url = "https://www.stats.gov.cn/sj/zxfb/" + ("" if n == 0 else f"index_{n}.html")
    for t, u in links(S.get(url, timeout=30) and get(f"nbs_list_p{n}", url)):
        for k, pat in want.items():
            if k not in got and re.search(pat, t):
                got[k] = (t, u)
summary["nbs_samples"] = got
for k, (t, u) in got.items():
    get(f"nbs_sample_{k}", u)
# NBS interpretation of 70-city
r = get("nbs_sjjd", "https://www.stats.gov.cn/sj/sjjd/")
jd = [(t, u) for t, u in links(r) if "商品住宅销售价格" in t][:1]
summary["nbs_sjjd_70"] = jd
for t, u in jd:
    get("nbs_sjjd_70city", u)
# NDRC list depth and MOF older lists
for n in (40, 60, 80):
    get(f"ndrc_list_{n}", f"https://www.ndrc.gov.cn/xwdt/xwfb/index_{n}.html")
for n in (9, 11, 13):
    get(f"mof_lgb_list_{n}", f"https://yss.mof.gov.cn/zhuantilanmu/dfzgl/sjtj/index_{n}.htm")
# MOF treasury bond issuance announcements
r = get("mof_gks_gzgl", "https://gks.mof.gov.cn/ztztz/guozaiguanli/")
summary["mof_gks_links"] = [(t[:40], u) for t, u in links(r)][:60]

(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps({k: v for k, v in summary.items() if not k.endswith("links")}, ensure_ascii=False)[:3000])
