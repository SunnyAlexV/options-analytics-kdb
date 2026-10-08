"""Deribit feed handler: live websocket data -> normalised, batched rows -> a sink.

Four jobs run side by side (asyncio tasks):

1. ``_connection_loop``  keep a websocket open; subscribe; route each message.
                         Reconnects with exponential backoff and logs every
                         outage to the ``gap`` table.
2. ``_flush_loop``       every ``batch_ms``, hand the buffered rows to the sink.
                         q inserts a batch of 300 rows far faster than 300
                         single rows, so batching matters once the sink is q.
3. ``_snapshot_loop``    every ``snap_every`` seconds, take a REST snapshot of
                         Deribit's marks, forwards and open interest (``snap``).
4. ``_ref_loop``         at start-up and hourly, refresh the instrument list
                         (``ref``): subscribe to new listings, drop expired ones.

Channels (rates measured 8 Oct 2026, ~950 BTC options):
- ``book.{inst}.none.1.100ms``    top of book, sent only when it changes   ~340 rows/s
- ``trades.option.BTC.100ms``     every option trade                       ~0.1 rows/s
- ``deribit_price_index.btc_usd`` spot index (table "spot")              ~1 row/s
We deliberately do NOT use ``ticker.{inst}.100ms``: it re-sends every
instrument whenever the index moves (~1,000 msgs/s, ~770 KB/s) because
Deribit's marks and Greeks change, even when no quote has changed.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict

import requests
import websockets

from . import normalise as N
from .quality import QualityMonitor

WS_URL = "wss://www.deribit.com/ws/api/v2"
REST_URL = "https://www.deribit.com/api/v2/public"

HEARTBEAT_S = 10      # Deribit sends a heartbeat at this interval...
SILENCE_S = 30        # ...so this long with no message at all means the link is dead
BACKOFF_MAX_S = 30    # longest wait between reconnect attempts
SUB_CHUNK = 100       # channels per subscribe request


def now_ns() -> int:
    return time.time_ns()


class DeribitFeed:
    def __init__(self, sink, currency: str = "BTC", batch_ms: int = 100,
                 snap_every: float = 10.0, ref_every: float = 3600.0):
        self.sink = sink
        self.currency = currency
        self.batch_s = batch_ms / 1000
        self.snap_every = snap_every
        self.ref_every = ref_every

        self.buffers: dict[str, list[dict]] = defaultdict(list)
        self.quality = QualityMonitor(currency)
        self.http = requests.Session()

        self.instruments: set[str] = set()   # active options we want quotes for
        self.ws = None                       # current websocket, None while disconnected
        self.last_msg_ns: int | None = None  # when we last heard from Deribit
        self.msg_id = 0

    # ------------------------------------------------------------------ public
    async def run(self, seconds: float | None = None) -> None:
        """Run until cancelled (Ctrl+C) or for ``seconds`` if given."""
        await self._refresh_instruments()          # need the list before subscribing
        tasks = [asyncio.create_task(c) for c in (
            self._connection_loop(), self._flush_loop(),
            self._snapshot_loop(), self._ref_loop())]
        try:
            if seconds:
                await asyncio.sleep(seconds)
            else:
                await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.emit("dq", [self.quality.row()])  # close the last, partial minute
            self._flush()
            self.sink.close()

    # --------------------------------------------------------------- buffering
    def emit(self, table: str, rows: list[dict]) -> None:
        if rows:
            self.buffers[table].extend(rows)

    def _flush(self) -> None:
        for table, rows in self.buffers.items():
            if rows:
                self.sink.publish(table, rows)
        self.buffers = defaultdict(list)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.batch_s)
            self._flush()

    # -------------------------------------------------------------- websocket
    async def _connection_loop(self) -> None:
        backoff = 1.0
        lost_at: int | None = None
        reason = "start"
        while True:
            try:
                async with websockets.connect(WS_URL, max_size=2**24, ping_interval=20) as ws:
                    self.ws = ws
                    await self._on_connect(ws)
                    if lost_at is not None:            # we are back: record the outage
                        self.emit("gap", [{"sym": self.currency, "asset": self.currency,
                                           "start": lost_at, "end": now_ns(), "reason": reason}])
                    backoff = 1.0
                    await self._read_loop(ws)
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                reason = "silent"
            except Exception as e:                    # network drop, server close, ...
                reason = type(e).__name__
            self.ws = None
            lost_at = self.last_msg_ns or now_ns()
            print(f"Feed: connection lost ({reason}); reconnecting in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def _on_connect(self, ws) -> None:
        # Ask Deribit to send a heartbeat every HEARTBEAT_S. If we stop hearing
        # anything for SILENCE_S, _read_loop times out and we reconnect.
        await self._send(ws, "public/set_heartbeat", {"interval": HEARTBEAT_S})
        fixed = [f"trades.option.{self.currency}.100ms",
                 f"deribit_price_index.{self.currency.lower()}_usd"]
        await self._subscribe(ws, fixed + [self._book(s) for s in sorted(self.instruments)])
        print(f"Feed: connected, subscribed to {len(self.instruments)} {self.currency} options")

    async def _read_loop(self, ws) -> None:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=SILENCE_S)
            recv = now_ns()
            self.last_msg_ns = recv
            await self._handle(ws, json.loads(raw), recv)

    async def _handle(self, ws, msg: dict, recv: int) -> None:
        method = msg.get("method")
        if method == "subscription":
            channel = msg["params"]["channel"]
            data = msg["params"]["data"]
            kind = channel.split(".", 1)[0]
            if kind == "book":
                q = N.book_to_quote(data, recv)
                self.emit("quote", [q])
                self.emit("dq", self.quality.on_quote(q))
            elif kind == "trades":
                for t in N.trades_to_rows(data, recv):
                    self.emit("trade", [t])
                    self.emit("dq", self.quality.on_trade(t))
            elif kind == "deribit_price_index":
                self.emit("spot", [N.index_to_row(data, recv)])
        elif method == "heartbeat":
            if msg["params"].get("type") == "test_request":
                await self._send(ws, "public/test", {})   # "yes, I'm still here"
        elif "error" in msg:
            print("Feed: Deribit error:", msg["error"])

    @staticmethod
    def _book(sym: str) -> str:
        return f"book.{sym}.none.1.100ms"

    async def _send(self, ws, method: str, params: dict) -> None:
        self.msg_id += 1
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": self.msg_id,
                                  "method": method, "params": params}))

    async def _subscribe(self, ws, channels: list[str], unsubscribe: bool = False) -> None:
        method = "public/unsubscribe" if unsubscribe else "public/subscribe"
        for i in range(0, len(channels), SUB_CHUNK):
            await self._send(ws, method, {"channels": channels[i:i + SUB_CHUNK]})

    # ------------------------------------------------------------------- REST
    def _get(self, endpoint: str, **params):
        r = self.http.get(f"{REST_URL}/{endpoint}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()["result"]

    async def _refresh_instruments(self) -> None:
        result = await asyncio.to_thread(self._get, "get_instruments",
                                         currency=self.currency, kind="option")
        self.emit("ref", N.instruments_to_ref(result))
        live = {i["instrument_name"] for i in result if i.get("is_active")}
        new, gone = live - self.instruments, self.instruments - live
        self.instruments = live
        if self.ws is not None and (new or gone):   # adjust the live subscription
            if new:
                await self._subscribe(self.ws, [self._book(s) for s in sorted(new)])
            if gone:
                await self._subscribe(self.ws, [self._book(s) for s in sorted(gone)], unsubscribe=True)
            print(f"Feed: instruments refreshed: +{len(new)} new, -{len(gone)} expired")

    async def _ref_loop(self) -> None:
        while True:
            await asyncio.sleep(self.ref_every)
            try:
                await self._refresh_instruments()
            except Exception as e:
                print("Feed: instrument refresh failed:", e)

    async def _snapshot_loop(self) -> None:
        while True:
            try:
                result = await asyncio.to_thread(self._get, "get_book_summary_by_currency",
                                                 currency=self.currency, kind="option")
                self.emit("snap", N.summary_to_rows(result, now_ns()))
            except Exception as e:
                print("Feed: snapshot failed:", e)
            await asyncio.sleep(self.snap_every)
