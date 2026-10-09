"""Daily BTC history from Deribit's public API: the btc_usd index and DVOL (30-day implied vol).

No C++ or kdb+ dependencies, so the dashboard and its public demo can use it too.
Cached to a CSV, so every result built on it can be reproduced.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pandas as pd

API = "https://www.deribit.com/api/v2/public/"
DAY_MS = 86_400_000


def _get(method: str, **params) -> dict:
    url = API + method + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())["result"]


def fetch_history(currency: str = "BTC", cache: Path | None = None, refresh: bool = False) -> pd.DataFrame:
    """Daily (date, spot, dvol), both as of 00:00 UTC on `date`. Cached to `cache` if given."""
    if cache is not None and Path(cache).exists() and not refresh:
        return pd.read_csv(cache, parse_dates=["date"])
    idx = _get("get_index_chart_data", index_name=f"{currency.lower()}_usd", range="all")
    s = pd.DataFrame(idx, columns=["ms", "spot"])
    s = s[s["ms"] % DAY_MS == 0]                                   # the 00:00 UTC points
    # DVOL candles are stamped at the day's START; their close is the level at the next 00:00
    end = int(s["ms"].max())
    start = end - 5 * 365 * DAY_MS
    rows = []
    while True:
        r = _get("get_volatility_index_data", currency=currency, start_timestamp=start,
                 end_timestamp=end, resolution="1D")
        rows += r["data"]
        if not r.get("continuation"):
            break
        end = int(r["continuation"])
    v = pd.DataFrame(rows, columns=["ms", "o", "h", "l", "dvol"]).drop_duplicates("ms")
    v["ms"] = v["ms"] + DAY_MS                                     # close of day d -> 00:00 of d+1
    df = s.merge(v[["ms", "dvol"]], on="ms").sort_values("ms")
    df["date"] = pd.to_datetime(df["ms"], unit="ms", utc=True).dt.tz_localize(None)
    df = df[["date", "spot", "dvol"]].reset_index(drop=True)
    if cache is not None:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache, index=False)
    return df
