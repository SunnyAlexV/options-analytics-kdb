"""Where normalised rows go. The feed handler doesn't care which sink it has.

- ``StatsSink``  prints rows/second per table every few seconds (for watching)
- ``FileSink``   records every row to gzipped JSON-lines files (for replay and tests)
- ``TickerplantSink`` publishes every batch to the kdb+ tickerplant

Every sink has the same two methods: ``publish(table, rows)`` and ``close()``.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import time
from collections import Counter
from pathlib import Path


class StatsSink:
    def __init__(self, every: float = 5.0):
        self.every = every
        self.counts: Counter = Counter()
        self.last: dict[str, dict] = {}
        self.t0 = time.monotonic()

    def publish(self, table: str, rows: list[dict]) -> None:
        self.counts[table] += len(rows)
        self.last[table] = rows[-1]
        if table in ("dq", "gap"):
            for r in rows:
                print(f"  [{table}] {r}")
        elapsed = time.monotonic() - self.t0
        if elapsed >= self.every:
            rates = "  ".join(f"{t}={n / elapsed:,.0f}/s" for t, n in sorted(self.counts.items()))
            print(f"{dt.datetime.now(dt.timezone.utc):%H:%M:%S}Z  {rates}")
            self.counts.clear()
            self.t0 = time.monotonic()

    def close(self) -> None:
        pass


class TickerplantSink:
    """Publishes each batch to KX's tickerplant by calling ``.u.upd[table; columns]``.

    - ``columns`` is a list of q vectors, one per feed column in schema order.
      The tickerplant sees that the first column is not a timespan and puts its
      own ``time`` column in front (kdb+tick's standard behaviour).
    - Messages are sent **asynchronously** (``wait=False``): we don't wait for
      q to reply, so a slow subscriber can never stall the feed.
    - If the tickerplant is down, batches are held in memory (up to
      ``max_pending`` rows) and sent when it comes back, so a tickerplant
      restart loses nothing unless the outage is very long.
    """

    def __init__(self, host: str = "localhost", port: int = 5010, max_pending: int = 2_000_000):
        import os
        os.environ.setdefault("PYKX_UNLICENSED", "true")   # IPC needs no licence
        import pykx as kx
        self.kx = kx
        self.host, self.port = host, port
        self.max_pending = max_pending
        self.pending: list[tuple[str, list[dict]]] = []
        self.pending_rows = 0
        self.dropped = 0
        self.conn = None
        self.warned = False
        self.next_try = 0.0                      # don't retry a dead tickerplant more than every 2 s
        self.sent: Counter = Counter()

    def _connect(self) -> bool:
        if time.monotonic() < self.next_try:
            return False
        self.next_try = time.monotonic() + 2.0
        try:
            self.conn = self.kx.SyncQConnection(host=self.host, port=self.port, no_ctx=True)
            print(f"TickerplantSink: connected to {self.host}:{self.port}")
            self.warned = False
            return True
        except Exception as e:
            if not self.warned:
                print(f"TickerplantSink: tickerplant not reachable ({type(e).__name__}); "
                      "buffering and retrying every 2 s")
                self.warned = True
            self.conn = None
            return False

    def publish(self, table: str, rows: list[dict]) -> None:
        self.pending.append((table, rows))
        self.pending_rows += len(rows)
        while self.pending_rows > self.max_pending:            # outage too long: drop oldest
            _, old = self.pending.pop(0)
            self.pending_rows -= len(old)
            self.dropped += len(old)
        if self.conn is None and not self._connect():
            return
        try:
            while self.pending:
                t, r = self.pending[0]
                cols = self.kx.toq(to_q_columns(self.kx, t, r))
                self.conn(".u.upd", self.kx.SymbolAtom(t), cols, wait=False)
                self.pending.pop(0)
                self.pending_rows -= len(r)
                self.sent[t] += len(r)
        except Exception as e:
            print(f"TickerplantSink: send failed ({type(e).__name__}); will retry")
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
        print("Sent to tickerplant:", dict(self.sent),
              f"| still pending: {self.pending_rows} | dropped: {self.dropped}")


def to_q_columns(kx, table: str, rows: list[dict]) -> list:
    """Rows (list of dicts) -> list of typed q vectors, one per feed column.

    Nulls: float None -> NaN (q 0n); timestamp/long None -> int64 min (q 0Np / 0N);
    symbol None -> empty symbol (q `).
    """
    import numpy as np
    from .schema import FEED_SCHEMAS
    NULL_J = np.iinfo(np.int64).min
    out = []
    for col, typ in FEED_SCHEMAS[table]:
        vals = [r[col] for r in rows]
        if typ == "s":
            out.append(kx.toq(np.array(["" if v is None else v for v in vals], dtype=object),
                              ktype=kx.SymbolVector))
        elif typ == "f":
            out.append(kx.toq(np.array(vals, dtype=float)))
        elif typ == "p":
            arr = np.array([NULL_J if v is None else v for v in vals], dtype=np.int64)
            out.append(kx.toq(arr.view("datetime64[ns]")))
        elif typ == "j":
            out.append(kx.toq(np.array([NULL_J if v is None else v for v in vals], dtype=np.int64)))
        elif typ == "b":
            out.append(kx.toq(np.array(vals, dtype=bool)))
        else:
            raise ValueError(f"{table}.{col}: unsupported type {typ}")
    return out


class FileSink:
    """Appends rows to ``<root>/<YYYY.MM.DD>/<table>.jsonl.gz``, one file per table per UTC day."""

    FLUSH_S = 5.0   # flush to disk this often: recordings are readable while the feed runs, and survive crashes

    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self.files: dict[tuple[str, str], gzip.GzipFile] = {}
        self.counts: Counter = Counter()
        self.last_flush = time.monotonic()

    def _file(self, table: str):
        day = dt.datetime.now(dt.timezone.utc).strftime("%Y.%m.%d")
        key = (day, table)
        if key not in self.files:
            for old in [k for k in self.files if k[1] == table]:   # day rolled over
                self.files.pop(old).close()
            path = self.root / day / f"{table}.jsonl.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.files[key] = gzip.open(path, "at", encoding="utf-8")
        return self.files[key]

    def publish(self, table: str, rows: list[dict]) -> None:
        f = self._file(table)
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
        self.counts[table] += len(rows)
        if time.monotonic() - self.last_flush >= self.FLUSH_S:
            for fh in self.files.values():
                fh.flush()            # gzip sync-flush: everything so far becomes readable
            self.last_flush = time.monotonic()

    def close(self) -> None:
        for f in self.files.values():
            f.close()
        print("Recorded:", dict(self.counts), "->", self.root)
