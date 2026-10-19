"""Observation store: one tidy long table, every row traceable to a raw file.

Schema (one row = one value of one series for one period, as known at one vintage):

    series_id      registry key, e.g. "nbs.retail.ytd_level"
    period         period end as ISO date string (month-end / quarter-end)
    value          float
    vintage_date   date the value became public (release date) or, failing that,
                   the retrieval date; used to rebuild real-time datasets
    source_tier    "primary" | "fallback" | "derived" | "manual"
    source         short source name ("NBS", "PBoC", "MOF", "ChinaBond", ...)
    source_url     URL the raw file was fetched from
    raw_file       path (relative to repo) of the archived raw response
    retrieved_at   UTC timestamp of the fetch
    lineage        for derived rows: transform name + input series_ids; else ""
    flags          comma-separated quality flags ("janfeb_combined", "break:M1_2025", ...)

Values are never overwritten: a new vintage appends rows. `latest()` returns the
most recent vintage per (series_id, period); `as_of(date)` returns what was known
on a given date, which is what the pseudo real-time nowcast evaluation uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = ROOT / "data" / "store"
OBS_FILE = STORE_DIR / "observations.parquet"

COLUMNS = [
    "series_id", "period", "value", "vintage_date", "source_tier", "source",
    "source_url", "raw_file", "retrieved_at", "lineage", "flags",
]
SOURCE_TIERS = {"primary", "fallback", "derived", "manual"}
KEY = ["series_id", "period", "vintage_date"]


@dataclass
class Provenance:
    source: str
    source_tier: str
    source_url: str = ""
    raw_file: str = ""
    retrieved_at: str = ""
    vintage_date: str = ""
    lineage: str = ""

    def __post_init__(self):
        if self.source_tier not in SOURCE_TIERS:
            raise ValueError(f"bad source_tier {self.source_tier!r}")


def make_rows(series_id: str, s: pd.Series, prov: Provenance, flags: pd.Series | dict | str = "") -> pd.DataFrame:
    """Turn a period-indexed Series into store rows with provenance attached."""
    s = s.dropna()
    df = pd.DataFrame({"period": pd.to_datetime(s.index).strftime("%Y-%m-%d"), "value": s.astype(float).values})
    df["series_id"] = series_id
    for k in ("vintage_date", "source_tier", "source", "source_url", "raw_file", "retrieved_at", "lineage"):
        df[k] = getattr(prov, k)
    if isinstance(flags, str):
        df["flags"] = flags
    else:
        fl = pd.Series(flags)
        fl.index = pd.to_datetime(fl.index).strftime("%Y-%m-%d")
        df["flags"] = df["period"].map(fl).fillna("")
    return df[COLUMNS]


def validate(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"missing columns {missing}")
    bad = df[~df["source_tier"].isin(SOURCE_TIERS)]
    if len(bad):
        raise ValueError(f"bad source_tier rows: {bad.head()}")
    if df["value"].isna().any():
        raise ValueError("NaN values must not be stored")
    fetched = df[df["source_tier"].isin(["primary", "fallback"])]
    no_raw = fetched[fetched["raw_file"] == ""]
    if len(no_raw):
        raise ValueError(f"fetched rows without raw_file: {no_raw['series_id'].unique()[:5]}")
    manual = df[df["source_tier"] == "manual"]
    if (manual["source_url"] == "").any():
        raise ValueError("manual rows must cite a source_url")
    derived = df[df["source_tier"] == "derived"]
    if (derived["lineage"] == "").any():
        raise ValueError("derived rows must carry lineage")
    return df


def load(path: Path = OBS_FILE) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_parquet(path)


def append(new: pd.DataFrame, path: Path = OBS_FILE) -> pd.DataFrame:
    """Append rows as new vintages only where they add information.

    A row is kept if its (series_id, period) is new, or its value differs from the latest
    stored vintage (a revision). Re-fetching unchanged history therefore adds nothing,
    and the store's vintages are genuine first-publication / revision dates. An identical
    (series_id, period, vintage_date) key is replaced, never duplicated.
    """
    validate(new)
    old = load(path)
    if len(old):
        prev = latest(old)[["series_id", "period", "value"]].rename(columns={"value": "_prev"})
        m = new.merge(prev, on=["series_id", "period"], how="left")
        changed = m["_prev"].isna() | ((m["value"] - m["_prev"]).abs() > 1e-9 * m["_prev"].abs().clip(lower=1))
        new = new[changed.values]
    allrows = pd.concat([old, new], ignore_index=True) if len(old) else new.copy()
    allrows = allrows.drop_duplicates(subset=KEY, keep="last").sort_values(KEY).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    allrows.to_parquet(path, index=False)
    return allrows


def replace_derived(new: pd.DataFrame, path: Path = OBS_FILE) -> pd.DataFrame:
    """Derived rows are recomputed on every build: drop all old derived rows, write the new set."""
    validate(new)
    old = load(path)
    keep = old[old["source_tier"] != "derived"] if len(old) else old
    allrows = pd.concat([keep, new], ignore_index=True)
    allrows = allrows.drop_duplicates(subset=KEY, keep="last").sort_values(KEY).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    allrows.to_parquet(path, index=False)
    return allrows


def latest(df: pd.DataFrame) -> pd.DataFrame:
    """Most recent vintage per (series_id, period); primary beats fallback at equal vintage."""
    if df.empty:
        return df
    rank = df["source_tier"].map({"primary": 0, "derived": 1, "manual": 2, "fallback": 3})
    d = df.assign(_r=rank).sort_values(["series_id", "period", "vintage_date", "_r"], ascending=[True, True, True, False])
    return d.drop_duplicates(["series_id", "period"], keep="last").drop(columns="_r")


def as_of(df: pd.DataFrame, date: str) -> pd.DataFrame:
    return latest(df[df["vintage_date"] <= date])


def series(df: pd.DataFrame, series_id: str) -> pd.Series:
    """Latest-vintage values of one series as a float Series indexed by Timestamp."""
    d = df[df["series_id"] == series_id]
    if d.empty:
        return pd.Series(dtype=float, name=series_id)
    d = latest(d)
    s = pd.Series(d["value"].values, index=pd.to_datetime(d["period"]), name=series_id).sort_index()
    return s.astype(float)


def duckdb_view(path: Path = OBS_FILE):
    """Open an in-memory DuckDB connection with `obs` and `obs_latest` views over the store."""
    import duckdb

    con = duckdb.connect()
    con.execute(f"CREATE VIEW obs AS SELECT * FROM read_parquet('{path.as_posix()}')")
    con.execute("""
        CREATE VIEW obs_latest AS
        SELECT * EXCLUDE (rn) FROM (
          SELECT *, row_number() OVER (PARTITION BY series_id, period ORDER BY vintage_date DESC) rn FROM obs
        ) WHERE rn = 1
    """)
    return con
