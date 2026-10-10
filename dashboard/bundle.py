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
RISK = ("pos", "risk", "scen", "pnl", "vares", "port")
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
    assets = sorted({r.get("asset") or "BTC" for r in surf})
    if hist is None or isinstance(hist, pd.DataFrame):
        try:
            from risk.history import fetch_histories
            hist = fetch_histories(assets, cache=history_cache)
        except Exception as e:
            log(f"history unavailable ({type(e).__name__}); "
                + ("BTC-only VaR from the given history" if hist is not None else "no VaR in this bundle"))
            hist = {"BTC": hist} if isinstance(hist, pd.DataFrame) else None
    out.update(_risk_offline(surf, raw["spot"], raw["ref"], risk_every_s, R, hist, log))
    for t in HEAVY:
        out[t] = downsample(out[t], bucket_s)
    return out


def _risk_offline(surf, spot_rows, ref_rows, every_s, R, hist, log, var_every_s=900.0):
    """The risk process's outputs on a simulated clock, with the same functions and the same
    rules as risk/__main__.py: a sample book per coin (sized to the BTC book's USD notional),
    Greeks, scenarios, P&L explain, each coin's own VaR and the portfolio VaR across coins.
    ``hist``: coin -> daily history (risk.history.fetch_histories), or None for no VaR."""
    from risk import portfolio as PF
    from risk import rows as RR
    from risk import var as varmod
    from risk.book import sample_book
    from risk.core import aggregate, pnl_explain, pnl_rows, position_greeks, scenario_grid
    from risk.history import var_source, vol_source
    from risk.market import Market

    asset_of = lambda r: r.get("asset") or "BTC"                    # noqa: E731
    assets = sorted({asset_of(r) for r in surf})
    listed, listed_exp, spots = {a: {} for a in assets}, {a: {} for a in assets}, {}
    for r in ref_rows:
        a = asset_of(r)
        if r.get("kind") == "option" and a in listed:
            lab = "-".join(r["sym"].split("-")[:2])
            listed[a].setdefault(lab, []).append(r["strike"])
            listed_exp[a][lab] = int(r["expiry"])
    for a in assets:
        sr = [r for r in spot_rows if asset_of(r) == a]
        spots[a] = (np.array([r["recv"] for r in sr], dtype=np.int64), np.array([r["price"] for r in sr], dtype=float))

    def spot_at(a, t):
        st, sp = spots[a]
        k = np.searchsorted(st, t, side="right") - 1
        return float(sp[k]) if k >= 0 else None

    surf = sorted(surf, key=lambda r: r["now"])
    rows = {t: [] for t in RISK}
    times = {t: [] for t in RISK}

    def add(t, rs, now):
        rows[t] += rs
        times[t] += [now] * len(rs)

    latest = {a: {} for a in assets}
    books, prev, first = {}, {}, {}
    i = 0
    start = surf[0]["now"] if surf else 0
    t, end = start + int(every_s * NS), surf[-1]["now"] if surf else 0
    last_scen = last_var = -1e30
    n_var = 0
    while t <= end:
        while i < len(surf) and surf[i]["now"] <= t:
            latest[asset_of(surf[i])][surf[i]["sym"]] = surf[i]
            i += 1
        mk = {}
        for a in assets:
            m = Market.from_surface(latest[a].values(), asof=t, spot=spot_at(a, t))
            if not m.labels:
                continue
            if a not in books:
                first.setdefault(a, t)
                days = [(e - t) / (86400 * NS) for e in listed_exp[a].values()]
                want = min(80.0, 0.95 * max([d for d in days if d >= 2] or [80.0 / 0.95]))
                if (m.T() * 365).max() < want and t - first[a] < 60 * NS:
                    continue
                ref_spot = next((spot_at(b, t) for b in ("BTC", "BTC_USDC") if b in spots and spot_at(b, t)),
                                float("nan"))
                s_a = spot_at(a, t) or float(m.F[0])
                books[a] = sample_book(m, listed[a], hedge_rule_R=R, scale=PF.scale_for(a, s_a, ref_spot))
                add("pos", RR.pos_rows(books[a], a), t)
                prev[a] = m
            mk[a] = m
        for a, m in mk.items():
            add("risk", RR.risk_rows(aggregate(position_greeks(books[a], m, R=R)), a), t)
        if mk and t - last_scen >= 60 * NS:
            for a, m in mk.items():
                add("scen", RR.scen_rows(scenario_grid(books[a], m, R=R), "sample", a), t)
                ex = pnl_explain(books[a], prev[a], m, R=R)
                if not ex.empty and t > prev[a].asof:
                    add("pnl", RR.pnl_q_rows(pnl_rows(ex), a), t)
                prev[a] = m
            last_scen = t
        if hist and mk and t - last_var >= var_every_s * NS:
            vm = {a: m for a, m in mk.items() if a in hist}
            for a, m in vm.items():
                res = varmod.compute(books[a], m, hist[a], R=R, source=var_source(a))
                bt = (varmod.backtest(books[a], m, hist[a], R=R) if len(hist[a]) - 1 > 365 else
                      {k: {"exceptions": 0, "days": 0, "kupiec_p": float("nan")} for k in ("hs", "fhs")})
                add("vares", RR.vares_rows(res, bt, "sample", a, R), t)
            if vm:
                rep = PF.compute({a: books[a] for a in vm}, vm, hist, R=R,
                                 proxied=[a for a in vm if vol_source(a)[1]])
                pbt = PF.backtest({a: books[a] for a in vm}, vm, hist, R, rep["main"]) if rep["main"] else {}
                add("port", RR.port_rows(rep, pbt), t)
                n_var += 1
            last_var = t
        t += int(every_s * NS)
    log(f"risk: books for {len(books)} coins, {len(rows['risk']):,} risk rows, {len(rows['pnl'])} P&L intervals, "
        f"{n_var} VaR runs")
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
