"""Daily history from Deribit's public API: each market's price index and a 30-day implied-vol index.

    BTC, ETH            btc_usd / eth_usd index (since 2016 / 2019) and their own DVOL
    <COIN>_USDC         <coin>_usdc index (most since Feb 2022; HYPE only since Jun 2026)
                        vol: the coin's own DVOL for BTC_USDC and ETH_USDC; for every other coin
                        Deribit publishes no vol index, so BTC's DVOL stands in (a stated proxy)

No C++ or kdb+ dependencies, so the dashboard and its public demo can use it too.
Cached to CSVs, so every result built on it can be reproduced.
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


def index_name(asset: str) -> str:
    """The Deribit price index a market's options settle on: BTC -> btc_usd, SOL_USDC -> sol_usdc."""
    coin, _, quote = asset.partition("_")
    return f"{coin.lower()}_{quote.lower() or 'usd'}"


DVOL_COINS = ("BTC", "ETH")                    # the only coins with a Deribit vol index (DVOL)


def vol_source(asset: str) -> tuple[str, bool]:
    """(currency whose DVOL is used, is_proxy). Own DVOL for BTC, ETH, BTC_USDC, ETH_USDC;
    BTC's DVOL as a proxy for every other coin."""
    coin = asset.split("_")[0]
    return (coin, False) if coin in DVOL_COINS else ("BTC", True)


def var_source(asset: str) -> str:
    """How a coin's VaR history is labelled in the vares table."""
    cur, proxy = vol_source(asset)
    return f"deribit index+{cur} DVOL" + (" (proxy)" if proxy else "")


def fetch_index(name: str) -> pd.DataFrame:
    """Daily (ms, spot) at 00:00 UTC from Deribit's index chart (6-hourly points)."""
    idx = _get("get_index_chart_data", index_name=name, range="all")
    s = pd.DataFrame(idx, columns=["ms", "spot"])
    return s[s["ms"] % DAY_MS == 0].reset_index(drop=True)


def fetch_dvol(currency: str, end_ms: int, years: float = 5) -> pd.DataFrame:
    """Daily (ms, dvol): DVOL candles are stamped at the day's START; their close is the level
    at the next 00:00, so each is re-stamped to that 00:00."""
    end, start = int(end_ms), int(end_ms - years * 365 * DAY_MS)
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
    return v[["ms", "dvol"]]


def _frame(s: pd.DataFrame, v: pd.DataFrame) -> pd.DataFrame:
    df = s.merge(v, on="ms").sort_values("ms")
    df["date"] = pd.to_datetime(df["ms"], unit="ms", utc=True).dt.tz_localize(None)
    return df[["date", "spot", "dvol"]].reset_index(drop=True)


def fetch_history(currency: str = "BTC", cache: Path | None = None, refresh: bool = False) -> pd.DataFrame:
    """Daily (date, spot, dvol), both as of 00:00 UTC on `date`. Cached to `cache` if given."""
    if cache is not None and Path(cache).exists() and not refresh:
        return pd.read_csv(cache, parse_dates=["date"])
    s = fetch_index(f"{currency.lower()}_usd")
    df = _frame(s, fetch_dvol(currency, int(s["ms"].max())))
    if cache is not None:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache, index=False)
    return df


def fetch_histories(assets, cache: Path | None = None, refresh: bool = False) -> dict[str, pd.DataFrame]:
    """asset -> daily (date, spot, dvol) for each market, with ``attrs['vol_proxy']`` saying whether
    dvol is the coin's own index or BTC's stand-in. One DVOL download per vol currency.
    ``cache``: one long CSV (date, asset, spot, dvol, proxy), so a whole run is reproducible."""
    assets = list(dict.fromkeys(assets))
    if cache is not None and Path(cache).exists() and not refresh:
        long = pd.read_csv(cache, parse_dates=["date"])
        if set(assets) <= set(long["asset"]):
            return _split(long, assets)
    idx = {a: fetch_index(index_name(a)) for a in assets}
    end = max(int(s["ms"].max()) for s in idx.values())
    vols = {c: fetch_dvol(c, end) for c in {vol_source(a)[0] for a in assets}}
    parts = []
    for a in assets:
        cur, proxy = vol_source(a)
        parts.append(_frame(idx[a], vols[cur]).assign(asset=a, proxy=proxy))
    long = pd.concat(parts, ignore_index=True)[["date", "asset", "spot", "dvol", "proxy"]]
    if cache is not None:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        long.to_csv(cache, index=False)
    return _split(long, assets)


def _split(long: pd.DataFrame, assets) -> dict[str, pd.DataFrame]:
    out = {}
    for a in assets:
        h = long[long["asset"] == a].sort_values("date")
        df = h[["date", "spot", "dvol"]].reset_index(drop=True)
        df.attrs["vol_proxy"] = bool(h["proxy"].iloc[0]) if len(h) else False
        out[a] = df
    return out
