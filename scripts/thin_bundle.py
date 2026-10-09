"""Thin a demo bundle for the Streamlit Community Cloud demo (committed to GitHub).

    python scripts/thin_bundle.py demo/bundle streamlit_app/bundle --every 60

Busy per-instrument tables (quote, iv, snap, fwd, surface) keep the last row per instrument per
`--every` seconds, exactly like dashboard.bundle.downsample. The risk table is thinned by whole
snapshots: one complete set of rows (every bucket) per `--every` seconds, so "risk at time t" is
never a mix of two moments. Everything else is small and kept as is. Files are written with
zstd compression.
"""
import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.bundle import HEAVY, downsample, load  # noqa: E402


def thin_snapshots(df: pd.DataFrame, every_s: float) -> pd.DataFrame:
    """Keep every row stamped at the first snapshot time in each bucket."""
    if df.empty:
        return df
    times = pd.Series(df["time"].unique()).sort_values()
    keep = times.groupby(times.dt.floor(f"{int(every_s)}s")).first()
    return df[df["time"].isin(set(keep))].reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--every", type=float, default=60.0, help="seconds per kept row (per instrument)")
    a = ap.parse_args(argv)
    src, dst = Path(a.src), Path(a.dst)
    T = load(src)
    dst.mkdir(parents=True, exist_ok=True)
    before = after = 0
    for t, df in T.items():
        n0 = len(df)
        if t in HEAVY:
            df = downsample(df, a.every)
        elif t == "risk":
            df = thin_snapshots(df, a.every)
        df.to_parquet(dst / f"{t}.parquet", index=False, compression="zstd")
        s0, s1 = (src / f"{t}.parquet").stat().st_size, (dst / f"{t}.parquet").stat().st_size
        before, after = before + s0, after + s1
        print(f"{t:8s} {n0:>9,} -> {len(df):>9,} rows   {s0 / 1e6:6.2f} -> {s1 / 1e6:6.2f} MB")
    if (src / "history.csv").exists():
        shutil.copy(src / "history.csv", dst / "history.csv")
    print(f"total {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB in {dst}")


if __name__ == "__main__":
    main()
