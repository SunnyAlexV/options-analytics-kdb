"""Take one snapshot of the Deribit BTC option chain and save it as CSV for q.

Usage (inside WSL, env 'oak'):
    python scripts/snapshot.py                 # writes ~/kdbdata/btc_snap.csv
    python scripts/snapshot.py --out my.csv

This is a teaching helper for the first q session; the real feed handler
(Phase 1) will stream data into the tickerplant instead of writing files.
"""
import argparse
import csv
import datetime as dt
from pathlib import Path

import requests

URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"

# Output columns, in the order q will read them, and the Deribit field behind each.
# q type string for this layout: "PSFFFFFFFF"
COLUMNS = [
    ("time",   None),                        # P  timestamp (UTC), when Deribit created the snapshot
    ("sym",    "instrument_name"),           # S  e.g. BTC-27NOV26-88000-C
    ("bid",    "bid_price"),                 # F  best bid, in BTC (blank if no bid)
    ("ask",    "ask_price"),                 # F  best ask, in BTC
    ("mark",   "mark_price"),                # F  Deribit's fair-value mark, in BTC
    ("markiv", "mark_iv"),                   # F  Deribit's mark implied vol, in vol points (45.2 = 45.2%)
    ("und",    "underlying_price"),          # F  forward/future price for this expiry, USD
    ("idx",    "estimated_delivery_price"),  # F  BTC index (spot) price, USD
    ("oi",     "open_interest"),             # F  open interest, in contracts (1 contract = 1 BTC)
    ("vol",    "volume"),                    # F  24h volume, in contracts
]


def q_timestamp(ms: int) -> str:
    """Epoch milliseconds -> q timestamp text, e.g. 2026.10.08D17:12:04.680000000."""
    t = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    return t.strftime("%Y.%m.%dD%H:%M:%S.") + f"{t.microsecond:06d}000"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--currency", default="BTC")
    ap.add_argument("--out", default=str(Path.home() / "kdbdata" / "btc_snap.csv"))
    args = ap.parse_args()

    r = requests.get(URL, params={"currency": args.currency, "kind": "option"}, timeout=15)
    r.raise_for_status()
    rows = r.json()["result"]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([name for name, _ in COLUMNS])
        for row in rows:
            line = [q_timestamp(row["creation_timestamp"])]
            for _, field in COLUMNS[1:]:
                v = row.get(field)
                line.append("" if v is None else v)  # blank -> q reads it as null
            w.writerow(line)

    print(f"Saved {len(rows)} {args.currency} options to {out}")


if __name__ == "__main__":
    main()
