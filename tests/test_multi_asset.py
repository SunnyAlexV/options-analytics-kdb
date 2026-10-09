"""Several coins at once: index routing, settlement groups, per-instrument ticks, the inverse and
linear books of one coin agreeing, and dashboard USD conversion for both conventions."""
import math
import types

import numpy as np
import pandas as pd
import pytest

px = pytest.importorskip("pricing", reason="build the C++ module first: pip install -e .")
from pricing import _core  # noqa: E402

from engine.conventions import DERIBIT, DERIBIT_USDC, for_asset  # noqa: E402
from engine.core import SurfaceEngine  # noqa: E402
from engine.replay import replay  # noqa: E402
from feed.deribit import DeribitFeed  # noqa: E402
from feed.normalise import index_asset, index_to_row  # noqa: E402

NS = 1_000_000_000
YEAR = 365 * 24 * 3600 * NS
NOW = 1_791_500_000 * NS
EXP = NOW + 30 * 86400 * NS                        # a 30-day expiry
SVI = [0.02, 0.15, -0.35, 0.03, 0.20]


# --------------------------------------------------------------------------- feed side
def test_index_names_map_to_option_assets():
    assert index_asset("btc_usd") == "BTC"
    assert index_asset("eth_usd") == "ETH"
    assert index_asset("sol_usdc") == "SOL_USDC"
    assert index_asset("btc_usdc") == "BTC_USDC"
    row = index_to_row({"index_name": "xrp_usdc", "timestamp": 1_000, "price": 2.5}, recv=7)
    assert row == {"sym": "xrp_usdc", "asset": "XRP_USDC", "exch": 1_000_000_000, "recv": 7, "price": 2.5}


def test_feed_subscribes_one_index_per_coin():
    btc = DeribitFeed.__new__(DeribitFeed)
    btc.currency, btc.instruments = "BTC", {"BTC-30OCT26-80000-C"}
    assert btc._index_channels() == ["deribit_price_index.btc_usd"]
    usdc = DeribitFeed.__new__(DeribitFeed)
    usdc.currency = "USDC"
    usdc.instruments = {"SOL_USDC-30OCT26-150-C", "SOL_USDC-30OCT26-150-P", "XRP_USDC-30OCT26-2d5-C"}
    assert usdc._index_channels() == ["deribit_price_index.sol_usdc", "deribit_price_index.xrp_usdc"]


def test_conventions_by_asset():
    assert for_asset("BTC") is DERIBIT and for_asset("ETH") is DERIBIT
    assert for_asset("SOL_USDC") is DERIBIT_USDC and for_asset("BTC_USDC") is DERIBIT_USDC


# --------------------------------------------------------------------------- engine side
def chain(asset, F, D, strikes, tick, linear, half_spread_ticks=2):
    """ref and quote rows for one expiry, priced off SVI with the asset's premium convention."""
    T = (EXP - NOW) / YEAR
    lab = f"{asset}-30OCT26"
    ref, quotes = [], []
    for K in strikes:
        vol = math.sqrt(_core.svi_w(SVI, [math.log(K / F)])[0] / T)
        for cp in "CP":
            black = px.price(F, K, T, vol, cp == "C")
            mid = D * black if linear else D * black / F
            bid = max(round(mid / tick) * tick - half_spread_ticks * tick, tick)
            ask = round(mid / tick) * tick + half_spread_ticks * tick
            ks = f"{K:g}".replace(".", "d")
            sym = f"{lab}-{ks}-{cp}"
            ref.append({"sym": sym, "asset": asset, "kind": "option", "expiry": EXP, "strike": K, "cp": cp,
                        "tick": tick, "recv": NOW - NS})
            quotes.append({"sym": sym, "asset": asset, "exch": NOW, "recv": NOW, "bid": bid, "bsize": 10.0,
                           "ask": ask, "asize": 10.0})
    return ref, quotes


def test_engine_keeps_each_instruments_tick():
    eng = SurfaceEngine(asset="TRX_USDC")
    ref, _ = chain("TRX_USDC", 0.30, 1.0, [0.25, 0.30, 0.35], 5e-5, True)
    eng.on_ref(ref)
    assert eng.tick_size == {"TRX_USDC-30OCT26": 5e-5}
    eng = SurfaceEngine(asset="BTC_USDC")
    eng.on_ref(chain("BTC_USDC", 80_000, 1.0, [80_000], 5.0, True)[0])
    assert eng.tick_size == {"BTC_USDC-30OCT26": 5.0}


def test_one_replay_fits_inverse_and_linear_books_to_the_same_smile():
    """BTC (inverse, premium in BTC) and BTC_USDC (linear, premium in USDC) quoted off the same
    forward and smile, interleaved in one recording: one engine per asset, the right convention
    for each, and the same forward and ATM vol out of both."""
    F, D = 80_000.0, 0.997
    K = np.arange(60_000, 100_001, 2_000, dtype=float)
    r1, q1 = chain("BTC", F, D, K, 1e-4, linear=False)
    r2, q2 = chain("BTC_USDC", F, D, K, 5.0, linear=True)
    # a few later quote batches so the engine refits on the parity forward
    later = [dict(q, exch=NOW + i * NS, recv=NOW + i * NS) for i in (2, 4) for q in q1 + q2]
    data = {"ref": r1 + r2, "snap": [], "quote": q1 + q2 + later}
    surf, fwd = {}, {}

    def on_out(now, ivs, fw, sf, eng):
        surf.update({r["sym"]: r for r in sf})
        fwd.update({r["sym"]: r for r in fw})

    engines = replay(data, on_output=on_out)
    assert set(engines) == {"BTC", "BTC_USDC"}
    assert engines["BTC"].conv is DERIBIT and engines["BTC_USDC"].conv is DERIBIT_USDC
    a, b = fwd["BTC-30OCT26"], fwd["BTC_USDC-30OCT26"]
    assert a["fsrc"] == b["fsrc"] == "parity"
    assert abs(a["F"] / F - 1) < 5e-4 and abs(b["F"] / F - 1) < 5e-4
    assert abs(a["D"] - D) < 2e-3 and abs(b["D"] - D) < 2e-3
    T = (EXP - NOW) / YEAR
    atm_true = math.sqrt(_core.svi_w(SVI, [0.0])[0] / T)
    for s in ("BTC-30OCT26", "BTC_USDC-30OCT26"):
        assert surf[s]["atmvol"] == pytest.approx(atm_true, abs=0.005)
    assert abs(surf["BTC-30OCT26"]["rr25"] - surf["BTC_USDC-30OCT26"]["rr25"]) < 0.005


def test_engine_process_routes_by_settlement_group():
    pytest.importorskip("pykx")
    from engine.__main__ import Runner, in_group
    assert in_group("SOL_USDC", "USDC") and in_group("BTC_USDC", "USDC")
    assert not in_group("BTC", "USDC") and in_group("BTC", "BTC") and not in_group("ETH", "BTC")

    class Sink:
        def __init__(self):
            self.rows = []

        def publish(self, t, rows):
            self.rows += rows

    run = Runner.__new__(Runner)
    run.group, run.engines, run.out = "USDC", {}, Sink()
    run.cfg = None
    run.n = {"quote": 0, "iv": 0, "surface": 0, "fwd": 0}
    r_sol, q_sol = chain("SOL_USDC", 150.0, 1.0, [140.0, 150.0, 160.0], 0.01, True)
    r_btc, q_btc = chain("BTC", 80_000.0, 1.0, [80_000.0], 1e-4, False)
    run.handle("ref", r_sol + r_btc)
    run.handle("quote", q_sol + q_btc)
    assert set(run.engines) == {"SOL_USDC"}                 # BTC rows belong to another process
    assert run.n["quote"] == len(q_sol)
    assert run.engines["SOL_USDC"].conv is DERIBIT_USDC


# --------------------------------------------------------------------------- dashboard side
def test_trade_usd_value_by_convention():
    from dashboard.analytics import trade_flow
    trades = pd.DataFrame({
        "time": pd.to_datetime([1, 2], unit="s"), "sym": ["BTC-30OCT26-80000-C", "SOL_USDC-30OCT26-150-C"],
        "price": [0.05, 7.5], "size": [1.0, 10.0], "side": ["buy", "sell"], "idx": [80_000.0, 150.0]})
    ref = pd.DataFrame({"sym": trades["sym"], "strike": [80_000.0, 150.0], "cp": ["C", "C"]})
    out = trade_flow({"trades": trades, "latest_ref": ref})
    usd = dict(zip(out["tape"]["sym"], out["tape"]["usd"]))
    assert usd["BTC-30OCT26-80000-C"] == pytest.approx(4_000.0)    # 0.05 BTC x 80,000
    assert usd["SOL_USDC-30OCT26-150-C"] == pytest.approx(7.5)     # already in USDC


def test_dashboard_views_split_by_asset():
    from dashboard.sources import assets_in, for_asset as view_for
    views = {"latest_surface": pd.DataFrame({"sym": ["SOL_USDC-30OCT26", "BTC-30OCT26"], "asset": ["SOL_USDC", "BTC"]}),
             "trades": pd.DataFrame({"sym": ["BTC-30OCT26-80000-C", "SOL_USDC-30OCT26-150-C"],
                                     "asset": ["BTC", "SOL_USDC"]}),
             "hist": pd.DataFrame({"date": [1], "dvol": [50.0]})}      # no asset column: passes through
    assert assets_in(views) == ["BTC", "SOL_USDC"]
    sol = view_for(views, "SOL_USDC")
    assert list(sol["latest_surface"]["sym"]) == ["SOL_USDC-30OCT26"] and list(sol["trades"]["asset"]) == ["SOL_USDC"]
    assert sol["hist"] is views["hist"]
