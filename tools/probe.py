"""Endpoint probe: run on a GitHub runner (open internet) to inspect live source behaviour.

Writes probe/<name>.(txt|html|json|bin) plus probe/summary.json. Development aid only;
nothing here feeds the store.
"""
import json
import re
import time
from pathlib import Path

import requests

OUT = Path("probe")
OUT.mkdir(exist_ok=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
summary = {}


def save(name, r, ext="html"):
    try:
        (OUT / f"{name}.{ext}").write_bytes(r.content[:3_000_000])
        summary[name] = {"status": r.status_code, "bytes": len(r.content), "url": r.url,
                         "ctype": r.headers.get("Content-Type", ""), "set_cookie": r.headers.get("Set-Cookie", "")[:200]}
    except Exception as e:
        summary[name] = {"error": repr(e)[:300]}


def get(s, name, url, ext="html", **kw):
    try:
        r = s.get(url, timeout=40, **kw)
        save(name, r, ext)
        return r
    except Exception as e:
        summary[name] = {"error": repr(e)[:300], "url": url}


def post(s, name, url, ext="json", **kw):
    try:
        r = s.post(url, timeout=40, **kw)
        save(name, r, ext)
        return r
    except Exception as e:
        summary[name] = {"error": repr(e)[:300], "url": url}


s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})

# --- NBS portal: plain vs warmed-up session with referer
q = {"m": "QueryData", "dbcode": "hgyd", "rowcode": "zb", "colcode": "sj", "wds": "[]",
     "dfwds": json.dumps([{"wdcode": "zb", "valuecode": "A0701"}, {"wdcode": "sj", "valuecode": "LAST13"}])}
get(requests.Session(), "nbs_query_plain", "https://data.stats.gov.cn/easyquery.htm", "json", params=q)
get(s, "nbs_landing", "https://data.stats.gov.cn/easyquery.htm?cn=A01")
hdr = {"Referer": "https://data.stats.gov.cn/easyquery.htm?cn=A01", "X-Requested-With": "XMLHttpRequest",
       "Accept": "application/json, text/javascript, */*; q=0.01"}
q2 = dict(q, k1=str(int(time.time() * 1000)), h="1")
get(s, "nbs_query_warm", "https://data.stats.gov.cn/easyquery.htm", "json", params=q2, headers=hdr)
post(s, "nbs_tree_warm", "https://data.stats.gov.cn/easyquery.htm",
     data={"id": "zb", "dbcode": "hgyd", "wdcode": "zb", "m": "getTree"}, headers=hdr)
get(s, "nbs_new_api", "https://data.stats.gov.cn/dg/website/publicrelease/web/external/getEsDataByCidAndDt", "json")

# --- NBS release list depth
for n in (50, 150, 300, 500):
    get(s, f"nbs_list_{n}", f"https://www.stats.gov.cn/sj/zxfb/index_{n}.html")
get(s, "nbs_sjjd", "https://www.stats.gov.cn/sj/sjjd/")
get(s, "nbs_sjjd_100", "https://www.stats.gov.cn/sj/sjjd/index_100.html")

# --- PBoC pagination + statistics section
pb = requests.Session(); pb.headers.update({"User-Agent": UA})
get(pb, "pboc_news_p2", "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/11040-2.html")
get(pb, "pboc_news_p200", "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/11040-200.html")
get(pb, "pboc_stats_index", "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html")
get(pb, "pboc_stats_2025", "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/2025nian/index.html")

# --- NDRC docx attachment + list depth
nd = requests.Session(); nd.headers.update({"User-Agent": UA})
get(nd, "ndrc_docx", "https://www.ndrc.gov.cn/xwdt/xwfb/202105/W020210518518450177907.docx", "bin")
get(nd, "ndrc_list_p20", "https://www.ndrc.gov.cn/xwdt/xwfb/index_20.html")

# --- MOF older list pages + CGB issuance section
mf = requests.Session(); mf.headers.update({"User-Agent": UA})
for n in (4, 8, 15):
    get(mf, f"mof_lgb_list_{n}", f"https://yss.mof.gov.cn/zhuantilanmu/dfzgl/sjtj/index_{n}.htm")
get(mf, "mof_gks_gzfx", "https://gks.mof.gov.cn/ztztz/guozaiguanli/gzfxdzs/")
get(mf, "mof_gks", "https://gks.mof.gov.cn/")

# --- CELMA / ChinaBond landing
get(requests.Session(), "celma", "https://www.celma.org.cn/")
get(requests.Session(), "chinabond", "https://www.chinabond.com.cn/")

(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
