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
import os
import sys
import time

os.environ.setdefault("PYKX_UNLICENSED", "true")
import pykx as kx  # noqa: E402

from feed.sinks import TickerplantSink  # noqa: E402

from .core import EngineConfig, SurfaceEngine  # noqa: E402
from .ipc import Subscriber, records  # noqa: E402,F401  (records: used by tests)

TABLES = ("ref", "snap", "quote")


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
        with Subscriber(self.args.tp_port, TABLES) as sub:        # engine/ipc.py: the .u.sub handshake
            print("Engine: subscribed to " + ", ".join(TABLES), flush=True)
            self.bootstrap()
            for msg in sub:
                if msg is None:
                    self.tick()
                    time.sleep(0.002)
                    continue
                if msg[0] == "upd":
                    self.handle(msg[1], msg[2])
                else:                                      # end of day: state carries on
                    print(f"Engine: end of day {msg[1]}", flush=True)
                self.tick()

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
    except (ConnectionError, RuntimeError, kx.QError) as e:   # tickerplant / RDB went away
        print(f"Engine: lost the kdb+ connection ({type(e).__name__}: {e}); stopping", flush=True)
        return 1
    finally:
        r.out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
