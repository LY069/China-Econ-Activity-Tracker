"""HTTP with raw-response archiving. Every fetched byte is saved before parsing.

Raw files are content-addressed and gzip-compressed:
data/raw/<source>/<request-hash>/<content-hash>.<ext>.gz (read with read_raw()),
with a .meta.json beside each (url, params, status, first retrieved_at). An unchanged
response re-uses the existing file untouched, so scheduled refreshes do not change the
repository unless the source actually changed. Cache-busting params (k1, t, _) are left
out of the request hash. Store rows reference the raw file, so any
dashboard number can be traced to the exact response it was parsed from.

Replay (CETRACKER_REPLAY=1, `python -m cetracker.build --replay`): no network; each request is
served from the most recently first-retrieved archived response for its request hash, so the
whole store can be rebuilt from data/raw alone (used to re-parse after a parser fix).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


NOCACHE_PARAMS = {"k1", "t", "_"}


class FetchError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if ca:
        s.verify = ca
    return s


SESSION = _session()


def fetch(source: str, url: str, params: dict | None = None, data: dict | None = None,
          method: str = "GET", ext: str = "bin", headers: dict | None = None,
          retries: int = 4, timeout: int = 40, encoding: str | None = None) -> tuple[bytes, str, str]:
    """Fetch and archive. Returns (content, raw_file relative path, retrieved_at)."""
    stable = {k: v for k, v in (params or {}).items() if k not in NOCACHE_PARAMS}
    key = json.dumps([url, stable, data, method], sort_keys=True, ensure_ascii=False)
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    d = RAW / source / h
    if os.environ.get("CETRACKER_REPLAY") == "1":
        return _replay(source, url, d)
    last = None
    for i in range(retries):
        try:
            r = SESSION.request(method, url, params=params, data=data, headers=headers, timeout=timeout)
            if r.status_code == 200 and r.content:
                ts = now_utc()
                ch = hashlib.sha1(r.content).hexdigest()[:16]
                d.mkdir(parents=True, exist_ok=True)
                path = d / f"{ch}.{ext}.gz"
                meta_p = d / f"{ch}.meta.json"
                if path.exists() and meta_p.exists():
                    meta = json.loads(meta_p.read_text())
                else:
                    path.write_bytes(gzip.compress(r.content, mtime=0))
                    meta = {"url": r.url, "request_url": url, "params": params, "data": data, "method": method,
                            "status": r.status_code, "first_retrieved_at": ts,
                            "bytes": len(r.content), "content_type": r.headers.get("Content-Type", "")}
                    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
                return r.content, str(path.relative_to(ROOT)), meta["first_retrieved_at"]
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = repr(e)[:300]
        time.sleep(2 ** (i + 1))
    raise FetchError(f"{source}: {url} failed after {retries} tries: {last}")


def _replay(source: str, url: str, d: Path) -> tuple[bytes, str, str]:
    found = []
    for m in (d.glob("*.meta.json") if d.exists() else []):
        raw = sorted(d.glob(m.name.replace(".meta.json", ".*.gz")))
        if raw:
            found.append((json.loads(m.read_text())["first_retrieved_at"], raw[0]))
    if not found:
        raise FetchError(f"{source}: {url} not in raw archive (replay mode)")
    ts, path = max(found)
    return read_raw(path), str(path.relative_to(ROOT)), ts


def reachable(url: str, timeout: int = 10) -> tuple[bool, str]:
    try:
        r = SESSION.get(url, timeout=timeout)
        return r.status_code < 500, f"HTTP {r.status_code}"
    except requests.RequestException as e:
        return False, repr(e)[:200]


def read_raw(path: str | Path) -> bytes:
    """Bytes of an archived raw response (transparently handles .gz)."""
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    b = p.read_bytes()
    return gzip.decompress(b) if p.suffix == ".gz" else b
