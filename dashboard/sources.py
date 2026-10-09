"""Where the dashboard's data comes from: the live kdb+ gateway, or a replayed bundle.

Every panel reads a few "views". Each view is defined twice, side by side, with the same
meaning: a q query that the gateway runs on the RDB (the live system), and a pandas function
on a bundle's tables up to a moment in time (the public demo, dashboard/bundle.py).

Queries that summarise big tables (a median per minute, the last row per instrument) run
inside q, next to the data; only small results travel to the dashboard. That is the standard
kdb+ client pattern.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd

MIN = "1min"
LATEST = ("quote", "iv", "snap", "fwd", "surface", "ref")

# ---------------------------------------------------------------------- views
# name -> q query on the RDB. "time" in the RDB is the tickerplant's time of day (a timespan).
Q = {
    **{f"latest_{t}": f"0!select by sym from {t}" for t in LATEST},
    "spot1m": "0!select price:last price by asset, time:0D00:01 xbar time from spot",
    "surf1m": ("0!select last asset, last atmvol, last rr25, last bf25, last F, last T "
               "by sym, time:0D00:01 xbar time from surface"),
    "trades": "-500#select from trade",
    "risk_last": "select from risk where time=max time",
    "risk1m": ("0!select last mtm, last deltaspot, last cashdelta, last gamma, last vega, last theta, "
               "last vanna, last volga by time:0D00:01 xbar time from risk where kind=`total"),
    "scen_last": "select from scen where time=max time",
    "pnl": "select from pnl",
    "vares": "select from vares",
    "pos": "select from pos where time=max time",
    "dq": "select from dq",
    "gap": "select from gap",
    "lat1m": ("0!select ms:1e-6*med `long$time-`timespan$recv by time:0D00:01 xbar time from quote "
              "where (`timespan$recv)>0D00:00:01"),
    "counts": "([] table:tables[]; rows:count each value each tables[])",
}


def assets_in(views: dict) -> list[str]:
    """Assets with fitted smiles, BTC first, then the rest alphabetically."""
    s = views.get("latest_surface")
    if s is None or s.empty or "asset" not in s:
        return []
    return sorted(set(s["asset"]), key=lambda a: (a != "BTC", a))


def for_asset(views: dict, asset: str | None) -> dict:
    """The same views restricted to one asset (tables without an asset column pass through)."""
    if not asset:
        return views
    return {n: (v[v["asset"] == asset].reset_index(drop=True)
                if isinstance(v, pd.DataFrame) and "asset" in v.columns else v)
            for n, v in views.items()}


def _last_by_sym(df):
    return df.groupby("sym", sort=False).tail(1).reset_index(drop=True)


def _bars(df, cols, how="last", by=None):
    if df.empty:
        return pd.DataFrame(columns=([by] if by else []) + ["time"] + list(cols))
    g = df.assign(time=df["time"].dt.floor(MIN))
    keys = ([by] if by else []) + ["time"]
    return g.groupby(keys, sort=True)[list(cols)].agg(how).reset_index()


def _at_max_time(df):
    return df[df["time"] == df["time"].max()].reset_index(drop=True) if len(df) else df


# name -> pandas function(tables up to now) with the same meaning as Q[name]
P = {
    **{f"latest_{t}": (lambda t: lambda T: _last_by_sym(T[t]))(t) for t in LATEST},
    "spot1m": lambda T: _bars(T["spot"], ["price"], by="asset"),
    "surf1m": lambda T: _bars(T["surface"], ["asset", "atmvol", "rr25", "bf25", "F", "T"], by="sym"),
    "trades": lambda T: T["trade"].tail(500).reset_index(drop=True),
    "risk_last": lambda T: _at_max_time(T["risk"]),
    "risk1m": lambda T: _bars(T["risk"][T["risk"]["kind"] == "total"],
                              ["mtm", "deltaspot", "cashdelta", "gamma", "vega", "theta", "vanna", "volga"]),
    "scen_last": lambda T: _at_max_time(T["scen"]),
    "pnl": lambda T: T["pnl"],
    "vares": lambda T: T["vares"],
    "pos": lambda T: _at_max_time(T["pos"]),
    "dq": lambda T: T["dq"],
    "gap": lambda T: T["gap"],
    # a bundle stamps rows with the feed's receive time, so feed -> tickerplant delay is not in it
    "lat1m": lambda T: pd.DataFrame(columns=["time", "ms"]),
    "counts": lambda T: pd.DataFrame({"table": list(T), "rows": [len(v) for v in T.values()]}),
}
assert set(P) == set(Q), "every view needs both a q and a pandas definition"


# ---------------------------------------------------------------------- q -> pandas
def qframe(k, date: str | None = None) -> pd.DataFrame:
    """A q table from PyKX -> DataFrame: symbols as str, ``time`` (a time of day in the RDB) made
    absolute by adding the date (UTC), and timestamps as datetime64[ns]."""
    df = k.pd()
    if isinstance(df.index, pd.MultiIndex) or df.index.name is not None:
        df = df.reset_index()
    for c in df.columns:
        col = df[c]
        if pd.api.types.is_string_dtype(col) or col.dtype == object:
            df[c] = col.astype(object).where(col.notna(), "").map(lambda v: v.decode() if isinstance(v, bytes) else str(v))
        elif pd.api.types.is_datetime64_any_dtype(col):
            df[c] = col.astype("datetime64[ns]")
    if "time" in df.columns and pd.api.types.is_timedelta64_dtype(df["time"]):
        day = pd.Timestamp(date.replace(".", "-")) if date else pd.Timestamp.now("UTC").tz_localize(None).normalize()
        df["time"] = day + df["time"].astype("timedelta64[ns]")
    return df


def _kdb_worker(port: int, host: str, req, resp):
    """Child process: the only place PyKX is imported. Each request is a list of
    (key, gateway function, q query, date for absolute times); the reply is {key: DataFrame},
    plus "_errors" if any query failed."""
    os.environ.setdefault("PYKX_UNLICENSED", "true")
    import pykx as kx
    conn = None
    while True:
        jobs = req.get()
        if jobs is None:
            return
        out, errors = {}, []
        for key, fn, q, date in jobs:
            try:
                if conn is None:
                    conn = kx.SyncQConnection(host=host, port=port, no_ctx=True)
                out[key] = qframe(conn(fn, kx.CharVector(q)), date)
            except Exception as e:                       # one failed query must not blank the page
                if isinstance(e, (ConnectionError, OSError)):
                    conn = None
                out[key] = pd.DataFrame()
                errors.append(f"{key}: {type(e).__name__}: {e}")
        if errors:
            out["_errors"] = errors
        resp.put(out)


class KdbWorker:
    """Runs gateway queries in a separate process, the only place PyKX is loaded.

    PyKX brings kdb+'s own native library into its process; next to other native stacks
    (Dash, pyarrow, ...) that can crash with a segmentation fault, depending on library
    versions. In its own process it cannot clash with anything; only DataFrames cross back."""

    def __init__(self, port: int = 5013, host: str = "localhost", timeout: float = 60.0):
        self.port, self.host, self.timeout = port, host, timeout
        self._start()

    def _start(self):
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        self.req, self.resp = ctx.Queue(), ctx.Queue()
        self.proc = ctx.Process(target=_kdb_worker, args=(self.port, self.host, self.req, self.resp), daemon=True)
        self.proc.start()

    def run(self, jobs) -> dict[str, pd.DataFrame]:
        import queue
        jobs = list(jobs)
        if not self.proc.is_alive():                     # worker died: start a new one
            self._start()
        self.req.put(jobs)
        try:
            return self.resp.get(timeout=self.timeout)
        except queue.Empty:
            self.proc.terminate()
            self._start()
            return {**{j[0]: pd.DataFrame() for j in jobs}, "_errors": ["kdb+ worker timed out; restarted"]}

    def close(self):
        if self.proc.is_alive():
            self.req.put(None)
            self.proc.join(timeout=5)


class GatewaySource:
    """The live system: every view is one query the gateway forwards to the RDB (via KdbWorker)."""

    name = "live (kdb+ gateway)"

    def __init__(self, port: int = 5013, host: str = "localhost"):
        self.worker = KdbWorker(port, host, timeout=30.0)

    def now(self) -> pd.Timestamp:
        return pd.Timestamp.now("UTC").tz_localize(None)

    def views(self, names) -> dict[str, pd.DataFrame]:
        return self.worker.run([(n, ".gw.rdbq", Q[n], None) for n in names])

    def close(self):
        self.worker.close()


class ReplaySource:
    """The public demo: a recorded bundle played back on a clock, ``speed`` times real time,
    looping at the end. Views are the pandas definitions on rows stamped up to 'now'."""

    def __init__(self, folder, speed: float = 10.0, warmup_min: float = 10.0):
        from .bundle import load
        self.T = load(folder)
        self.name = f"replay of {Path(folder).name} ({speed:g}x)"
        times = pd.concat([df["time"] for df in self.T.values() if len(df)])
        self.t_start, self.t_end = times.min(), times.max()
        self.speed, self.warm = speed, pd.Timedelta(minutes=warmup_min)
        self.wall0 = time.monotonic()
        # the "ref" table is static: keep it whole regardless of the clock
        self._cache_t, self._cache = None, None

    def now(self) -> pd.Timestamp:
        span = (self.t_end - self.t_start - self.warm).total_seconds()
        el = ((time.monotonic() - self.wall0) * self.speed) % max(span, 1.0)
        return self.t_start + self.warm + pd.Timedelta(seconds=el)

    def tables_at(self, now) -> dict[str, pd.DataFrame]:
        if self._cache_t == now:
            return self._cache
        out = {}
        for t, df in self.T.items():
            out[t] = df if t == "ref" else df[df["time"] <= now]
        self._cache_t, self._cache = now, out
        return out

    def views(self, names, now=None) -> dict[str, pd.DataFrame]:
        T = self.tables_at(self.now() if now is None else now)
        return {n: P[n](T) for n in names}
