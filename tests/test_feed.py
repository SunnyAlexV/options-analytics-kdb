"""Tests for the feed handler, run with:  pytest -q

They use real messages recorded from Deribit (tests/fixtures/deribit_samples.json),
so they check our parsing against what Deribit actually sends, with no network.
"""
import asyncio
import os
os.environ.setdefault("PYKX_UNLICENSED", "true")
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
    assert q["exch"] == data["timestamp"] * 1_000_000          # ms -> ns
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
    assert_matches_schema("spot", r)
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
            assert t in "npsfjdb", f"{table}.{name} has unknown type {t}"


def test_schemas_follow_kdb_tick_rules():
    # KX's tick.q refuses to start unless every table begins with time, sym
    for table, cols in SCHEMAS.items():
        assert cols[:2] == [("time", "n"), ("sym", "s")], table
        assert "asset" in dict(cols), f"{table} needs an asset column (multi-asset)"


# ------------------------------------------------------------------ quality
def quote(recv_s, bid, ask, sym="BTC-A"):
    ns = recv_s * 1_000_000_000
    return {"sym": sym, "asset": "BTC", "exch": ns - 5_000_000, "recv": ns,
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
    assert len(sink.rows["spot"]) == 1
    assert ws.sent[-1]["method"] == "public/test"                # heartbeat answered


# ------------------------------------------------- Python -> q conversion
QTYPENUM = {"s": 11, "f": 9, "p": 12, "j": 7, "b": 1}   # q type numbers of vectors


def sample_rows():
    """One realistic row list per table, built from the recorded fixtures."""
    from feed.quality import QualityMonitor
    q = N.book_to_quote(FIX["book"]["params"]["data"], RECV)
    m = QualityMonitor("BTC")
    m.on_quote(q)
    return {
        "quote": [q, N.book_to_quote({"timestamp": 1, "instrument_name": "BTC-X", "bids": [], "asks": []}, RECV)],
        "trade": N.trades_to_rows(FIX["trades"]["params"]["data"], RECV),
        "spot": [N.index_to_row(FIX["index"]["params"]["data"], RECV)],
        "snap": N.summary_to_rows(FIX["summary"], RECV),
        "ref": N.instruments_to_ref(FIX["instruments"]),
        "dq": [m.row()],
        "gap": [{"sym": "BTC", "asset": "BTC", "start": RECV, "end": RECV + 5, "reason": "silent"}],
        **engine_rows(),
    }


def engine_rows():
    """iv / fwd / surface rows from a run of the surface engine on a synthetic market."""
    pytest.importorskip("pricing", reason="build the C++ module first: pip install -e .")
    from engine.core import SurfaceEngine
    from tests.test_engine import synthetic_market
    now = 1_791_500_000 * 1_000_000_000
    ref, snap, quotes, _ = synthetic_market(now)
    eng = SurfaceEngine()
    eng.on_ref(ref)
    eng.on_snap(snap)
    ivs = eng.on_quotes(quotes)
    fwd, surf = eng.refit(now, force=True)
    return {"iv": ivs, "fwd": fwd, "surface": surf}


def test_every_table_converts_to_correctly_typed_q_columns():
    kx = pytest.importorskip("pykx")
    from feed.schema import FEED_SCHEMAS
    from feed.sinks import to_q_columns
    rows = sample_rows()
    assert set(rows) == set(FEED_SCHEMAS), "a table has no conversion test"
    for table, rs in rows.items():
        for r in rs:
            assert_matches_schema(table, r)
        cols = to_q_columns(kx, table, rs)
        assert len(cols) == len(FEED_SCHEMAS[table])
        for (name, typ), vec in zip(FEED_SCHEMAS[table], cols):
            assert vec.t == QTYPENUM[typ], f"{table}.{name}: q type {vec.t}, expected {typ}"
            assert len(vec) == len(rs)


def test_nulls_survive_conversion():
    kx = pytest.importorskip("pykx")
    from feed.sinks import to_q_columns
    empty = N.book_to_quote({"timestamp": 1, "instrument_name": "BTC-X", "bids": [], "asks": []}, RECV)
    cols = dict(zip(columns("quote"), to_q_columns(kx, "quote", [empty])))
    import math
    assert math.isnan(cols["bid"].py()[0])            # q 0n, not a fake zero


# ------------------------------------------------------------ q source checks
def test_q_scripts_have_no_lone_slash_lines():
    # In q, a line holding only "/" OPENS A BLOCK COMMENT that runs until a line
    # holding only "\". A lone "/" used as a spacer in a comment header silently
    # comments out the rest of the script (this bug stopped rdb.q, hdb.q and
    # gw.q loading anything on the first real run).
    import re
    qdir = Path(__file__).resolve().parent.parent / "q"
    ours = [p for p in qdir.rglob("*.q") if p.name not in ("tick.q", "u.q", "r.q")]
    assert ours, "no q scripts found"
    for p in ours:
        for n, line in enumerate(p.read_text().splitlines(), 1):
            assert not re.fullmatch(r"/\s*", line), f"{p.name}:{n} is a lone '/' (opens a block comment)"
