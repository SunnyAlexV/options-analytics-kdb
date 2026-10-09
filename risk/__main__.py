"""Real-time risk process: a second kdb+ subscriber, downstream of the surface engine.

    python -m risk                          # sample book, smile rule from config (RISK_R)
    python -m risk --book mybook.csv        # your own positions (columns: risk/book.py COLS)

Subscribes to the tickerplant's `surface` (the engine's fitted smiles) and `spot`, keeps the
latest smile per expiry, and publishes back through the tickerplant:
    pos     the positions, at start-up and at each start of day        (risk/book.py)
    risk    Greeks by bucket, every --risk-every seconds              (risk/core.py)
    scen    the spot x vol scenario grid, every --scen-every seconds
    pnl     P&L explain for each --pnl-every interval
    vares   VaR / ES (HS and FHS) and the VaR backtest, every --var-every seconds (risk/var.py)
Everything is therefore logged, replayable and saved to the HDB like market data.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("PYKX_UNLICENSED", "true")
import pykx as kx  # noqa: E402

from engine.ipc import Subscriber, records  # noqa: E402
from feed.sinks import TickerplantSink  # noqa: E402

from . import var as varmod  # noqa: E402
from .book import load_csv, sample_book  # noqa: E402
from .core import aggregate, pnl_explain, pnl_rows, position_greeks, scenario_grid  # noqa: E402
from .market import Market  # noqa: E402
from .rows import pnl_q_rows, pos_rows, risk_rows, scen_rows, vares_rows  # noqa: E402

TABLES = ("surface", "spot")


class RiskProcess:
    def __init__(self, args):
        self.args = args
        self.R = args.R
        self.asset = args.currency
        self.out = TickerplantSink(port=args.tp_port)
        self.surface: dict[str, dict] = {}            # latest fitted smile per expiry
        self.spot = math.nan
        self.book: list[dict] = []
        self.listed: dict[str, list[float]] = {}
        self.prev: Market | None = None               # market at the start of the current P&L interval
        self.due = {"risk": 0.0, "scen": 0.0, "pnl": time.monotonic() + args.pnl_every, "var": 0.0}
        self.hist = None
        self.hist_day = None

    # --------------------------------------------------------------- state
    def bootstrap(self):
        # the tickerplant carries every coin: take this process's asset only (the spot table
        # holds btc_usd, eth_usd, sol_usdc, ... side by side, so "last price" alone is wrong)
        a = f"`{self.asset}"
        with kx.SyncQConnection(port=self.args.rdb_port, no_ctx=True) as c:
            for r in records(c(f"0!select by sym from surface where asset={a}")):
                self.surface[r["sym"]] = r
            sp = records(c(f"select last price from spot where asset={a}"))
            if sp and np.isfinite(sp[0]["price"]):
                self.spot = sp[0]["price"]
            for r in records(c(f"select sym, strike from ref where kind=`option, asset={a}")):
                self.listed.setdefault("-".join(r["sym"].split("-")[:2]), []).append(r["strike"])
        print(f"Risk: bootstrapped {len(self.surface)} smiles, spot {self.spot:,.0f}", flush=True)
        if self.args.book:
            self.book = load_csv(self.args.book)
            print(f"Risk: loaded {len(self.book)} positions from {self.args.book}", flush=True)
            self.publish_pos()

    def market(self, now_ns: int) -> Market | None:
        m = Market.from_surface(self.surface.values(), asof=now_ns, spot=self.spot)
        return m if m.labels else None

    def handle(self, table: str, rows: list[dict]):
        if table == "surface":
            for r in rows:
                if r.get("asset", self.asset) == self.asset:
                    self.surface[r["sym"]] = r
        elif table == "spot":
            px = [r["price"] for r in rows if r.get("asset", self.asset) == self.asset]
            if px and np.isfinite(px[-1]):
                self.spot = px[-1]

    # ------------------------------------------------------------ publishing
    def publish_pos(self):
        if self.book:
            self.out.publish("pos", pos_rows(self.book, self.asset))

    def tick(self):
        now_m = time.monotonic()
        if not any(now_m >= t for t in self.due.values()):
            return
        now = time.time_ns()
        m = self.market(now)
        if m is None:
            return
        if not self.book:                              # no positions given: build the sample book
            # the engine fits expiries one by one as data arrives: wait until the curve reaches
            # ~3 months (the longest sample leg), or 60 s, so legs land on the intended expiries
            self.first_seen = getattr(self, "first_seen", now_m)
            if (m.T() * 365).max() < 80 and now_m - self.first_seen < 60:
                return
            self.book = sample_book(m, self.listed, hedge_rule_R=self.R)
            print(f"Risk: built the sample book ({len(self.book)} positions, delta-hedged under "
                  f"R = {self.R})", flush=True)
            self.publish_pos()
            self.prev = m
        a = self.args
        if now_m >= self.due["risk"]:
            self.due["risk"] = now_m + a.risk_every
            agg = aggregate(position_greeks(self.book, m, R=self.R))
            self.out.publish("risk", risk_rows(agg, self.asset))
        if now_m >= self.due["scen"]:
            self.due["scen"] = now_m + a.scen_every
            g = scenario_grid(self.book, m, R=self.R)
            books = sorted({r["book"] for r in self.book})
            self.out.publish("scen", scen_rows(g, books[0], self.asset))
        if now_m >= self.due["pnl"]:
            self.due["pnl"] = now_m + a.pnl_every
            if self.prev is not None:
                ex = pnl_explain(self.book, self.prev, m, R=self.R)
                if not ex.empty:
                    self.out.publish("pnl", pnl_q_rows(pnl_rows(ex), self.asset))
            self.prev = m
        if now_m >= self.due["var"]:
            self.due["var"] = now_m + a.var_every
            self.publish_var(m)

    def publish_var(self, m: Market):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        try:
            if self.hist is None or self.hist_day != today:     # refresh the history once a day
                cache = Path(self.args.data).expanduser() / "history" / f"{self.asset.lower()}_daily.csv"
                self.hist = varmod.fetch_history(self.asset, cache=cache, refresh=True)
                self.hist_day = today
        except Exception as e:                                   # offline: use the cached file
            cache = Path(self.args.data).expanduser() / "history" / f"{self.asset.lower()}_daily.csv"
            if self.hist is None and cache.exists():
                self.hist = varmod.fetch_history(self.asset, cache=cache)
            print(f"Risk: history refresh failed ({type(e).__name__}); "
                  f"{'using the cached file' if self.hist is not None else 'no VaR yet'}", flush=True)
            if self.hist is None:
                return
        books = sorted({r["book"] for r in self.book})
        for b in books:
            rows = [r for r in self.book if r["book"] == b]
            res = varmod.compute(rows, m, self.hist, R=self.R, window=self.args.var_window)
            bt = varmod.backtest(rows, m, self.hist, R=self.R, window=self.args.var_window)
            out = vares_rows(res, bt, b, self.asset, self.R)
            self.out.publish("vares", out)
            fhs = next(o for o in out if o["method"] == "fhs")
            print(f"Risk: {b} VaR99 {fhs['var99']:,.0f} USD, ES97.5 {fhs['es975']:,.0f} USD (FHS); "
                  f"backtest {fhs['btexc']}/{fhs['btdays']} exceptions, Kupiec p = {fhs['kupiec']:.2f}",
                  flush=True)

    # ------------------------------------------------------------------ run
    def run(self):
        with Subscriber(self.args.tp_port, TABLES) as sub:
            print("Risk: subscribed to " + ", ".join(TABLES), flush=True)
            self.bootstrap()
            for msg in sub:
                if msg is None:
                    self.tick()
                    time.sleep(0.005)
                    continue
                if msg[0] == "upd":
                    self.handle(msg[1], msg[2])
                else:                                            # new day: record today's book
                    print(f"Risk: end of day {msg[1]}; republishing positions", flush=True)
                    self.publish_pos()
                self.tick()


def main():
    ap = argparse.ArgumentParser(description="Real-time portfolio risk")
    ap.add_argument("--tp-port", type=int, default=5010)
    ap.add_argument("--rdb-port", type=int, default=5011)
    ap.add_argument("--currency", default="BTC")
    ap.add_argument("--R", type=float, default=0.0, help="smile rule: skew-stickiness ratio "
                    "(0 sticky-moneyness, 1 sticky-strike); see results/phase5_smile_rules.md")
    ap.add_argument("--book", help="positions CSV (default: build the sample book)")
    ap.add_argument("--data", default=os.environ.get("DATA", "~/kdbdata"), help="for the history cache")
    ap.add_argument("--risk-every", type=float, default=5.0)
    ap.add_argument("--scen-every", type=float, default=60.0)
    ap.add_argument("--pnl-every", type=float, default=60.0)
    ap.add_argument("--var-every", type=float, default=900.0)
    ap.add_argument("--var-window", type=int, default=365)
    args = ap.parse_args()
    p = RiskProcess(args)
    try:
        p.run()
    except KeyboardInterrupt:
        print("\nStopped.")
    except (ConnectionError, RuntimeError, kx.QError) as e:
        print(f"Risk: lost the kdb+ connection ({type(e).__name__}: {e}); stopping", flush=True)
        return 1
    finally:
        p.out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
