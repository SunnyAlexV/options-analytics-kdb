"""End-to-end test of the whole system: feed -> tickerplant -> RDB -> HDB -> gateway.

Runs a separate, throwaway copy of the system (ports 6010-6013, data in
~/kdbdata-test), so it never touches your real data. Takes about 4 minutes.

    python scripts/system_test.py

Checks:
  1. Every process starts and the RDB holds every table, with q column types
     exactly matching feed/schema.py.
  2. Live data flows: quotes, spot and reference rows arrive.
  3. Crash recovery: kill the RDB, restart it, and it rebuilds exactly the
     same rows from the tickerplant log.
  4. The gateway returns the same rows as the RDB.
  5. The surface engine publishes implied vols, forwards and fitted smiles, in
     both stream mode (tickerplant subscription) and poll mode (RDB reads).
  6. Risk (Phase 5): positions, risk by bucket, the scenario grid, P&L explain
     and VaR/ES are published, with basic sanity checks on each.
  7. End of day: force one, and today's rows move to the HDB intact while
     the RDB empties.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("PYKX_UNLICENSED", "true")
import pykx as kx  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from feed.schema import SCHEMAS  # noqa: E402

ENV = dict(os.environ, ENGINE_MODE="stream", RISK_PNL_EVERY="20", DATA=str(Path.home() / "kdbdata-test"),
           TP_PORT="6010", RDB_PORT="6011", HDB_PORT="6012", GW_PORT="6013",
           PY=sys.executable)
PORT = {"tp": 6010, "rdb": 6011, "hdb": 6012, "gw": 6013}
results: list[tuple[bool, str]] = []


def sh(*args: str) -> None:
    subprocess.run(["bash", str(ROOT / "scripts" / args[0]), *args[1:]], env=ENV, check=True)


def q(proc: str, expr: str, *args):
    with kx.SyncQConnection(port=PORT[proc], no_ctx=True) as c:
        return c(expr, *args).py()


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(f"  {'PASS' if ok else 'FAIL'}  {what}")


def show_logs(lines: int = 15) -> None:
    for name in ("tp", "hdb", "rdb", "gw", "feed", "engine", "risk"):
        f = Path(ENV["DATA"]) / "logs" / f"{name}.log"
        if f.exists():
            print(f"----- {name}.log -----")
            print("\n".join(f.read_text(errors="replace").splitlines()[-lines:]))


def counts() -> dict:
    return q("rdb", "tables[]!count each value each tables[]")


def main() -> int:
    data = Path(ENV["DATA"])
    sh("stop.sh")
    if data.exists():
        shutil.rmtree(data)

    print("\n[1] start everything (incl. the surface engine and risk) and run for 60 s")
    sh("start.sh")
    time.sleep(60)

    print("\n[2] schemas and data in the RDB")
    c1 = counts()
    for table, cols in SCHEMAS.items():
        types = q("rdb", "{exec t from meta x}", kx.SymbolAtom(table))
        types = types.decode() if isinstance(types, bytes) else "".join(types)
        want = "".join(t for _, t in cols)
        check(types == want, f"{table}: q types {types!r} match schema {want!r}")
    check(c1.get("quote", 0) > 1000, f"quotes arriving ({c1.get('quote', 0):,} in 60 s)")
    check(c1.get("spot", 0) >= 30, f"spot index arriving ({c1.get('spot', 0)} rows)")
    check(c1.get("ref", 0) > 100, f"reference data loaded ({c1.get('ref', 0)} instruments)")
    # feed received (recv) -> tickerplant stamped (time). time is time-of-day, so
    # compare it with recv's time-of-day; rows within 1 s of midnight are skipped.
    lag = q("rdb", "exec 1e-6*med `long$d from select d:time-`timespan$recv from quote "
                   "where (`timespan$recv)>0D00:00:01")
    print(f"  INFO  median feed -> tickerplant delay: {lag:.2f} ms")

    print(f"\n[2b] surface engine ({ENV['ENGINE_MODE']} mode)")
    check(c1.get("iv", 0) > 1000, f"implied vols published ({c1.get('iv', 0):,} rows)")
    check(c1.get("surface", 0) >= 10, f"smiles fitted ({c1.get('surface', 0)} surface rows)")
    check(c1.get("fwd", 0) >= 10, f"parity forwards estimated ({c1.get('fwd', 0)} rows)")
    if c1.get("iv", 0):
        med_iv = q("rdb", "exec med midiv from iv where not null midiv")
        check(0.1 < med_iv < 2.0, f"median mid implied vol plausible ({med_iv:.1%})")
    if c1.get("surface", 0):
        inb = q("rdb", "exec med inband from select by sym from surface")
        check(inb > 0.5, f"smiles inside the bid-ask band (median {inb:.0%} of points)")
        neg = q("rdb", "exec sum afming<0 from select by sym from surface")
        check(neg == 0, "arbitrage-free smiles have no negative density")
    if c1.get("fwd", 0):
        dd = q("rdb", "exec med abs diff from select by sym from fwd where not null diff")
        print(f"  INFO  median |parity forward - Deribit forward|: {dd:.2f} USD")

    print("\n[2c] risk (sample book, smile rule R = " + ENV.get("RISK_R", "0") + ")")
    check(c1.get("pos", 0) >= 5, f"positions published ({c1.get('pos', 0)} rows)")
    check(c1.get("risk", 0) > 0, f"risk by bucket published ({c1.get('risk', 0):,} rows)")
    if c1.get("risk", 0):
        tot = q("rdb", "exec last deltaspot, last vega, last mtm from risk where kind=`total")
        print(f"  INFO  book: value {tot['mtm']:,.0f} USD, spot delta {tot['deltaspot']:+.3f} BTC, "
              f"vega {tot['vega']:,.0f} USD/vol pt")
        check(abs(tot["deltaspot"]) < 2.0, "book still close to delta-neutral (hedged at start)")
    check(c1.get("scen", 0) >= 77, f"scenario grid published ({c1.get('scen', 0)} rows, 77 per grid)")
    if c1.get("scen", 0):
        z = q("rdb", "exec last abs pnl from scen where dspot=0, dvol=0")
        check(z < 1e-6, "scenario grid: zero move gives zero P&L")
    check(c1.get("pnl", 0) >= 1, f"P&L explain published ({c1.get('pnl', 0)} intervals)")
    if c1.get("pnl", 0):
        e = q("rdb", "exec sum abs actual, sum abs unexpl from pnl")
        print(f"  INFO  P&L explain: sum |actual| {e['actual']:,.2f} USD, sum |unexplained| {e['unexpl']:,.2f} USD")
        check(e["unexpl"] <= 0.05 * e["actual"] + 1.0, "P&L explain leaves under 5% unexplained")
    if c1.get("vares", 0):
        v = q("rdb", "exec last var99, last kupiec from vares where method=`fhs")
        print(f"  INFO  VaR 99% (FHS) {v['var99']:,.0f} USD, backtest Kupiec p = {v['kupiec']:.2f}")
    check(c1.get("vares", 0) == 2, f"VaR / ES published for HS and FHS ({c1.get('vares', 0)} rows)")

    print("\n[3] crash recovery: stop the feed, engine and risk, kill the RDB, restart it")
    sh("stop.sh", "risk", "engine", "feed")
    time.sleep(2)
    before = counts()
    sh("stop.sh", "rdb")
    sh("start.sh", "rdb")
    time.sleep(3)
    after = counts()
    check(after == before, f"RDB rebuilt every row from the tickerplant log ({sum(before.values()):,} rows)")

    print("\n[4] surface engine, poll mode (reads new RDB rows)")
    ENV["ENGINE_MODE"] = "poll"
    sh("start.sh", "feed", "engine")
    time.sleep(25)
    sh("stop.sh", "engine", "feed")
    time.sleep(2)
    now = counts()
    check(now["iv"] - before["iv"] > 500, f"poll mode published implied vols ({now['iv'] - before['iv']:,} new rows)")
    before = now

    print("\n[5] gateway")
    gq = q("gw", "{count .gw.get[`quote;.z.D;.z.D;`]}", None)
    check(gq == before["quote"], f"gateway returns all of today's quotes ({gq:,})")
    nsym = q("rdb", "count distinct quote`sym")
    latest = q("gw", "{count .gw.latest[`quote]}", None)
    check(latest == nsym, f"gateway latest quote per instrument ({latest} instruments)")

    print("\n[6] end of day")
    day = q("rdb", ".z.D")
    q("tp", ".u.endofday[]")
    time.sleep(5)
    hdb_quotes = q("hdb", "{count select from quote where date=x}", day)
    check(hdb_quotes == before["quote"], f"HDB holds {hdb_quotes:,} quotes for {day}")
    hdb_surf = q("hdb", "{count select from surface where date=x}", day)
    check(hdb_surf == before["surface"], f"HDB holds {hdb_surf:,} surface rows for {day}")
    hdb_pos = q("hdb", "{count select from pos where date=x}", day)
    check(hdb_pos == before["pos"], f"HDB holds {hdb_pos} positions for {day}")
    check(sum(counts().values()) == 0, "RDB empty after end of day")
    check((data / "hdb" / str(day).replace("-", ".") / "quote").is_dir(), "date folder written to disk")

    sh("stop.sh")
    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        show_logs()
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("\nTest aborted with an error. Last lines of each process log:")
        show_logs()
        sh("stop.sh")
        raise
