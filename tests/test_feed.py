"""Tests for the feed handler, run with:  pytest -q

They use real messages recorded from Deribit (tests/fixtures/deribit_samples.json),
so they check our parsing against what Deribit actually sends, with no network.
"""
import asyncio
import json
from pathlib import Path

import pytest

from feed import normalise as N
from feed.deribit import DeribitFeed
from feed.quality import QualityMonitor
from feed.schema import SCHEMAS, columns

FIX = json.loads((Path(__file__).parent / "fixtures" / "deribit_samples.json").read_text())
RECV = 1_791_485_000_000_000_000   # an arbitrary receive time, ns


def assert_matches_schema(table, row):
    assert list(row) == columns(table), f"{table} columns out of order or missing"


# ---------------------------------------------------------------- normalise
def test_book_to_quote_matches_schema_and_values():
    data = FIX["book"]["params"]["data"]
    q = N.book_to_quote(data, RECV)
    assert_matches_schema("quote", q)
    assert q["sym"] == data["instrument_name"] and q["asset"] == "BTC"
    assert q["time"] == data["timestamp"] * 1_000_000          # ms -> ns
    if data["bids"]:
        assert q["bid"] == data["bids"][0][0] and q["bsize"] == data["bids"][0][1]


def test_empty_book_side_gives_nulls():
    q = N.book_to_quote({"timestamp": 1, "instrument_name": "BTC-X", "bids": [], "asks": [[0.1, 2.0]]}, RECV)
    assert q["bid"] is None and q["bsize"] is None and q["ask"] == 0.1


def test_trades_to_rows():
    data = FIX["trades"]["params"]["data"]
    rows = N.trades_to_rows(data, RECV)
    assert len(rows) == len(data)
    for r in rows:
        assert_matches_schema("trade", r)
        assert r["side"] in ("buy", "sell") and r["size"] > 0


def test_index_to_row():
    r = N.index_to_row(FIX["index"]["params"]["data"], RECV)
    assert_matches_schema("index", r)
    assert r["sym"] == "btc_usd" and r["asset"] == "BTC" and r["price"] > 0


def test_summary_and_ref_rows():
    for r in N.summary_to_rows(FIX["summary"], RECV):
        assert_matches_schema("snap", r)
    for r in N.instruments_to_ref(FIX["instruments"]):
        assert_matches_schema("ref", r)
        assert r["cp"] in ("C", "P")
        # Deribit options expire at 08:00 UTC
        assert (r["expiry"] // 1_000_000_000) % 86400 == 8 * 3600


def test_every_schema_type_is_a_q_type():
    for table, cols in SCHEMAS.items():
        for name, t in cols:
            assert t in "psfjdb", f"{table}.{name} has unknown type {t}"


# ------------------------------------------------------------------ quality
def quote(recv_s, bid, ask, sym="BTC-A"):
    ns = recv_s * 1_000_000_000
    return {"time": ns - 5_000_000, "recv": ns, "sym": sym, "asset": "BTC",
            "bid": bid, "bsize": 1.0, "ask": ask, "asize": 1.0}


def test_quality_counts_and_minute_rollover():
    m = QualityMonitor("BTC")
    assert m.on_quote(quote(60, 0.1, 0.2)) == []
    assert m.on_quote(quote(61, 0.3, 0.2)) == []                 # crossed
    assert m.on_quote(quote(62, None, 0.2, sym="BTC-B")) == []   # one-sided
    out = m.on_quote(quote(120, 0.1, 0.2))                       # new minute closes the old one
    assert len(out) == 1
    row = out[0]
    assert_matches_schema("dq", row)
    assert (row["quotes"], row["crossed"], row["onesided"], row["symsupdated"]) == (3, 1, 1, 2)
    assert row["latmed"] == pytest.approx(5.0)                   # recv - time = 5 ms


# ------------------------------------------------------- message routing
class ListSink:
    def __init__(self):
        self.rows = {}

    def publish(self, table, rows):
        self.rows.setdefault(table, []).extend(rows)

    def close(self):
        pass


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(json.loads(text))


def test_handle_routes_messages_and_answers_heartbeats():
    sink, ws = ListSink(), FakeWS()
    feed = DeribitFeed(sink)

    async def go():
        await feed._handle(ws, FIX["book"], RECV)
        await feed._handle(ws, FIX["trades"], RECV)
        await feed._handle(ws, FIX["index"], RECV)
        await feed._handle(ws, {"method": "heartbeat", "params": {"type": "test_request"}}, RECV)

    asyncio.run(go())
    feed._flush()
    assert len(sink.rows["quote"]) == 1
    assert len(sink.rows["trade"]) == len(FIX["trades"]["params"]["data"])
    assert len(sink.rows["index"]) == 1
    assert ws.sent[-1]["method"] == "public/test"                # heartbeat answered
