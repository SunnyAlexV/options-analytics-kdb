"""A "bundle": a day of every table the live system produces, saved as Parquet files.

The public demo replays a bundle through the same dashboard that runs on the live system,
so people without kdb+ can see it working. A bundle can be made two ways:

  from_gateway(port, date)     pull the tables from our kdb+ system: exactly what ran live
  from_recording(folder)       rebuild them offline from a feed recording: the surface engine
                               and the risk calculations run on a simulated clock, the same
                               code the live processes use

Every table gets an absolute ``time`` column (UTC, datetime64[ns]) as its first column,
standing in for the tickerplant's time stamp. The busy tables (quote, iv, snap) keep the last
row per instrument per ``bucket_s`` seconds, which keeps a bundle small enough to host.
"""
from __future__ import annotations

import time as _time
from pathlib import Path

import numpy as np
import pandas as pd

from feed.schema import FEED_SCHEMAS, columns

MARKET = ("quote", "trade", "spot", "snap", "ref", "dq", "gap")
ENGINE = ("iv", "fwd", "surface")
RISK = ("pos", "risk", "scen", "pnl", "vares")
TABLES = MARKET + ENGINE + RISK
HEAVY = ("quote", "iv", "snap", "fwd", "surface")   # downsampled: last row per sym per bucket
NS = 1_000_000_000


def _frame(table: str, rows: list[dict], times) -> pd.DataFrame:
    """Rows -> DataFrame in schema order, with ``time`` (datetime64[ns]) first."""
    cols = columns(table)
    df = pd.DataFrame([{c: r.get(c) for c in cols} for r in rows], columns=cols)
    for c, t in FEED_SCHEMAS[table]:
        if t == "p":                        # int ns -> datetime64 exactly (no float round trip)
            s = pd.to_numeric(df[c], errors="coerce")
            arr = s.fillna(0).astype("int64").to_numpy().view("datetime64[ns]").copy()
            arr[s.isna().to_numpy()] = np.datetime64("NaT", "ns")
            df[c] = arr
        elif t == "f":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
        elif t == "s":
            df[c] = df[c].fillna("").astype(str)
    df.insert(0, "time", pd.to_datetime(np.asarray(times, dtype="int64"), unit="ns"))
    return df.sort_values("time", kind="stable").reset_index(drop=True)


def downsample(df: pd.DataFrame, bucket_s: float) -> pd.DataFrame:
    """Last row per (sym, time bucket)."""
    if df.empty:
        return df
    b = df["time"].dt.floor(f"{int(bucket_s)}s")
    return df.groupby([df["sym"], b], sort=False).tail(1).sort_values("time").reset_index(drop=True)


# ------------------------------------------------------------------ from a feed recording
def from_recording(folder, bucket_s: float = 30, risk_every_s: float = 30, R: float = 0.0,
                   history_cache=None, hist=None, log=print) -> dict[str, pd.DataFrame]:
    """folder: a feed recording, or a list of them (one per feed group: BTC, ETH, USDC).
    hist: daily index/DVOL history for VaR; if None it is fetched (cached to history_cache)."""
    from engine.replay import _read_jsonl, replay

    folders = [folder] if isinstance(folder, (str, Path)) else list(folder)   # one per feed group
    days = [d for f in folders for d in sorted(Path(f).expanduser().glob("[0-9]*"))]
    raw = {t: [] for t in MARKET}
    for day in days:
        for t in MARKET:
            raw[t] += _read_jsonl(day / f"{t}.jsonl.gz")
    t0 = min(r["recv"] for r in raw["quote"])
    n_quotes, span = len(raw["quote"]), (max(r["recv"] for r in raw["quote"]) - t0) / 60e9
    bucket_ns = int(bucket_s * NS)

    def thin(rows, ts):
        """Last row per (sym, bucket), as downsample() would keep, without building the big
        frame first: a 25-minute recording of all nine coins is ~2.4M quotes and ~2.4M IVs."""
        last = {}
        for r, t in zip(rows, ts):
            last[(r["sym"], t // bucket_ns)] = (r, t)
        kept = sorted(last.values(), key=lambda x: x[1])
        return [r for r, _ in kept], [t for _, t in kept]

    # surface engine on a simulated clock (engine/replay.py), exactly as in the harness.
    # Implied vols are thinned as they arrive (they are the bulk); forwards and smiles are
    # kept whole because the risk replay below steps through them.
    iv_last: dict = {}
    eng_rows = {"fwd": [], "surface": []}
    eng_t = {"fwd": [], "surface": []}
    n_iv = 0

    def on_output(now, ivs, fw, sf, eng):
        nonlocal n_iv
        n_iv += len(ivs)
        for r in ivs:
            iv_last[(r["sym"], now // bucket_ns)] = (r, now)
        for t, rows in (("fwd", fw), ("surface", sf)):
            eng_rows[t] += rows
            eng_t[t] += [now] * len(rows)

    tic = _time.time()
    replay({"ref": raw["ref"], "snap": raw["snap"], "quote": raw["quote"]}, on_output=on_output)
    kept = sorted(iv_last.values(), key=lambda x: x[1])
    out = {"iv": _frame("iv", [r for r, _ in kept], [t for _, t in kept])}
    del iv_last, kept
    for t in ("fwd", "surface"):
        out[t] = _frame(t, eng_rows[t], eng_t[t])
    log(f"surface engine: {n_iv:,} implied vols, {len(eng_rows['surface']):,} smiles "
        f"({_time.time() - tic:.0f} s)")

    for t in ("quote", "snap"):                                # the busy market tables
        raw[t] = thin(raw[t], [r["recv"] for r in raw[t]])
        out[t] = _frame(t, *raw[t])
        raw[t] = None
    for t in ("trade", "spot"):
        out[t] = _frame(t, raw[t], [r["recv"] for r in raw[t]])
    out["ref"] = _frame("ref", raw["ref"], [t0] * len(raw["ref"]))
    out["dq"] = _frame("dq", raw["dq"], [r["minute"] + 60 * NS for r in raw["dq"]])
    out["gap"] = _frame("gap", raw["gap"], [r["end"] for r in raw["gap"]])
    log(f"market data: {n_quotes:,} quotes, {len(raw['trade'])} trades over {span:.0f} minutes")

    surf = [dict(r, now=n) for r, n in zip(eng_rows["surface"], eng_t["surface"])]
    if hist is None:
        try:
            from risk.history import fetch_history
            hist = fetch_history("BTC", cache=history_cache)
        except Exception as e:
            log(f"history unavailable ({type(e).__name__}); no VaR in this bundle")
    out.update(_risk_offline(surf, raw["spot"], raw["ref"], risk_every_s, R, hist, log))
    for t in HEAVY:
        out[t] = downsample(out[t], bucket_s)
    return out


def _risk_offline(surf, spot_rows, ref_rows, every_s, R, hist, log, asset="BTC"):
    """The risk process's outputs on a simulated clock (same functions as risk/__main__.py).
    Like the live risk process, it books one asset (BTC); a multi-coin recording's other
    coins are filtered out here."""
    from risk import rows as RR
    from risk import var as varmod
    from risk.book import sample_book
    from risk.core import aggregate, pnl_explain, pnl_rows, position_greeks, scenario_grid
    from risk.market import Market

    mine = lambda r: (r.get("asset") or "BTC") == asset            # noqa: E731
    surf = [r for r in surf if mine(r)]
    spot_rows = [r for r in spot_rows if mine(r)]
    listed = {}
    for r in ref_rows:
        if r.get("kind") == "option" and mine(r):
            listed.setdefault("-".join(r["sym"].split("-")[:2]), []).append(r["strike"])
    spot_t = np.array([r["recv"] for r in spot_rows], dtype=np.int64)
    spot_p = np.array([r["price"] for r in spot_rows], dtype=float)
    surf = sorted(surf, key=lambda r: r["now"])
    rows = {t: [] for t in RISK}
    times = {t: [] for t in RISK}

    def add(t, rs, now):
        rows[t] += rs
        times[t] += [now] * len(rs)

    latest, i, book, prev = {}, 0, None, None
    first = surf[0]["now"] if surf else 0
    t, end = first + int(every_s * NS), surf[-1]["now"] if surf else 0
    last_scen = last_var = -1e30
    while t <= end:
        while i < len(surf) and surf[i]["now"] <= t:
            latest[surf[i]["sym"]] = surf[i]
            i += 1
        k = np.searchsorted(spot_t, t, side="right") - 1
        m = Market.from_surface(latest.values(), asof=t, spot=float(spot_p[k]) if k >= 0 else None)
        if not m.labels:
            t += int(every_s * NS)
            continue
        if book is None:
            if (m.T() * 365).max() < 80 and t - first < 60 * NS:
                t += int(every_s * NS)
                continue
            book = sample_book(m, listed, hedge_rule_R=R)
            add("pos", RR.pos_rows(book, asset), t)
            prev = m
        add("risk", RR.risk_rows(aggregate(position_greeks(book, m, R=R)), asset), t)
        if t - last_scen >= 60 * NS:
            add("scen", RR.scen_rows(scenario_grid(book, m, R=R), "sample", asset), t)
            ex = pnl_explain(book, prev, m, R=R)
            if not ex.empty and t > prev.asof:
                add("pnl", RR.pnl_q_rows(pnl_rows(ex), asset), t)
            prev, last_scen = m, t
        if hist is not None and t - last_var >= 900 * NS:
            res = varmod.compute(book, m, hist, R=R)
            bt = varmod.backtest(book, m, hist, R=R)
            add("vares", RR.vares_rows(res, bt, "sample", asset, R), t)
            last_var = t
        t += int(every_s * NS)
    log(f"risk: {len(rows['risk']):,} risk rows, {len(rows['pnl'])} P&L intervals, "
        f"{len(rows['vares']) // 2} VaR runs")
    return {tb: _frame(tb, rows[tb], times[tb]) for tb in RISK}


# ------------------------------------------------------------------ from kdb+ (the live system)
def from_gateway(port: int, date: str, bucket_s: float = 30, log=print) -> dict[str, pd.DataFrame]:
    """Pull one date's tables from the gateway (RDB if it is today, else HDB).
    date as 'YYYY.MM.DD'. Queries run in a separate process (dashboard.sources.KdbWorker)."""
    from .sources import KdbWorker

    today = _time.strftime("%Y.%m.%d", _time.gmtime())
    fn, where = (".gw.rdbq", "") if date == today else (".gw.hdbq", f" where date={date}")
    jobs = []
    for t in TABLES:
        # busy tables: last row per sym per bucket (0D00:00:01*30 is the timespan 30 s)
        sel = (f"0!select by sym, (0D00:00:01*{int(bucket_s)}) xbar time from {t}" if t in HEAVY
               else f"select from {t}")
        jobs.append((t, fn, sel + where, date))
    w = KdbWorker(port, timeout=600.0)
    try:
        res = w.run(jobs)
    finally:
        w.close()
    if res.get("_errors"):
        raise RuntimeError("; ".join(res["_errors"]))
    out = {}
    for t in TABLES:
        df = res[t]
        out[t] = df.drop(columns=[c for c in ("date",) if c in df.columns])
        log(f"{t}: {len(df):,} rows")
    return out


# ------------------------------------------------------------------ files
def save(tables: dict[str, pd.DataFrame], folder) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for t, df in tables.items():
        df.to_parquet(folder / f"{t}.parquet", index=False)


def load(folder) -> dict[str, pd.DataFrame]:
    folder = Path(folder)
    return {t: pd.read_parquet(folder / f"{t}.parquet") for t in TABLES if (folder / f"{t}.parquet").exists()}
