"""Premium conventions: the same engine prices inverse (Deribit) and linear (NSE) options."""
import datetime as dt
import math

import numpy as np
import pytest

px = pytest.importorskip("pricing", reason="build the C++ module first: pip install -e .")
from pricing import _core  # noqa: E402

from engine.conventions import DERIBIT, NSE, for_asset  # noqa: E402
from engine.core import EngineConfig, SurfaceEngine  # noqa: E402
from engine.forward import parity_forward  # noqa: E402

NS = 1_000_000_000
YEAR = 365 * 24 * 3600 * NS
SVI_NIFTY = [0.002, 0.03, -0.55, 0.02, 0.06]          # an index-like smile: steep put skew, low vol


def inr_quotes(F, D, K, T, vol, call, half_spread):
    """Bid/ask in INR around the linear convention's value V = D * Black76(F), on a 0.05 tick."""
    mid = D * px.price(F, K, T, vol, call)
    bid = np.maximum(np.round((mid - half_spread) / 0.05) * 0.05, 0.05)
    ask = np.round((mid + half_spread) / 0.05) * 0.05
    return bid, np.maximum(ask, bid + 0.05)


def test_linear_parity_recovers_forward_and_inr_discount_factor():
    """C - P = D (F - K): with an INR rate of 6.5% and dividend yield 1.2%, the regression must
    recover both the forward (carry 5.3%) and the discount factor (the rate alone)."""
    S, r, q, T = 25_000.0, 0.065, 0.012, 45 / 365
    F, D = S * math.exp((r - q) * T), math.exp(-r * T)
    K = np.arange(22_000, 28_001, 100, dtype=float)
    vol = np.sqrt(_core.svi_w(SVI_NIFTY, np.log(K / F)) / T)
    cb, ca = inr_quotes(F, D, K, T, vol, True, 0.5)
    pb, pa = inr_quotes(F, D, K, T, vol, False, 0.5)
    res = parity_forward(K, cb, ca, pb, pa, conv=NSE)
    assert res.ok
    assert abs(res.F - F) < 3 * res.se_F + 0.5                     # within its standard error (+tick rounding)
    assert abs(res.D - D) < 3 * res.se_D + 1e-5
    implied_r = -math.log(res.D) / T
    assert implied_r == pytest.approx(r, abs=0.003)                 # the INR rate, from options alone
    # the same quotes read with the inverse convention give nonsense: conventions matter
    wrong = parity_forward(K, cb, ca, pb, pa, conv=DERIBIT)
    assert not wrong.ok or abs(wrong.D - D) > 1.0


def test_conventions_round_trip_and_expiry_time():
    for conv in (DERIBIT, NSE):
        v = np.array([10.0, 250.0])
        np.testing.assert_allclose(conv.to_black(conv.from_black(v, 25_000.0, 0.99), 25_000.0, 0.99), v)
    # NSE options stop trading at 15:30 IST = 10:00 UTC
    t = NSE.expiry_ns(dt.date(2026, 10, 27))
    assert dt.datetime.fromtimestamp(t / NS, dt.timezone.utc) == dt.datetime(2026, 10, 27, 10, 0, tzinfo=dt.timezone.utc)
    assert for_asset("BTC") is DERIBIT and for_asset("NIFTY") is NSE


def test_engine_end_to_end_on_a_linear_nse_chain():
    """The unchanged engine, given the NSE convention, recovers the forward, the INR discount
    factor and the smile from INR quotes."""
    now = 1_791_500_000 * NS
    exp = NSE.expiry_ns(dt.date(2026, 11, 24))
    T = (exp - now) / YEAR
    S, r, q = 25_000.0, 0.065, 0.012
    F, D = S * math.exp((r - q) * T), math.exp(-r * T)
    K = np.arange(21_000, 29_001, 100, dtype=float)
    vol = np.sqrt(_core.svi_w(SVI_NIFTY, np.log(K / F)) / T)
    ref, quotes = [], []
    for call in (True, False):
        b, a = inr_quotes(F, D, K, T, vol, call, 0.5)
        for k, bb, aa in zip(K, b, a):
            sym = f"NIFTY-24NOV26-{int(k)}-{'C' if call else 'P'}"
            ref.append({"sym": sym, "kind": "option", "expiry": exp, "strike": k, "cp": "C" if call else "P"})
            quotes.append({"sym": sym, "exch": now, "bid": bb, "bsize": 65.0, "ask": aa, "asize": 65.0})
    eng = SurfaceEngine(EngineConfig(), asset="NIFTY", conv=NSE)
    eng.on_ref(ref)
    eng.on_snap([{"sym": ref[0]["sym"], "exch": now, "und": F * 1.001}])   # a stale futures-based guess
    eng.on_quotes(quotes)
    fwd, _ = eng.refit(now, force=True)
    f = fwd[0]
    assert f["fsrc"] == "parity" and abs(f["F"] - F) < 5 and abs(f["D"] - D) < 2e-4
    eng.on_quotes(quotes)                                   # re-priced on the parity forward
    _, surf = eng.refit(now + 2 * NS, force=True)
    s = surf[0]
    assert s["inband"] > 0.9
    k = np.log(K / F)
    fitted = np.sqrt(_core.svi_w([s["afa"], s["afb"], s["afrho"], s["afm"], s["afsigma"]], k) / s["T"])
    near = np.abs(k) < 0.08
    np.testing.assert_allclose(fitted[near], vol[near], atol=5e-3)          # the true smile, to 0.5 vol pt
