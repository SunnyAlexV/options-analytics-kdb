"""Per-minute data-quality monitor.

Watches every quote and trade as it passes and, once a minute, emits one
``dq`` row: counts, crossed and one-sided quotes, how many instruments
updated, and the median and worst latency (our receive time minus Deribit's
timestamp). Latency figures stay private under the KDB-X licence, but
measuring them is still how we find problems.
"""
from __future__ import annotations

import statistics

MS = 1_000_000


class QualityMonitor:
    def __init__(self, asset: str):
        self.asset = asset
        self._reset(minute=None)

    def _reset(self, minute: int | None) -> None:
        self.minute = minute
        self.quotes = self.trades = self.crossed = self.onesided = 0
        self.syms: set[str] = set()
        self.lat_ms: list[float] = []

    def _roll(self, recv: int) -> list[dict]:
        """If a new minute has started, close the old one and return its dq row."""
        minute = recv // (60 * 1_000_000_000)
        out = []
        if self.minute is not None and minute != self.minute:
            out.append(self.row())
        if minute != self.minute:
            self._reset(minute)
        return out

    def on_quote(self, q: dict) -> list[dict]:
        out = self._roll(q["recv"])
        self.quotes += 1
        self.syms.add(q["sym"])
        self.lat_ms.append((q["recv"] - q["time"]) / MS)
        if q["bid"] is None or q["ask"] is None:
            self.onesided += 1
        elif q["bid"] >= q["ask"]:
            self.crossed += 1          # bid at or above ask: should never persist
        return out

    def on_trade(self, t: dict) -> list[dict]:
        out = self._roll(t["recv"])
        self.trades += 1
        return out

    def row(self) -> dict:
        lat = self.lat_ms
        return {
            "time": self.minute * 60 * 1_000_000_000, "asset": self.asset,
            "quotes": self.quotes, "trades": self.trades,
            "crossed": self.crossed, "onesided": self.onesided,
            "symsupdated": len(self.syms),
            "latmed": statistics.median(lat) if lat else None,
            "latmax": max(lat) if lat else None,
        }
