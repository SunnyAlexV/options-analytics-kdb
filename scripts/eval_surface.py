"""Run the evaluation harness: score engine design choices on recorded data.

    python scripts/eval_surface.py --recording ~/kdbdata/raw          # a feed recording
    python scripts/eval_surface.py --gw 5013 --date 2026.10.09         # a day in our kdb+ system
    python scripts/eval_surface.py --recording ~/kdbdata/raw --minutes 20

Each configuration changes ONE thing from the baseline, so differences are attributable.
"""
import argparse
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.core import EngineConfig  # noqa: E402
from engine.harness import evaluate, print_table, score_deribit_marks  # noqa: E402
from engine.replay import load_from_gateway, load_recording  # noqa: E402
from engine.smile import SliceConfig  # noqa: E402


def configs():
    base = EngineConfig()
    yield "baseline (current defaults)", base
    yield "+ quote-size weights", dataclasses.replace(base, slice=SliceConfig(size_weight=True))
    yield "Deribit forward only", dataclasses.replace(base, forward_source="deribit")
    yield "parity fwd, gate 0.1% (looser)", dataclasses.replace(base, fwd_max_rel_se=1e-3)
    yield "parity fwd always (no gate)", dataclasses.replace(base, fwd_max_rel_se=1.0)
    yield "parity always + size weights", dataclasses.replace(base, fwd_max_rel_se=1.0,
                                                               slice=SliceConfig(size_weight=True))
    yield "OTM options only (not best side)", dataclasses.replace(base, slice=SliceConfig(side="otm"))
    yield "refit every 2 s", dataclasses.replace(base, throttle_s=2.0)
    yield "refit every 0.1 s", dataclasses.replace(base, throttle_s=0.1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recording", help="feed recording folder (python -m feed --record)")
    ap.add_argument("--gw", type=int, help="gateway port")
    ap.add_argument("--date", help="date for --gw, YYYY.MM.DD")
    ap.add_argument("--start", type=float, default=0, help="skip the first N minutes")
    ap.add_argument("--minutes", type=float, default=0, help="then use only N minutes")
    ap.add_argument("--horizon", type=float, default=10.0, help="prediction horizon, seconds")
    args = ap.parse_args()

    data = load_recording(args.recording) if args.recording else load_from_gateway(args.gw, args.date, args.date)
    if args.start or args.minutes:
        t0 = min(r["recv"] for r in data["quote"]) + args.start * 60e9
        cut = t0 + args.minutes * 60e9 if args.minutes else float("inf")
        # reference data is kept whole (it describes the instruments, not the market)
        data = {t: rows if t == "ref" else [r for r in rows if t0 <= r.get("recv", 0) < cut]
                for t, rows in data.items()}
    span = (max(r["recv"] for r in data["quote"]) - min(r["recv"] for r in data["quote"])) / 60e9
    print(f"Data: {len(data['quote']):,} quotes, {len(data['snap']):,} snapshots, "
          f"{len(data['ref'])} instruments over {span:.1f} minutes")

    scores = [score_deribit_marks(data, args.horizon)]
    for name, cfg in configs():
        scores += evaluate(data, cfg, name, args.horizon)
    print_table(scores)


if __name__ == "__main__":
    main()
