"""Tests for the C++ pricing library through its Python interface.

Needs the compiled module:  pip install -e .
"""
import json
from pathlib import Path

import numpy as np
import pytest

px = pytest.importorskip("pricing", reason="build the C++ module first: pip install -e .")
from pricing import reference as ref  # noqa: E402

FIX = json.loads((Path(__file__).parent / "fixtures" / "deribit_samples.json").read_text())

# A grid shaped like a crypto option chain: strikes 0.3x-3x the forward, 1 day to 2 years.
rng = np.random.default_rng(7)
N = 500
F = np.full(N, 80_000.0)
K = F * np.exp(rng.uniform(-1.2, 1.2, N))
T = rng.uniform(1 / 365, 2.0, N)
S = rng.uniform(0.2, 1.5, N)
C = rng.random(N) < 0.5


def test_cpp_price_matches_python_reference():
    np.testing.assert_allclose(px.price(F, K, T, S, C), ref.price(F, K, T, S, C), rtol=1e-10, atol=1e-8)


def test_cpp_iv_matches_brent_reference():
    p = px.price(F, K, T, S, C)
    ours, status, _ = px.implied_vol(p, F, K, T, C, full=True)
    theirs = np.array([ref.implied_vol(*a) for a in zip(p, F, K, T, C)])
    time_value = p - np.where(C, np.maximum(F - K, 0), np.maximum(K - F, 0))
    # Where an option has real time value, both solvers must agree.
    real = time_value > 1e-8 * F
    np.testing.assert_allclose(ours[real], theirs[real], atol=1e-8)
    # Where it has none (worth < ~1e-9 USD above intrinsic), ours refuses with a status
    # code. Brent "succeeds" there by returning its lower search bound, 1e-8, which is
    # not a vol at all: the reason our solver reports status instead of guessing.
    none = time_value <= 1e-14 * np.minimum(F, K)
    assert none.any()
    assert np.all(status[none] == 1) and np.all(np.isnan(ours[none]))


def test_broadcasting_and_cp_strings():
    out = px.price(80_000, [70_000, 80_000, 90_000], 0.25, 0.5, call=["C", "P", "c"])
    assert out.shape == (3,)
    assert np.isclose(out[0], px.price(80_000, 70_000, 0.25, 0.5, True))
    assert np.isclose(out[1], px.price(80_000, 80_000, 0.25, 0.5, False))
    assert isinstance(px.price(80_000, 80_000, 0.25, 0.5), float)      # scalars in -> scalar out


def test_iv_status_codes():
    sig, st, _ = px.implied_vol([9.0, 100.0, 12.0], 100, 90, 0.5, True, full=True)
    assert [px.IV_STATUS[s] for s in st] == ["below_intrinsic", "above_maximum", "ok"]
    assert np.isnan(sig[0]) and np.isnan(sig[1]) and np.isfinite(sig[2])


def test_premium_adjusted_delta_is_derivative_of_btc_price():
    # V_btc = V_usd / F  =>  F * dV_btc/dF = delta - V_usd/F  (the premium-adjusted delta)
    f, k, t, s = 80_000.0, 90_000.0, 0.3, 0.6
    h = 1e-5 * f
    for call in (True, False):
        vb = lambda x: px.to_btc(px.price(x, k, t, s, call), x)  # noqa: E731
        fd = f * (vb(f + h) - vb(f - h)) / (2 * h)
        assert np.isclose(px.premium_adjusted_delta(f, k, t, s, call), fd, rtol=1e-6)


def test_deribit_expiry_and_year_fraction():
    inst = FIX["instruments"][0]
    assert px.deribit_expiry_ns(inst["instrument_name"]) == inst["expiration_timestamp"] * 1_000_000
    assert np.isclose(px.year_fraction(0, 365 * 86400 * 10**9), 1.0)


def test_reproduces_deribit_marks_offline():
    # Deribit's mark price (BTC) should equal Black-76(mark_iv) / forward to within one tick.
    for s in FIX["summary"]:
        sym = s["instrument_name"]
        t = px.year_fraction(s["creation_timestamp"] * 1_000_000, px.deribit_expiry_ns(sym))
        k, cp = float(sym.split("-")[2]), sym[-1]
        model_btc = px.to_btc(px.price(s["underlying_price"], k, t, s["mark_iv"] / 100, cp), s["underlying_price"])
        assert abs(model_btc - s["mark_price"]) < 1e-4, sym
