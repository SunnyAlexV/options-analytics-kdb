"""kdb+ tickerplant subscriber, shared by every real-time process (surface engine, risk).

The standard kdb+tick pattern: open a connection, call .u.sub for each table, then receive
every update as an async message (`upd; table; rows), plus (`.u.end; date) at end of day.

PyKX details this module deals with, so the processes don't have to:
  - RawQConnection must be awaited to open, and its event loop must stay open afterwards.
  - Its calls return an asyncio Task (wrapping a QFuture) that only advances while that loop
    runs: subscribe() runs it, with a timeout.
  - While any call is pending, an incoming message is taken as that call's reply, so all
    tables are subscribed in ONE call; nothing can interleave (the tickerplant replies
    before it publishes anything to us).
  - Unlicensed PyKX cannot index q objects (msg[0]) but can iterate them (list(msg)).
  - no_ctx=True: skip PyKX's namespace-introspection query on connect.
"""
from __future__ import annotations

import asyncio
import os

import pandas as pd

os.environ.setdefault("PYKX_UNLICENSED", "true")
import pykx as kx  # noqa: E402


def records(table) -> list[dict]:
    """q table (pykx) -> list of dicts, timestamps as int ns, symbols as str.

    Column types are checked with pandas' own API: pandas 3 stores text in its own
    string dtype, which NumPy's type checks cannot interpret.
    """
    df = table.pd()
    for c in df.columns:
        col = df[c]
        if pd.api.types.is_datetime64_any_dtype(col):
            df[c] = col.astype("int64")
        elif pd.api.types.is_string_dtype(col) or col.dtype == object:
            df[c] = col.astype(object).where(col.notna(), "").map(str)
    return df.to_dict("records")


async def _await(x):
    return await x


class Subscriber:
    """with Subscriber(port, ("surface", "spot")) as sub:
           for msg in sub:            # never ends; msg is None when nothing has arrived
               ...                    # ("upd", table, rows) or ("end", date)
    """

    def __init__(self, port: int, tables, timeout: float = 15.0):
        self.port, self.tables, self.timeout = port, tuple(tables), timeout
        # PyKX looks the event loop up again on later calls, so it must stay open and be this
        # thread's current loop (asyncio.run would close it straight after connecting).
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.conn = self.loop.run_until_complete(
            _await(kx.RawQConnection(port=port, no_ctx=True, event_loop=self.loop)))
        tabs = "".join("`" + t for t in self.tables)
        reply = self.conn(f".u.sub[;`] each {tabs}")
        self.conn.poll_send(0)
        self.loop.run_until_complete(asyncio.wait_for(reply, timeout=timeout))   # raises if it failed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self.loop.run_until_complete(self.conn.close())     # PyKX closes raw connections async
        except Exception:
            pass

    def poll(self):
        """Next message, or None if nothing has arrived."""
        msg = self.conn.poll_recv()
        if msg is None:
            return None
        try:
            parts = list(msg)
        except TypeError:
            return None
        if not parts:
            return None
        fn = parts[0].py()
        if fn == "upd":
            return ("upd", parts[1].py(), records(parts[2]))
        if fn == ".u.end":
            return ("end", parts[1].py())
        return None

    def __iter__(self):
        while True:
            yield self.poll()
