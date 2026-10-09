"""Validate our C++ implied vols against Deribit's own mark IVs, live.

    python scripts/validate_deribit.py                # take a fresh REST snapshot
    python scripts/validate_deribit.py --gw 5013      # use the latest `snap` rows in our kdb+ system

For every option: convert Deribit's mark price (BTC) to USD at the expiry's
forward, invert it with our solver, and compare with Deribit's mark_iv.

Conditioning: if one vol point moves an option's price by less than one tick
(0.0001 BTC), the price cannot pin the vol down to a point, and a rounding-sized
price difference becomes a huge vol difference. Those options are reported
separately, not hidden.
"""
import argparse
import os

import numpy as np

import pricing as px

TICK_BTC = 1e-4


def from_rest(currency="BTC"):
    import requests
    r = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                     params={"currency": currency, "kind": "option"}, timeout=15).json()["result"]
    return {"sym": [x["instrument_name"] for x in r],
            "t": np.array([x["creation_timestamp"] for x in r], dtype=float) * 1e6,
            "und": np.array([x["underlying_price"] for x in r], dtype=float),
            "mark": np.array([x["mark_price"] for x in r], dtype=float),
            "markiv": np.array([x["mark_iv"] for x in r], dtype=float)}


def from_gateway(port):
    os.environ.setdefault("PYKX_UNLICENSED", "true")
    import pykx as kx
    with kx.SyncQConnection(port=port, no_ctx=True) as c:
        t = c("{0!.gw.latest[`snap]}", None).pd()
    return {"sym": list(t["sym"]), "t": t["exch"].astype("int64").to_numpy(dtype=float),
            "und": t["und"].to_numpy(float), "mark": t["mark"].to_numpy(float),
            "markiv": t["markiv"].to_numpy(float)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", type=int, help="gateway port: read the latest snap rows from kdb+")
    args = ap.parse_args()
    d = from_gateway(args.gw) if args.gw else from_rest()

    sym = d["sym"]
    F, mark, miv = d["und"], d["mark"], d["markiv"] / 100
    K = np.array([float(s.split("-")[2]) for s in sym])
    cp = np.array([s[-1] for s in sym])
    T = px.year_fraction(d["t"], np.array([px.deribit_expiry_ns(s) for s in sym]))
    live = np.isfinite(F) & np.isfinite(mark) & np.isfinite(miv) & (T > 0)

    iv, st, it = px.implied_vol(px.from_btc(mark, F), F, K, T, cp, full=True)
    vega_per_pt_btc = px.greeks(F, K, T, np.where(live, miv, 0.5), cp)["vega"] / 100 / F
    good = live & (vega_per_pt_btc >= TICK_BTC)
    solved = good & (st == 0)
    err_bp = np.abs(iv - miv) * 1e4

    print(f"Source: {'kdb+ gateway' if args.gw else 'Deribit REST'} | options: {len(sym)}")
    print(f"Price check: |Black-76(mark_iv)/F - mark| median "
          f"{np.median(np.abs(px.to_btc(px.price(F, K, T, np.where(live, miv, 0.5), cp), F) - mark)[live]):.2e} BTC")
    print(f"Well-conditioned (1 vol pt >= 1 tick): {good.sum()}  solved: {solved.sum()}")
    print(f"  |our IV - Deribit mark_iv|  median {np.median(err_bp[solved]):.2f} bp   "
          f"95th pct {np.percentile(err_bp[solved], 95):.2f} bp   max {err_bp[solved].max():.2f} bp")
    print(f"  solver iterations           median {np.median(it[solved]):.0f}   max {it[solved].max()}")
    bad = live & ~good
    print(f"Ill-conditioned (excluded): {bad.sum()}  -- median days to expiry "
          f"{np.median(T[bad]) * 365 if bad.any() else float('nan'):.1f}")
    print("(Deribit rounds mark_iv to 0.01 vol points = 1 bp, so ~1 bp is the agreement floor.)")

    # Where the remaining differences come from: if Deribit's mark and the forward it
    # reports were computed a moment apart, every option would imply the same small
    # shift in the forward: dF = (mark_usd - BlackScholes(mark_iv)) / delta.
    g = px.greeks(F, K, T, np.where(live, miv, 0.5), cp)
    sens = live & (np.abs(g["delta"]) > 0.3)
    dF = (px.from_btc(mark, F) - g["price"])[sens] / g["delta"][sens]
    q = np.percentile(dF, [10, 50, 90])
    print(f"Implied forward shift explaining the gaps (|delta|>0.3, n={sens.sum()}): "
          f"median {q[1]:+.2f} USD, 10-90% [{q[0]:+.2f}, {q[2]:+.2f}] on F ~ {np.median(F[live]):,.0f}")


if __name__ == "__main__":
    main()
