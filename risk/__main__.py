"""Real-time risk process: a kdb+ subscriber downstream of the surface engines, one book per coin.

    python -m risk                          # a sample book for every coin with fitted smiles
    python -m risk --assets BTC ETH         # only these coins
    python -m risk --book mybook.csv        # your own positions (columns: risk/book.py COLS;
                                            # the coin is read from each sym, e.g. SOL_USDC-30OCT26-...)

Subscribes to the tickerplant's `surface` (the engines' fitted smiles) and `spot`, keeps the
latest smile per expiry for every coin, and publishes back through the tickerplant, per coin:
    pos     the positions, at start-up and at each start of day        (risk/book.py)
    risk    Greeks by bucket, every --risk-every seconds              (risk/core.py)
    scen    the spot x vol scenario grid, every --scen-every seconds
    pnl     P&L explain for each --pnl-every interval
    vares   each coin's own VaR / ES (HS and FHS) and VaR backtest     (risk/var.py)
and, across all coins, every --var-every seconds:
    port    the joint VaR / ES, diversification, ES contributions, the stress and all-coin
            variants, the joint backtest and the correlation of daily moves (risk/portfolio.py)
Everything is therefore logged, replayable and saved to the HDB like market data.

Sample books: the same seven legs for every coin (risk/book.py SAMPLE_LEGS), sized so each leg
has the same USD notional as the BTC book's, and delta-hedged under the smile rule with a
future on the front leg's expiry (a synthetic forward at our parity forward for the USDC coins,
which have no dated futures on Deribit).
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

os.environ.setdefault("PYKX_UNLICENSED", "true")
import pykx as kx  # noqa: E402

from engine.ipc import Subscriber, records  # noqa: E402
from feed.sinks import TickerplantSink  # noqa: E402

from . import portfolio as PF  # noqa: E402
from . import var as varmod  # noqa: E402
from .book import label_of, load_csv, sample_book  # noqa: E402
from .core import aggregate, pnl_explain, pnl_rows, position_greeks, scenario_grid  # noqa: E402
from .history import fetch_histories, var_source, vol_source  # noqa: E402
from .market import Market  # noqa: E402
from .rows import pnl_q_rows, port_rows, pos_rows, risk_rows, scen_rows, vares_rows  # noqa: E402

TABLES = ("surface", "spot")
NS = 1_000_000_000


def asset_of_label(label: str) -> str:
    return label.split("-")[0]


@dataclass
class Coin:
    """Everything the risk process keeps for one coin."""
    asset: str
    surface: dict = field(default_factory=dict)       # latest fitted smile per expiry
    spot: float = math.nan
    listed: dict = field(default_factory=dict)        # expiry label -> listed strikes
    listed_exp: dict = field(default_factory=dict)    # expiry label -> expiry (ns)
    book: list = field(default_factory=list)
    prev: Market | None = None                        # market at the start of the current P&L interval
    first_seen: float | None = None

    def market(self, now_ns: int) -> Market | None:
        m = Market.from_surface(self.surface.values(), asof=now_ns, spot=self.spot)
        return m if m.labels else None


class RiskProcess:
    def __init__(self, args):
        self.args = args
        self.R = args.R
        assets = list(getattr(args, "assets", None) or ["ALL"])
        if assets == ["ALL"] and getattr(args, "currency", None) not in (None, "", "ALL"):
            assets = [args.currency]                     # the old single-coin flag
        self.only = None if assets == ["ALL"] else set(assets)
        self.out = TickerplantSink(port=args.tp_port)
        self.coins: dict[str, Coin] = {}
        self.due = {"risk": 0.0, "scen": 0.0, "pnl": time.monotonic() + args.pnl_every, "var": 0.0}
        self.hist = None
        self.hist_day = None
        self.hist_assets: set = set()
        self.fixed_book = bool(getattr(args, "book", None))

    # --------------------------------------------------------------- state
    def wants(self, asset: str) -> bool:
        return self.only is None or asset in self.only

    def coin(self, asset: str) -> Coin:
        if asset not in self.coins:
            self.coins[asset] = Coin(asset)
        return self.coins[asset]

    @property
    def spot(self) -> float:
        """BTC's index (kept for tools and tests written for the single-coin process)."""
        return self.coins["BTC"].spot if "BTC" in self.coins else math.nan

    def bootstrap(self):
        with kx.SyncQConnection(port=self.args.rdb_port, no_ctx=True) as c:
            for r in records(c("0!select by sym from surface")):
                a = r.get("asset") or asset_of_label(r["sym"])
                if self.wants(a):
                    self.coin(a).surface[r["sym"]] = r
            for r in records(c("0!select by sym from spot")):
                a = r.get("asset") or "BTC"
                if self.wants(a) and np.isfinite(r["price"]):
                    self.coin(a).spot = r["price"]
            for r in records(c("select sym, strike, expiry from ref where kind=`option")):
                lab = "-".join(r["sym"].split("-")[:2])
                a = asset_of_label(lab)
                if self.wants(a):
                    self.coin(a).listed.setdefault(lab, []).append(r["strike"])
                    if r.get("expiry") is not None:
                        self.coin(a).listed_exp[lab] = int(r["expiry"])
        n = sum(len(c.surface) for c in self.coins.values())
        print(f"Risk: bootstrapped {n} smiles for {len(self.coins)} coins "
              f"({', '.join(sorted(self.coins)) or 'none yet'})", flush=True)
        if self.args.book:
            for r in load_csv(self.args.book):
                a = asset_of_label(label_of(r))
                if self.wants(a):
                    self.coin(a).book.append(r)
            print(f"Risk: loaded positions for {', '.join(sorted(a for a, c in self.coins.items() if c.book))} "
                  f"from {self.args.book}", flush=True)
            for c in self.coins.values():
                self.publish_pos(c)

    def handle(self, table: str, rows: list[dict]):
        if table == "surface":
            for r in rows:
                a = r.get("asset") or asset_of_label(r["sym"])
                if self.wants(a):
                    self.coin(a).surface[r["sym"]] = r
        elif table == "spot":
            for r in rows:
                a = r.get("asset") or "BTC"
                if self.wants(a) and np.isfinite(r["price"]):
                    self.coin(a).spot = r["price"]

    # ------------------------------------------------------------ publishing
    def publish_pos(self, c: Coin):
        if c.book:
            self.out.publish("pos", pos_rows(c.book, c.asset))

    def ready(self, c: Coin, m: Market, now_m: float) -> bool:
        """Build the sample book once the engine has fitted the curve out to ~3 months (the longest
        leg) or to every listed expiry it will fit, or after 60 s, so legs land on the intended expiries."""
        c.first_seen = c.first_seen or now_m
        listed = [(e - m.asof) / (86400 * NS) for e in c.listed_exp.values()]
        want = min(80.0, 0.95 * max([d for d in listed if d >= 2] or [80.0 / 0.95]))
        return (m.T() * 365).max() >= want or now_m - c.first_seen >= 60

    def ref_spot(self) -> float:
        for a in ("BTC", "BTC_USDC"):
            if a in self.coins and np.isfinite(self.coins[a].spot):
                return self.coins[a].spot
        return math.nan

    def tick(self):
        now_m = time.monotonic()
        if not any(now_m >= t for t in self.due.values()):
            return
        now = time.time_ns()
        mk = {}
        for a, c in sorted(self.coins.items()):
            m = c.market(now)
            if m is None:
                continue
            if not c.book and not self.fixed_book:
                if not self.ready(c, m, now_m):
                    continue
                spot = c.spot if np.isfinite(c.spot) else float(m.F[0])
                c.book = sample_book(m, c.listed, hedge_rule_R=self.R,
                                     scale=PF.scale_for(a, spot, self.ref_spot()))
                print(f"Risk: built the {a} sample book ({len(c.book)} positions, delta-hedged under "
                      f"R = {self.R})", flush=True)
                self.publish_pos(c)
                c.prev = m
            if c.book:
                mk[a] = m
        if not mk:
            return
        a_ = self.args
        if now_m >= self.due["risk"]:
            self.due["risk"] = now_m + a_.risk_every
            for a, m in mk.items():
                agg = aggregate(position_greeks(self.coins[a].book, m, R=self.R))
                if not agg.empty:
                    self.out.publish("risk", risk_rows(agg, a))
        if now_m >= self.due["scen"]:
            self.due["scen"] = now_m + a_.scen_every
            for a, m in mk.items():
                book = self.coins[a].book
                g = scenario_grid(book, m, R=self.R)
                self.out.publish("scen", scen_rows(g, sorted({r["book"] for r in book})[0], a))
        if now_m >= self.due["pnl"]:
            self.due["pnl"] = now_m + a_.pnl_every
            for a, m in mk.items():
                c = self.coins[a]
                if c.prev is not None:
                    ex = pnl_explain(c.book, c.prev, m, R=self.R)
                    if not ex.empty:
                        self.out.publish("pnl", pnl_q_rows(pnl_rows(ex), a))
                c.prev = m
        if now_m >= self.due["var"]:
            self.due["var"] = now_m + a_.var_every
            self.publish_var(mk)

    def history(self, assets) -> dict | None:
        """Daily index + vol history for these coins, refreshed once a day (cached on disk)."""
        today = time.strftime("%Y-%m-%d", time.gmtime())
        cache = Path(self.args.data).expanduser() / "history" / "all_daily.csv"
        want = set(assets) | self.hist_assets
        try:
            if self.hist is None or self.hist_day != today or set(assets) - self.hist_assets:
                self.hist = fetch_histories(sorted(want), cache=cache, refresh=True)
                self.hist_day, self.hist_assets = today, set(self.hist)
        except Exception as e:                                   # offline: use the cached file
            if cache.exists():
                try:
                    self.hist = fetch_histories(sorted(assets), cache=cache)
                    self.hist_assets = set(self.hist)
                except Exception:
                    pass
            print(f"Risk: history refresh failed ({type(e).__name__}); "
                  f"{'using the cached file' if self.hist is not None else 'no VaR yet'}", flush=True)
        return self.hist

    def publish_var(self, mk: dict):
        hist = self.history(mk)
        if hist is None:
            return
        mk = {a: m for a, m in mk.items() if a in hist}
        books = {a: self.coins[a].book for a in mk}
        w = self.args.var_window
        for a, m in mk.items():                                    # each coin's own VaR, as before
            for b in sorted({r["book"] for r in books[a]}):
                rows = [r for r in books[a] if r["book"] == b]
                res = varmod.compute(rows, m, hist[a], R=self.R, window=w, source=var_source(a))
                if len(hist[a]) - 1 > w:
                    bt = varmod.backtest(rows, m, hist[a], R=self.R, window=w)
                else:                                              # too little history to backtest
                    bt = {k: {"exceptions": 0, "days": 0, "kupiec_p": math.nan} for k in ("hs", "fhs")}
                self.out.publish("vares", vares_rows(res, bt, b, a, self.R))
        proxied = [a for a in mk if vol_source(a)[1]]
        rep = PF.compute(books, mk, hist, R=self.R, window=w, proxied=proxied)
        bt = PF.backtest(books, mk, hist, self.R, rep["main"], window=w) if rep["main"] else {}
        self.out.publish("port", port_rows(rep, bt))
        f = rep["methods"]["fhs"].get("joint")
        if f:
            print(f"Risk: portfolio of {len(rep['main'])} coins: ES97.5 {f['joint']['es975']:,.0f} USD (FHS) "
                  f"vs {f['sum_es975']:,.0f} summed alone (diversification {f['div_es975']:,.0f}); "
                  f"VaR99 {f['joint']['var99']:,.0f}"
                  + (f"; joint backtest {bt['fhs']['exceptions']}/{bt['fhs']['days']}, "
                     f"Kupiec p = {bt['fhs']['kupiec_p']:.2f}" if bt else ""), flush=True)

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
                else:                                            # new day: record today's books
                    print(f"Risk: end of day {msg[1]}; republishing positions", flush=True)
                    for c in self.coins.values():
                        self.publish_pos(c)
                self.tick()


def main():
    ap = argparse.ArgumentParser(description="Real-time portfolio risk")
    ap.add_argument("--tp-port", type=int, default=5010)
    ap.add_argument("--rdb-port", type=int, default=5011)
    ap.add_argument("--assets", nargs="*", default=["ALL"], help="coins to book (default ALL: every coin with fitted smiles)")
    ap.add_argument("--currency", default=None, help="old single-coin flag: the same as --assets <coin>")
    ap.add_argument("--R", type=float, default=0.0, help="smile rule: skew-stickiness ratio "
                    "(0 sticky-moneyness, 1 sticky-strike); see results/phase5_smile_rules.md")
    ap.add_argument("--book", help="positions CSV (default: build a sample book per coin)")
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
