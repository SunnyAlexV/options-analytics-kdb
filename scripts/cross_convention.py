"""Cross-convention check: the same coin's smile from two independent Deribit books.

Deribit lists BTC and ETH options twice:
  BTC-30OCT26-...        inverse: premium in BTC, V = D * Black76(F) / F, coin-margined
  BTC_USDC-30OCT26-...   linear:  premium in USDC, V = D * Black76(F),  USDC-margined
Different order books, different market makers' quotes, different premium conventions,
different contract sizes and ticks. If the engine treats each convention correctly, the
two books must give the same forward and the same smile for each expiry, up to noise and
a small basis (the two margin currencies). A convention bug (dividing by F twice, the
wrong discount factor) shows up here at once as a gap of many vol points or many bp.

Usage
  python scripts/cross_convention.py --rec ~/rec_ETH ~/rec_USDC           # recordings, merged
  python scripts/cross_convention.py --gw 5013 --date 2026.10.09          # from kdb+
  options: --pairs BTC ETH   --bucket 60   (seconds per comparison point)

Output: for each coin and expiry, medians over time of ATM vol, 25-delta risk reversal and
butterfly from each book, the median paired difference and how often |difference| stayed
under 1 vol point, and the forward gap in bp.
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.replay import load_from_gateway, load_recording, replay  # noqa: E402


def collect(data, bucket_ns: int):
    """(asset, expiry label, time bucket) -> last surf row and last forward in that bucket."""
    surf, fwd = {}, {}

    def on_out(now, ivs, fw, sf, eng):
        b = now // bucket_ns
        for r in sf:
            surf[(r["asset"], r["sym"].split("-", 1)[1], b)] = r
        for r in fw:
            if r["F"] == r["F"]:
                fwd[(r["asset"], r["sym"].split("-", 1)[1], b)] = r["F"]

    replay(data, on_output=on_out)
    return surf, fwd


def compare(surf, fwd, coin: str) -> list[dict]:
    lin = f"{coin}_USDC"
    out = []
    labels = sorted({k[1] for k in surf if k[0] == coin} & {k[1] for k in surf if k[0] == lin})
    for lab in labels:
        buckets = sorted({k[2] for k in surf if k[:2] == (coin, lab)} & {k[2] for k in surf if k[:2] == (lin, lab)})
        if not buckets:
            continue
        a = [surf[(coin, lab, b)] for b in buckets]
        c = [surf[(lin, lab, b)] for b in buckets]
        row = {"coin": coin, "expiry": lab, "days": np.median([r["T"] for r in a]) * 365, "n": len(buckets)}
        for m in ("atmvol", "rr25", "bf25"):
            x = np.array([r[m] for r in a]) * 100
            y = np.array([r[m] for r in c]) * 100
            ok = np.isfinite(x) & np.isfinite(y)
            d = (x - y)[ok]
            row[m] = (np.median(x[ok]) if ok.any() else np.nan, np.median(y[ok]) if ok.any() else np.nan,
                      np.median(d) if d.size else np.nan, np.mean(np.abs(d) < 1.0) if d.size else np.nan)
        g = [(fwd[(coin, lab, b)] / fwd[(lin, lab, b)] - 1) * 1e4 for b in buckets
             if (coin, lab, b) in fwd and (lin, lab, b) in fwd]
        row["fgap"] = np.median(g) if g else np.nan
        out.append(row)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rec", nargs="*", help="recording folders (merged)")
    ap.add_argument("--gw", type=int, help="gateway port")
    ap.add_argument("--date", help="YYYY.MM.DD (with --gw); --end defaults to it")
    ap.add_argument("--end")
    ap.add_argument("--pairs", nargs="*", default=["BTC", "ETH"])
    ap.add_argument("--bucket", type=float, default=60.0, help="seconds per comparison point")
    a = ap.parse_args(argv)
    if a.rec:
        data = {"ref": [], "snap": [], "quote": []}
        for f in a.rec:
            for t, rows in load_recording(f).items():
                data[t] += rows
    elif a.gw:
        data = load_from_gateway(a.gw, a.date, a.end or a.date)
    else:
        ap.error("give --rec folders or --gw port --date")
    surf, fwd = collect(data, int(a.bucket * 1e9))
    rows = sorted((r for c in a.pairs for r in compare(surf, fwd, c)), key=lambda r: (r["coin"], r["days"]))
    if not rows:
        print("no expiry is fitted in both books: does the data hold both the coin and <coin>_USDC?")
        return 1
    print(f"{'expiry':16s} {'days':>5s} {'pts':>4s} | {'ATM inv':>7s} {'lin':>6s} {'diff':>6s} {'<1vp':>5s} | "
          f"{'RR inv':>6s} {'lin':>6s} {'diff':>6s} {'<1vp':>5s} | {'BF inv':>6s} {'lin':>6s} {'diff':>6s} | {'F gap':>7s}")
    for r in rows:
        at, rr, bf = r["atmvol"], r["rr25"], r["bf25"]
        print(f"{r['coin'] + '-' + r['expiry']:16s} {r['days']:5.1f} {r['n']:4d} | {at[0]:7.2f} {at[1]:6.2f} {at[2]:+6.2f} {at[3]:5.0%} | "
              f"{rr[0]:+6.2f} {rr[1]:+6.2f} {rr[2]:+6.2f} {rr[3]:5.0%} | {bf[0]:+6.2f} {bf[1]:+6.2f} {bf[2]:+6.2f} | {r['fgap']:+6.1f}bp")
    d = np.array([r["atmvol"][2] for r in rows])
    w = np.array([r["n"] for r in rows])
    ok = np.isfinite(d)
    print(f"\nATM vol, inverse minus linear: median {np.median(d[ok]):+.2f} vol pts, "
          f"point-weighted mean |diff| {np.average(np.abs(d[ok]), weights=w[ok]):.2f} vol pts over {ok.sum()} expiries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
