"""Make a bundle for the public demo: a day of every table, as Parquet files.

    python scripts/make_demo_bundle.py --gw 5013 --date 2026.10.09 --out demo/bundle   # from kdb+ (best)
    python scripts/make_demo_bundle.py --recording ~/kdbdata/raw --out demo/bundle     # from a recording
    python scripts/make_demo_bundle.py --recording ~/rec/BTC ~/rec/ETH ~/rec/USDC --out demo/bundle

From kdb+, the bundle holds exactly what the live system published. From a feed recording,
the surface engine and risk calculations are re-run offline on a simulated clock.
The daily index/DVOL history is saved alongside (history.csv for BTC's variance-risk-premium
chart; history_all.csv, every coin's, for the VaR of a recording), so the demo needs no network.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard import bundle  # noqa: E402
from risk.history import fetch_history  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", type=int, help="gateway port")
    ap.add_argument("--date", help="YYYY.MM.DD, with --gw")
    ap.add_argument("--recording", nargs="+", help="feed recording folder(s), e.g. one per group")
    ap.add_argument("--out", default="demo/bundle")
    ap.add_argument("--bucket", type=float, default=30, help="busy tables: keep the last row per sym per N s")
    ap.add_argument("--R", type=float, default=0.0, help="smile rule, for --recording")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    hist = fetch_history("BTC", cache=out / "history.csv", refresh=True)
    if a.recording:
        # every coin's daily index + vol history, saved in the bundle (history_all.csv) for the VaR
        T = bundle.from_recording(a.recording, bucket_s=a.bucket, R=a.R, history_cache=out / "history_all.csv")
    else:
        T = bundle.from_gateway(a.gw, a.date, bucket_s=a.bucket)
    bundle.save(T, out)
    size = sum(f.stat().st_size for f in out.iterdir()) / 1e6
    print(f"Wrote {len(T)} tables + history.csv to {out} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
