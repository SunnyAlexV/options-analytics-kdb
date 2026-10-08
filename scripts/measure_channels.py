"""Measure the message rate of a Deribit websocket channel across all options.

This is the measurement behind the quote-source decision in PLAN.md.

Usage (env 'oak'):
    python scripts/measure_channels.py "ticker.{i}.100ms" 20
    python scripts/measure_channels.py "book.{i}.none.1.100ms" 20
    python scripts/measure_channels.py "trades.option.BTC.100ms" 30

``{i}`` is replaced by every active BTC option; patterns without ``{i}`` are
subscribed once. The second argument is how many seconds to measure for.
"""
import asyncio
import json
import sys
import time

import requests
import websockets

URL = "wss://www.deribit.com/ws/api/v2"


async def measure(pattern: str, secs: float, currency: str = "BTC") -> None:
    inst = [x["instrument_name"] for x in requests.get(
        "https://www.deribit.com/api/v2/public/get_instruments",
        params={"currency": currency, "kind": "option"}, timeout=15).json()["result"]]
    chans = [pattern.format(i=i) for i in inst] if "{i}" in pattern else [pattern]

    async with websockets.connect(URL, max_size=2**24) as ws:
        for k in range(0, len(chans), 100):
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": k, "method": "public/subscribe",
                                      "params": {"channels": chans[k:k + 100]}}))
        msgs = rows = nbytes = 0
        t0 = time.time()
        while time.time() - t0 < secs:
            try:
                m = await asyncio.wait_for(ws.recv(), timeout=1)
            except asyncio.TimeoutError:
                continue
            d = json.loads(m)
            if d.get("method") == "subscription":
                data = d["params"]["data"]
                msgs += 1
                nbytes += len(m)
                rows += len(data) if isinstance(data, list) else 1

    print(f"{pattern}  ({len(inst)} {currency} options, {secs:.0f}s)")
    print(f"  {msgs / secs:,.0f} messages/s   {rows / secs:,.0f} rows/s   {nbytes / secs / 1024:,.0f} KB/s")


if __name__ == "__main__":
    asyncio.run(measure(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 20))
