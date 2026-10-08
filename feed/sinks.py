"""Where normalised rows go. The feed handler doesn't care which sink it has.

- ``StatsSink``  prints rows/second per table every few seconds (for watching)
- ``FileSink``   records every row to gzipped JSON-lines files (for replay and tests)
- Phase 2 adds ``TickerplantSink``, which publishes to q.

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


class FileSink:
    """Appends rows to ``<root>/<YYYY.MM.DD>/<table>.jsonl.gz``, one file per table per UTC day."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self.files: dict[tuple[str, str], gzip.GzipFile] = {}
        self.counts: Counter = Counter()

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

    def close(self) -> None:
        for f in self.files.values():
            f.close()
        print("Recorded:", dict(self.counts), "->", self.root)
