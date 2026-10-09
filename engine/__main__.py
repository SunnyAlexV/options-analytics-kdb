"""Real-time surface engine process: subscribes to market data, publishes iv / fwd / surface.

    python -m engine                      # stream mode: subscribe to the tickerplant (default)
    python -m engine --mode poll          # poll mode: read new rows from the RDB every 100 ms

Stream mode is the standard kdb+ "real-time engine" (RTE): like the RDB, it calls
.u.sub on the tickerplant and receives every update as an async (`upd; table; data)
message. Poll mode reads only rows it has not seen yet (by row index), so it is
also lossless; it exists as a fallback that uses only plain queries.

Either way: every quote update gets an implied vol immediately, changed expiries
are refitted at most every --throttle seconds, and results go back through the
tickerplant, so they are logged, replayable and saved to the HDB like market data.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

import pandas as pd

os.environ.setdefault("PYKX_UNLICENSED", "true")
import pykx as kx  # noqa: E402

from feed.sinks import TickerplantSink  # noqa: E402

from .core import EngineConfig, SurfaceEngine  # noqa: E402

TABLES = ("ref", "snap", "quote")


def records(table) -> list[dict]:
    """q table (pykx) -> list of dicts, timestamps as int ns, symbols as str.

    Column types are checked with pandas' own API: pandas 3 stores text in its own
    string dtype, which NumPy's type checks cannot interpret.
    """
    df = table.pd()
    for c in df.columns:
        col = df[c]
        if pd.api.types.is_datetime64_any_dtype(col):
            df[c] = col.astype("int64")
        elif pd.api.types.is_string_dtype(col) or col.dtype == object:
            df[c] = col.astype(object).where(col.notna(), "").map(str)
    return df.to_dict("records")


def open_subscriber(port: int):
    """PyKX's RawQConnection must be awaited to open (an asyncio pattern); do that once here.
    After that, poll_send / poll_recv are ordinary non-blocking calls.

    no_ctx=True on every connection in this project: by default PyKX sends an extra
    query on connect to introspect the server's namespaces (its "context interface"),
    which we never use -- one less round trip, and nothing unexpected sent to a tickerplant."""
    # PyKX looks the event loop up again on later calls, so it must stay open and be this
    # thread's current loop (asyncio.run would close it straight after connecting).
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(_await(kx.RawQConnection(port=port, no_ctx=True, event_loop=loop)))


async def _await(x):
    return await x


class Runner:
    def __init__(self, args):
        self.args = args
        cfg = EngineConfig(throttle_s=args.throttle)
        self.eng = SurfaceEngine(cfg, asset=args.currency)
        self.out = TickerplantSink(port=args.tp_port)
        self.t_stats = time.monotonic()
        self.n = {"quote": 0, "iv": 0, "surface": 0, "fwd": 0}

    def bootstrap(self):
        """Load today's state (instruments, latest forwards and quotes) from the RDB."""
        with kx.SyncQConnection(port=self.args.rdb_port, no_ctx=True) as c:
            self.eng.on_ref(records(c("0!select by sym from ref")))
            self.eng.on_snap(records(c("0!select by sym from snap")))
            self.eng.on_quotes(records(c("0!select by sym from quote")))   # state only, not published
        print(f"Engine: bootstrapped {len(self.eng.ref)} instruments, {len(self.eng.book)} quotes", flush=True)

    def handle(self, table: str, rows: list[dict]):
        if table == "ref":
            self.eng.on_ref(rows)
        elif table == "snap":
            self.eng.on_snap(rows)
        elif table == "quote":
            ivs = self.eng.on_quotes(rows)
            self.n["quote"] += len(rows)
            if ivs:
                self.out.publish("iv", ivs)
                self.n["iv"] += len(ivs)

    def tick(self):
        fwd, surf = self.eng.refit(time.time_ns())
        if fwd:
            self.out.publish("fwd", fwd)
            self.n["fwd"] += len(fwd)
        if surf:
            self.out.publish("surface", surf)
            self.n["surface"] += len(surf)
        if time.monotonic() - self.t_stats >= 30:
            el = time.monotonic() - self.t_stats
            fit_ms = 1e3 * self.eng.stats["fit_s"] / max(self.eng.stats["refits"], 1)
            print(f"Engine: {self.n['quote'] / el:,.0f} quotes/s -> {self.n['iv'] / el:,.0f} ivs/s, "
                  f"{self.n['surface'] / el:.1f} surface rows/s, {fit_ms:.1f} ms per refit", flush=True)
            self.n = dict.fromkeys(self.n, 0)
            self.t_stats = time.monotonic()

    # ------------------------------------------------------------ stream mode
    def run_stream(self):
        conn = open_subscriber(self.args.tp_port)
        for t in TABLES:                                  # subscribe: .u.sub[`table; `] = all syms
            conn(".u.sub", kx.SymbolAtom(t), kx.SymbolAtom(""))
        conn.poll_send(0)
        # Each .u.sub replies (table name; empty schema). Live updates for tables we have
        # already subscribed to may arrive in between: buffer them, don't mistake them for replies.
        replies, early = 0, []
        while replies < len(TABLES):
            msg = conn.poll_recv()
            if msg is None:
                time.sleep(0.001)
            elif self._is_upd(msg):
                early.append(msg)
            else:
                replies += 1
        print("Engine: subscribed to " + ", ".join(TABLES), flush=True)
        self.bootstrap()
        for msg in early:
            self._dispatch(msg)
        while True:
            msg = conn.poll_recv()
            if msg is None:
                self.tick()
                time.sleep(0.002)
                continue
            self._dispatch(msg)
            self.tick()

    # Unlicensed PyKX cannot index into q objects (msg[0] raises a licence error), but it
    # can iterate over them: list(msg) gives the elements as q objects, which convert fine.
    @staticmethod
    def _parts(msg) -> list:
        try:
            return list(msg)
        except TypeError:
            return []

    @staticmethod
    def _is_upd(msg) -> bool:
        parts = Runner._parts(msg)
        return len(parts) == 3 and parts[0].py() == "upd"

    def _dispatch(self, msg):
        parts = self._parts(msg)
        if not parts:
            return
        fn = parts[0].py()
        if fn == "upd":                                   # (`upd; `table; rows)
            self.handle(parts[1].py(), records(parts[2]))
        elif fn == ".u.end":                              # end of day: nothing to reset, state carries on
            print(f"Engine: end of day {parts[1].py()}", flush=True)

    # -------------------------------------------------------------- poll mode
    def run_poll(self):
        self.bootstrap()
        seen = dict.fromkeys(TABLES, 0)
        with kx.SyncQConnection(port=self.args.rdb_port, no_ctx=True) as c:
            for t in TABLES:
                seen[t] = int(c(f"count {t}").py())       # bootstrap already covered these rows
            print("Engine: polling the RDB every 100 ms", flush=True)
            while True:
                t0 = time.monotonic()
                for t in TABLES:
                    n = int(c(f"count {t}").py())
                    if n < seen[t]:                       # end of day: the RDB was emptied
                        seen[t] = 0
                    if n > seen[t]:
                        # row numbers written straight into the query (no lambda arguments)
                        self.handle(t, records(c(f"select from {t} where i within {seen[t]} {n - 1}")))
                        seen[t] = n
                self.tick()
                time.sleep(max(0.0, 0.1 - (time.monotonic() - t0)))


def main():
    ap = argparse.ArgumentParser(description="Real-time surface engine")
    ap.add_argument("--mode", choices=["stream", "poll"], default="stream")
    ap.add_argument("--tp-port", type=int, default=5010)
    ap.add_argument("--rdb-port", type=int, default=5011)
    ap.add_argument("--throttle", type=float, default=0.5, help="refit an expiry at most this often (s)")
    ap.add_argument("--currency", default="BTC")
    args = ap.parse_args()
    r = Runner(args)
    try:
        r.run_stream() if args.mode == "stream" else r.run_poll()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        r.out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
