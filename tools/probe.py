"""Endpoint probe (round 3): PBoC TSF / money supply / RMB credit table samples.
Development aid only; nothing here feeds the store."""
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

OUT = Path("probe")
OUT.mkdir(exist_ok=True)
for f in OUT.glob("*"):
    f.unlink()
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"})
summary = {}


def get(name, url, ext="html"):
    try:
        r = S.get(url, timeout=60)
        (OUT / f"{name}.{ext}").write_bytes(r.content[:4_000_000])
        summary[name] = {"status": r.status_code, "bytes": len(r.content), "url": r.url}
        return r
    except Exception as e:
        summary[name] = {"error": repr(e)[:300], "url": url}


def rows_with_htm(r):
    s = BeautifulSoup(r.content.decode("utf-8", "replace"), "lxml")
    out = []
    for tr in s.find_all("tr"):
        a = tr.find("a", href=re.compile(r"\.htm$"))
        if a and not tr.find("tr"):
            out.append((re.sub(r"\s+", " ", tr.get_text(" ", strip=True))[:80], urljoin(r.url, a["href"])))
    return out


base = "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/"
cats = {"tsf_2025": "5570903/5570885/", "tsf_2016": "3013637/3013639/", "tsf_2026": "2026ntjsj/shrzgm/",
        "money_2025": "5570903/5570886/", "credit_2025": "5570903/5570888/", "credit_2016": "3013637/3013643/"}
for name, sub in cats.items():
    r = get(f"pboc_{name}", base + sub + "index.html")
    if r is None or r.status_code != 200:
        continue
    rows = rows_with_htm(r)
    summary[f"pboc_{name}_rows"] = rows
    for i, (label, u) in enumerate(rows):
        if name.startswith("tsf") or re.search(r"货币供应量|^金融机构人民币信贷收支表", label):
            get(f"pboc_{name}_t{i}", u, "htm")

(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False)[:2000])
