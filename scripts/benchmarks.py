"""Benchmarks: how fast each part of the system is, on this machine. Writes results/benchmarks.md.

    python scripts/benchmarks.py                                        # pricing, fitting, risk
    python scripts/benchmarks.py --recording ~/rec/BTC ~/rec/ETH ~/rec/USDC   # + engine replay throughput

Every workload is synthetic and seeded (or a recording you pass in), so a rerun measures the same
work. Each time is the best of several runs (the least-disturbed measurement of the code itself),
and the machine it ran on is recorded at the top of the report. No kdb+ is involved, so the
numbers are fine to publish. Timings that involve kdb+ (e.g. feed -> tickerplant delay) are not
published under the KDB-X licence; scripts/system_test.py prints them locally.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pricing as px  # noqa: E402
from pricing import _core  # noqa: E402
from pricing import reference as ref  # noqa: E402

NS = 1_000_000_000


def best_of(fn, repeat=5):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def us(x):
    return f"{x * 1e6:,.2f} µs" if x < 1e-3 else f"{x * 1e3:,.2f} ms" if x < 1 else f"{x:,.2f} s"


def machine() -> dict:
    cpu = platform.processor() or "?"
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {"cpu": cpu, "cores": os.cpu_count(), "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(), "numpy": np.__version__}


# --------------------------------------------------------------------------- 1. pricing
def bench_pricing(N=1_000):
    rng = np.random.default_rng(42)
    F = np.full(N, 80_000.0)
    K = F * np.exp(rng.uniform(-1.0, 1.0, N))
    T = rng.uniform(1 / 365, 1.5, N)
    S = rng.uniform(0.3, 1.2, N)
    C = rng.random(N) < 0.5
    P = px.price(F, K, T, S, C)
    iv = px.implied_vol(P, F, K, T, C)
    ok = np.isfinite(iv)
    e = np.abs(iv[ok] - S[ok])
    z = np.abs(np.log(K / F)) / (S * np.sqrt(T))             # distance from the money, in standard deviations
    err = (float(np.median(e)), float(e.max()), float(z[~ok].min()) if (~ok).any() else np.nan,
           int((e > 1e-8).sum()), float(z[ok][e > 1e-8].min()) if (e > 1e-8).any() else np.nan)
    rows = [
        ("Black-76 price", best_of(lambda: px.price(F, K, T, S, C), 20) / N,
         "NumPy vectorised", best_of(lambda: ref.price(F, K, T, S, C), 20) / N),
        ("implied vol (safeguarded Newton)", best_of(lambda: px.implied_vol(P, F, K, T, C), 20) / N,
         "SciPy brentq, per option", best_of(lambda: [ref.implied_vol(*a) for a in zip(P, F, K, T, C)], 3) / N),
        ("price + 12 Greeks", best_of(lambda: px.greeks(F, K, T, S, C), 20) / N, "–", np.nan),
    ]
    return rows, N, err, int(ok.sum())


# --------------------------------------------------------------------------- 2. smile fitting
def bench_fit(n_exp=10):
    """One expiry = 33 strikes x calls and puts, from a known SVI smile with a bid-ask; the
    engine's full refit: parity forward, IVs, raw SVI and arbitrage-free SVI."""
    from engine.core import EngineConfig, SurfaceEngine
    from tests.test_engine import synthetic_market
    now = 1_791_500_000 * NS
    ref_rows, snap, quotes, _ = [], [], [], None
    for i in range(n_exp):                                   # n_exp expiries, 7 .. 300 days
        r, s, q, _ = synthetic_market(now, days=7 + 30 * i)
        tag = f"E{i:02d}"
        for row in r:
            row["sym"] = row["sym"].replace("BTC-TEST", f"BTC-{tag}")
        for row in q:
            row["sym"] = row["sym"].replace("BTC-TEST", f"BTC-{tag}")
        s[0]["sym"] = s[0]["sym"].replace("BTC-TEST", f"BTC-{tag}")
        ref_rows += r
        snap += s
        quotes += q

    def run():
        eng = SurfaceEngine(EngineConfig())
        eng.on_ref(ref_rows)
        eng.on_snap(snap)
        eng.on_quotes(quotes)
        eng.refit(now, force=True)
        return eng

    run()                                                   # warm-up
    best, share = float("inf"), float("nan")
    for _ in range(5):
        t0 = time.perf_counter()
        eng = run()
        el = time.perf_counter() - t0
        if el < best:
            best, share = el, eng.stats["fit_s"] / el            # the SVI fits' share of this same run
    return {"expiries": n_exp, "quotes": len(quotes), "total": best, "per_expiry": best / n_exp,
            "fit_share": share}


# --------------------------------------------------------------------------- 3. risk
def bench_risk():
    from risk import var as varmod
    from risk.book import sample_book
    from risk.core import position_greeks, scenario_grid
    from tests.test_risk import market
    m = market()
    book = sample_book(m, hedge_rule_R=0.0)
    rng = np.random.default_rng(0)
    hist = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=400, freq="D"),
                         "spot": 80000 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))),
                         "dvol": 50 * np.exp(np.cumsum(rng.normal(0, 0.03, 400)))})
    out = {"legs": len(book), "opts": sum(1 for r in book if r.get("kind") == "option")}
    out["greeks"] = best_of(lambda: position_greeks(book, m, R=0.0), 10)
    out["scen"] = best_of(lambda: scenario_grid(book, m, R=0.0), 10)
    out["var"] = best_of(lambda: varmod.compute(book, m, hist, R=0.0), 3)
    # scaling: one full revaluation of a 1,000-option book on the same surfaces
    n = 1_000
    labels = list(m.labels)
    j = np.arange(n) % len(labels)
    K = np.round(80_000 * np.exp(rng.uniform(-0.4, 0.4, n)), -2)
    cp = np.where(rng.random(n) < 0.5, "C", "P")
    big = [{"sym": f"{labels[jj]}-{k:.0f}-{c}", "book": "big", "kind": "option", "expiry": int(m.expiry[jj]),
            "strike": float(k), "cp": c, "qty": float(q), "entry": 0.0}
           for jj, k, c, q in zip(j, K, cp, rng.normal(0, 5, n))]
    try:
        out["greeks_1000"] = best_of(lambda: position_greeks(big, m, R=0.0), 5)
        out["scen_1000"] = best_of(lambda: scenario_grid(big, m, R=0.0), 3)
    except Exception as e:                                  # keep the report even if the shape differs
        out["big_error"] = f"{type(e).__name__}: {e}"
    return out


# --------------------------------------------------------------------------- 4. engine replay
def bench_replay(folders):
    from engine.replay import load_recording, replay
    data = {"ref": [], "snap": [], "quote": []}
    for f in folders:
        for t, rows in load_recording(f).items():
            data[t] += rows
    q = data["quote"]
    span = (max(r["recv"] for r in q) - min(r["recv"] for r in q)) / NS
    t0 = time.perf_counter()
    engines = replay(data)
    el = time.perf_counter() - t0
    n_assets = len(engines) if isinstance(engines, dict) else 1
    return {"quotes": len(q), "span_min": span / 60, "seconds": el, "rate": len(q) / el,
            "arrival": len(q) / span, "assets": n_assets}


# --------------------------------------------------------------------------- report
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recording", nargs="*", help="feed recording folder(s) for the replay throughput test")
    ap.add_argument("--out", default=str(ROOT / "results" / "benchmarks.md"))
    a = ap.parse_args(argv)

    mc = machine()
    print("machine:", mc)
    pr, N, err, n_ok = bench_pricing()
    print("pricing done")
    ft = bench_fit()
    print("fitting done")
    rk = bench_risk()
    print("risk done")
    rp = bench_replay(a.recording) if a.recording else None
    if rp:
        print("replay done")

    L = [f"# Benchmarks — {dt.date.today():%d %b %Y}", "",
         f"Machine: {mc['cpu']}, {mc['cores']} logical cores, {mc['os']}; Python {mc['python']}, NumPy {mc['numpy']}.",
         "Reproduce: `python scripts/benchmarks.py"
         + (" --recording " + " ".join(f"<recordings>/{Path(f).name}" for f in a.recording) if a.recording else "")
         + "` (recordings made with `python -m feed --currency <group> --record`). "
         "Each time is the best of several runs. The numbers belong to this machine: rerun the command to measure yours.", "",
         "## 1. Pricing and implied vol (C++ via pybind11)", "",
         f"A synthetic chain of {N:,} options (strikes ±100% in log-moneyness, 1 day to 1.5 years, vols 30–120%), "
         f"per option. The solver returned a vol for {n_ok:,} of {N:,} prices; error against the true vol: median "
         f"{err[0]:.1e}, worst {err[1]:.1e}. Every price it declined is at least {err[2]:.1f} standard deviations "
         f"from the money, where the price is zero or pure intrinsic value to double precision, so no vol can be "
         f"recovered; the {err[3]} solves off by more than 1e-8 are all at least {err[4]:.1f} standard deviations out.", "",
         "| task | C++ per option | baseline | baseline per option | speed-up |", "|---|---|---|---|---|"]
    for task, tc, name, tp in pr:
        L.append(f"| {task} | {us(tc)} | {name} | {us(tp) if np.isfinite(tp) else '–'} | "
                 f"{f'{tp / tc:,.0f}×' if np.isfinite(tp) else '–'} |")
    L += ["", "## 2. Smile fitting (the surface engine's full refit)", "",
          f"{ft['expiries']} expiries (7 to {7 + 30 * (ft['expiries'] - 1)} days), {ft['quotes']:,} quotes, each "
          "expiry: put-call parity forward and discount factor, bid/mid/ask implied vols, raw SVI and "
          "arbitrage-free SVI (butterfly, calendar and Lee constraints).", "",
          "| measure | time |", "|---|---|",
          f"| full refit, all {ft['expiries']} expiries | {us(ft['total'])} |",
          f"| per expiry | {us(ft['per_expiry'])} |",
          f"| share of it spent in the SVI fits | {ft['fit_share']:.0%} |", "",
          "The live engine refits the expiries whose quotes changed, at most every 0.5 s.", "",
          "## 3. Portfolio risk (full revaluation in C++ on the fitted smiles)", "",
          "| task | sample book |" + (" 1,000-option book |" if "greeks_1000" in rk else ""),
          "|---|---|" + ("---|" if "greeks_1000" in rk else ""),
          f"| position Greeks (price, delta, gamma, vega, theta, vanna, volga, smile-rule delta) | {us(rk['greeks'])} |"
          + (f" {us(rk['greeks_1000'])} |" if "greeks_1000" in rk else ""),
          f"| scenario grid: 11 spot moves × 7 vol shifts, each cell a full revaluation | {us(rk['scen'])} |"
          + (f" {us(rk['scen_1000'])} |" if "scen_1000" in rk else ""),
          f"| VaR/ES: 365 historical days × 2 methods (HS, FHS), full revaluation | {us(rk['var'])} |"
          + (" – |" if "greeks_1000" in rk else ""), "",
          f"The sample book is risk/book.py's, built on a three-expiry test market: {rk['opts']} options and "
          f"{rk['legs'] - rk['opts']} futures hedge (two of its target legs map to the same option on this market; the live "
          "book has seven options and a hedge)."]
    if "big_error" in rk:
        L += [f"(1,000-option book not measured: {rk['big_error']})"]
    L += [""]
    if rp:
        L += ["## 4. Surface engine throughput on recorded data", "",
              f"{rp['quotes']:,} recorded Deribit quote updates over {rp['span_min']:.1f} minutes, {rp['assets']} "
              "assets, replayed through fresh engines in one process on a simulated clock (implied vol for every "
              "quote, refits as live).", "",
              "| measure | value |", "|---|---|",
              f"| processed | {rp['rate']:,.0f} quotes/s |",
              f"| arriving (live rate in the recording) | {rp['arrival']:,.0f} quotes/s |",
              f"| headroom | {rp['rate'] / rp['arrival']:.1f}× |", "",
              "Live, each feed group (BTC, ETH, USDC) has its own engine process, so headroom per process is higher.", ""]
    else:
        L += ["## 4. Surface engine throughput", "",
              "Run with `--recording <feed recording folders>` to measure quotes processed per second against "
              "the live arrival rate.", ""]
    L += ["## What is not here", "",
          "No timing that involves kdb+/q (tickerplant, RDB, gateway queries, feed → tickerplant delay) is "
          "published. The project's rule, set with the KDB-X Community licence terms in mind, is to publish such "
          "numbers only with KX's written approval (PLAN.md, Decisions). `python scripts/system_test.py` prints "
          "them locally.", ""]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
