"""Run the feed handler from the command line.

Examples (from the project folder, env 'oak'):
    python -m feed                          # watch live rates, Ctrl+C to stop
    python -m feed --minutes 2              # watch for 2 minutes
    python -m feed --record --minutes 10    # also record to ~/kdbdata/raw/<date>/
    python -m feed --tp                     # publish to the tickerplant (port 5010)
"""
import argparse
import asyncio
from pathlib import Path

from .deribit import DeribitFeed
from .sinks import FileSink, StatsSink, TickerplantSink


class Tee:
    """Send every batch to several sinks."""

    def __init__(self, *sinks):
        self.sinks = sinks

    def publish(self, table, rows):
        for s in self.sinks:
            s.publish(table, rows)

    def close(self):
        for s in self.sinks:
            s.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Deribit options feed handler")
    ap.add_argument("--currency", default="BTC")
    ap.add_argument("--minutes", type=float, default=0, help="stop after this long (0 = run until Ctrl+C)")
    ap.add_argument("--tp", action="store_true", help="publish to the kdb+ tickerplant")
    ap.add_argument("--tp-host", default="localhost")
    ap.add_argument("--tp-port", type=int, default=5010)
    ap.add_argument("--record", action="store_true", help="also record rows to gzipped JSON lines")
    ap.add_argument("--out", default=str(Path.home() / "kdbdata" / "raw"))
    args = ap.parse_args()

    sinks = [StatsSink(every=30 if args.tp else 5)]
    if args.tp:
        sinks.append(TickerplantSink(args.tp_host, args.tp_port))
    if args.record:
        sinks.append(FileSink(Path(args.out)))

    feed = DeribitFeed(Tee(*sinks), currency=args.currency)
    try:
        asyncio.run(feed.run(seconds=args.minutes * 60 or None))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
