"""Tests for the surface engine (engine/). Needs the compiled module: pip install -e ."""
import math

import numpy as np
import pytest

px = pytest.importorskip("pricing", reason="build the C++ module first: pip install -e .")
from pricing import _core  # noqa: E402

from engine.core import EngineConfig, SurfaceEngine  # noqa: E402
from engine.forward import parity_forward  # noqa: E402
from engine.smile import SliceConfig, build_slice, delta_strike, smile_metrics  # noqa: E402
from feed.schema import columns  # noqa: E402

NS = 1_000_000_000
YEAR = 365 * 24 * 3600 * NS
SVI_TRUE = [0.02, 0.15, -0.35, 0.03, 0.20]        # a skewed crypto-like smile


def btc_quotes(F, D, K, T, vol, call, half_spread_btc):
    """Bid/ask in BTC around the Black-76 value under Deribit's convention V_btc = D*Black/F."""
    mid = D * px.price(F, K, T, vol, call) / F
    return np.maximum(mid - half_spread_btc, 1e-8), mid + half_spread_btc


# ----------------------------------------------------------------- forward
def test_parity_forward_recovers_F_and_D_exactly():
    F, D, T = 82_000.0, 0.9985, 0.2
    K = np.arange(60_000, 110_001, 5_000, dtype=float)
    vol = np.sqrt(_core.svi_w(SVI_TRUE, np.log(K / F)) / T)
    cb, ca = btc_quotes(F, D, K, T, vol, True, 1e-4)
    pb, pa = btc_quotes(F, D, K, T, vol, False, 1e-4)
    r = parity_forward(K, cb, ca, pb, pa)
    assert r.ok and r.n == len(K)
    assert abs(r.F - F) < 1e-6 * F and abs(r.D - D) < 1e-8


def test_parity_forward_with_noise_is_within_its_standard_error():
    rng = np.random.default_rng(3)
    F, D, T = 82_000.0, 1.0, 0.1
    K = np.arange(70_000, 95_001, 1_000, dtype=float)
    vol = np.sqrt(_core.svi_w(SVI_TRUE, np.log(K / F)) / T)
    errs = []
    for _ in range(200):
        cb, ca = btc_quotes(F, D, K, T, vol, True, 2e-4)
        pb, pa = btc_quotes(F, D, K, T, vol, False, 2e-4)
        shift = rng.normal(0, 1e-4, (2, len(K)))           # quotes centred with noise ~ half-spread/2
        r = parity_forward(K, cb + shift[0], ca + shift[0], pb + shift[1], pa + shift[1])
        errs.append((r.F - F) / r.se_F)
    z = np.array(errs)
    assert abs(z.mean()) < 0.3                             # unbiased
    assert 0.3 < z.std() < 1.5                             # standard error is honest (not over/under-stated)


def test_parity_forward_rejects_too_few_strikes():
    assert not parity_forward([80_000, 85_000], [0.1, 0.05], [0.11, 0.06], [0.05, 0.1], [0.06, 0.11]).ok


# ----------------------------------------------------------------- metrics
def test_delta_strike_matches_closed_form_on_flat_smile():
    from scipy.stats import norm
    T, s = 0.25, 0.6
    flat = [s * s * T, 0.0, 0.0, 0.0, 0.1]                 # b = 0: w(k) = a, a flat smile
    for d in (0.25, 0.5, 0.75):
        # N(d1) = d  with d1 = (-k + w/2)/sqrt(w)  =>  k = w/2 - sqrt(w) * N^-1(d)
        w = s * s * T
        assert delta_strike(flat, T, d) == pytest.approx(w / 2 - math.sqrt(w) * norm.ppf(d), abs=2e-3)
    m = smile_metrics(flat, T)
    assert m["atmvol"] == pytest.approx(s) and abs(m["rr25"]) < 1e-9 and abs(m["bf25"]) < 1e-9


def test_skewed_smile_has_negative_risk_reversal():
    m = smile_metrics(SVI_TRUE, 0.25)      # rho < 0: puts richer than calls
    assert m["rr25"] < 0 and m["bf25"] > 0


# ------------------------------------------------------------------- slice
def test_build_slice_keeps_the_tighter_side_and_recovers_vols():
    F, D, T = 82_000.0, 1.0, 0.25
    K = np.arange(60_000, 110_001, 2_500, dtype=float)
    vol = np.sqrt(_core.svi_w(SVI_TRUE, np.log(K / F)) / T)
    rows = []
    for call, hs in ((True, 1e-4), (False, 3e-4)):        # calls quoted tighter than puts
        b, a = btc_quotes(F, D, K, T, vol, call, hs)
        rows += [(k, "C" if call else "P", bb, 1.0, aa, 1.0) for k, bb, aa in zip(K, b, a)]
    sl = build_slice(rows, F, D, T, SliceConfig())
    assert sl is not None and len(sl.k) == len(K)
    # where the call is the tighter market it must be chosen... except deep ITM calls, whose
    # vol spread is wider than the OTM put's even with a tighter price spread
    assert (sl.side == "C").sum() > len(K) // 3
    np.testing.assert_allclose(sl.iv, vol, atol=2e-3)      # mid IVs ~ true vols


# ------------------------------------------------------------- end to end
def synthetic_market(now_ns, F=82_000.0, D=1.0, days=60):
    exp_ns = now_ns + days * 86400 * NS
    T = (exp_ns - now_ns) / YEAR
    K = np.arange(50_000, 130_001, 2_500, dtype=float)
    vol = np.sqrt(_core.svi_w(SVI_TRUE, np.log(K / F)) / T)
    ref, quotes = [], []
    for call in (True, False):
        b, a = btc_quotes(F, D, K, T, vol, call, 1.5e-4)
        for k, bb, aa in zip(K, b, a):
            sym = f"BTC-TEST-{int(k)}-{'C' if call else 'P'}"
            ref.append({"sym": sym, "kind": "option", "expiry": exp_ns, "strike": k, "cp": "C" if call else "P"})
            quotes.append({"sym": sym, "exch": now_ns, "bid": bb, "bsize": 5.0, "ask": aa, "asize": 5.0})
    snap = [{"sym": ref[0]["sym"], "exch": now_ns, "und": F + 25.0}]   # Deribit's forward, deliberately $25 off
    return ref, snap, quotes, T


def test_engine_end_to_end_on_a_known_smile():
    now = 1_791_500_000 * NS
    ref, snap, quotes, T = synthetic_market(now)
    eng = SurfaceEngine(EngineConfig())
    eng.on_ref(ref)
    eng.on_snap(snap)
    ivs = eng.on_quotes(quotes)
    assert len(ivs) == len(quotes)
    for r in ivs[:3]:
        assert list(r) == columns("iv")
    fwd, surf = eng.refit(now, force=True)
    assert len(fwd) == 1 and len(surf) == 1
    assert list(fwd[0]) == columns("fwd") and list(surf[0]) == columns("surface")
    f, s = fwd[0], surf[0]
    assert abs(f["F"] - 82_000) < 1.0 and abs(f["D"] - 1.0) < 1e-4    # parity finds the true forward...
    assert f["fsrc"] == "parity"                                       # ...and the engine uses it
    # second pass: IVs now priced on the parity forward, smile matches the true one
    eng.on_quotes(quotes)
    fwd, surf = eng.refit(now + 2 * NS, force=True)
    s = surf[0]
    assert s["fsrc"] == "parity" and s["inband"] > 0.95 and s["afming"] >= 0
    true = smile_metrics(SVI_TRUE, T)
    assert s["atmvol"] == pytest.approx(true["atmvol"], abs=2e-3)
    assert s["rr25"] == pytest.approx(true["rr25"], abs=3e-3)
