"""The live engine process (engine/__main__.py) against stand-in kdb+ servers.

These exercise the real networking code paths -- PyKX connections, kdb+tick's
.u.sub handshake, async upd messages, the RDB polling queries and publishing
back through .u.upd -- using tests/fake_kdb.py, which speaks kdb+'s wire protocol.
"""
import argparse
import threading
import time

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pricing", reason="build the C++ module first: pip install -e .")

from engine.__main__ import Runner, records  # noqa: E402
from tests.fake_kdb import ServerProcess  # noqa: E402
from tests.test_engine import synthetic_market  # noqa: E402


def frames(now_ns):
    ref, snap, quotes, _ = synthetic_market(now_ns)
    ts = lambda v: pd.to_datetime(v, unit="ns")  # noqa: E731
    ref_df = pd.DataFrame({"sym": [r["sym"] for r in ref], "kind": "option",
                           "expiry": ts([r["expiry"] for r in ref]),
                           "strike": [r["strike"] for r in ref], "cp": [r["cp"] for r in ref]})
    snap_df = pd.DataFrame({"sym": [r["sym"] for r in snap], "exch": ts([r["exch"] for r in snap]),
                            "und": [r["und"] for r in snap]})
    q_df = pd.DataFrame({"sym": [r["sym"] for r in quotes], "exch": ts([r["exch"] for r in quotes]),
                         "bid": [r["bid"] for r in quotes], "bsize": 5.0,
                         "ask": [r["ask"] for r in quotes], "asize": 5.0})
    return {"ref": ref_df, "snap": snap_df, "quote": q_df}


def start(runner, mode):
    t = threading.Thread(target=runner.run_stream if mode == "stream" else runner.run_poll, daemon=True)
    t.start()
    return t


def args(mode, tp, rdb):
    return argparse.Namespace(mode=mode, tp_port=tp.port, rdb_port=rdb.port, throttle=0.2, currency="BTC")


def test_records_handles_pandas_string_and_timestamp_columns():
    import pykx as kx
    df = pd.DataFrame({"sym": pd.array(["a", "b"], dtype="string"), "x": [1.0, np.nan],
                       "t": pd.to_datetime([1, 2], unit="ns")})
    rows = records(kx.toq(df))
    assert rows[0]["sym"] == "a" and rows[1]["t"] == 2 and np.isnan(rows[1]["x"])


def test_stream_mode_subscribes_and_publishes():
    now = time.time_ns()
    data = frames(now)
    tp = ServerProcess("tp", data)
    rdb = ServerProcess("rdb", {t: df.iloc[:0] for t, df in data.items()})   # empty at start-up
    try:
        start(Runner(args("stream", tp, rdb)), "stream")
        subs, _ = tp.wait_for(lambda s, p: len(s) == 3)
        assert subs == ["quote", "ref", "snap"], "engine did not subscribe via .u.sub"
        for t in ("ref", "snap", "quote"):
            tp.publish(t, data[t])
        _, pub = tp.wait_for(lambda s, p: {"iv", "surface", "fwd"} <= set(p))
        assert pub.get("iv"), "no implied vols published"
        assert pub.get("surface") and pub.get("fwd"), "no smiles / forwards published"
        assert sum(pub["iv"]) == len(data["quote"])                    # one iv row per quote
    finally:
        tp.stop(); rdb.stop()


def test_poll_mode_reads_new_rows_and_publishes():
    now = time.time_ns()
    data = frames(now)
    half = len(data["quote"]) // 2
    tp = ServerProcess("tp", data)
    rdb = ServerProcess("rdb", {"ref": data["ref"], "snap": data["snap"], "quote": data["quote"].iloc[:half]})
    try:
        start(Runner(args("poll", tp, rdb)), "poll")
        time.sleep(2.0)                                                # bootstrap reads the first half
        rdb.set_table("quote", data["quote"])                          # new rows arrive in the RDB
        _, pub = tp.wait_for(lambda s, p: "iv" in p and "surface" in p)
        assert pub.get("iv"), "poll mode published no implied vols"
        assert pub.get("surface"), "poll mode published no smiles"
        assert sum(pub["iv"]) == len(data["quote"]) - half             # exactly the new rows, once each
    finally:
        tp.stop(); rdb.stop()
