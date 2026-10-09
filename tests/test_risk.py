"""Phase 5 risk: Greeks under smile rules, P&L explain, scenarios, VaR, smile-dynamics estimation.

Synthetic BTC-like smiles (SVI) are used so each test knows the right answer exactly.
"""
import math

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pricing", reason="build the C++ module first: pip install -e .")

import pricing as px  # noqa: E402
from pricing import _core  # noqa: E402
from risk import var as varmod  # noqa: E402
from risk.book import sample_book  # noqa: E402
from risk.core import aggregate, pnl_explain, pnl_rows, position_greeks, scenario_grid  # noqa: E402
from risk.dynamics import estimate_R, pairs, snapshots  # noqa: E402
from risk.market import NS, YEAR_NS, Market  # noqa: E402

NOW = 1_791_500_000 * NS                                   # 2026-10-08, a fixed moment
EXPIRIES = [(7, "BTC-15OCT26"), (30, "BTC-07NOV26"), (90, "BTC-06JAN27")]


def surface_rows(now=NOW, F=80000.0, dlnF=0.0, m_shift=0.0, a_shift=0.0):
    """Surface rows like the engine publishes: skewed SVI smiles, ~45-55% vol."""
    rows = []
    for days, label in EXPIRIES:
        expiry = NOW + days * 86400 * NS                    # fixed dates: time passing shortens T
        T = (expiry - now) / YEAR_NS
        rows.append({"sym": label, "expiry": expiry, "T": T, "F": F * math.exp(dlnF),
                     "afa": (0.18 + a_shift) * T, "afb": 0.6 * T, "afrho": -0.3, "afm": 0.02 + m_shift,
                     "afsigma": 0.2, "now": now})
    return rows


def market(**kw) -> Market:
    rows = surface_rows(**kw)
    return Market.from_surface(rows, asof=rows[0]["now"], spot=80000.0)


def opt(label_days, K, cp, qty, book="t"):
    days, label = next(e for e in EXPIRIES if e[0] == label_days)
    return {"sym": f"{label}-{K:.0f}-{cp}", "book": book, "kind": "option", "expiry": NOW + days * 86400 * NS,
            "strike": float(K), "cp": cp, "qty": float(qty), "entry": 0.0}


def fut(label_days, qty, entry, book="t"):
    days, label = next(e for e in EXPIRIES if e[0] == label_days)
    return {"sym": label, "book": book, "kind": "future", "expiry": NOW + days * 86400 * NS,
            "strike": math.nan, "cp": "", "qty": float(qty), "entry": float(entry)}


def test_parity_book_has_no_risk():
    m = market()
    K = 85000
    book = [opt(30, K, "C", 1), opt(30, K, "P", -1), fut(30, -1, K)]
    tot = aggregate(position_greeks(book, m, R=0.3)).query("kind == 'total'").iloc[0]
    for c in ("deltaR", "gamma", "vega", "theta", "vanna", "volga", "mtm"):
        assert abs(tot[c]) < 1e-6 * 80000, c
    assert np.abs(scenario_grid(book, m, R=0.3)["pnl"]).max() < 1e-5


def test_sticky_strike_gamma_is_black76_gamma():
    """Under R = 1 the smile does not move at a fixed strike, so the finite-difference rule gamma
    must equal Black-76's analytic gamma."""
    m = market()
    pg = position_greeks([opt(30, 80000, "C", 1), opt(90, 60000, "P", 1)], m, R=1.0)
    g = px.greeks(pg["F"].to_numpy(), pg["K"].to_numpy(), pg["T"].to_numpy(), pg["vol"].to_numpy(),
                  np.array([True, False]))
    np.testing.assert_allclose(pg["gamma"], g["gamma"] * pg["F"] * 0.01, rtol=1e-4)
    np.testing.assert_allclose(pg["deltaR"], g["delta"], rtol=1e-12)


def test_rule_delta_matches_scenario_revaluation():
    m = market()
    book = [opt(30, 70000, "P", 2), opt(90, 95000, "C", -1)]
    for R in (0.0, 0.5, 1.0):
        tot = aggregate(position_greeks(book, m, R=R)).query("kind == 'total'").iloc[0]
        g = scenario_grid(book, m, R=R, spots=(-1e-4, 1e-4), vols=(0,))
        fd = (g["pnl"].iloc[1] - g["pnl"].iloc[0]) / 2e-4 * 0.01        # USD per +1% (all forwards)
        assert fd == pytest.approx(tot["cashdelta"], rel=1e-4)


def test_pnl_explain_small_move_is_explained():
    m0 = market()
    m1 = market(now=NOW + 3600 * NS, dlnF=0.004, a_shift=0.002)     # 1 hour, +0.4%, vols up a little
    book = [opt(7, 80000, "C", 10), opt(30, 70000, "P", -5), opt(90, 90000, "C", 3), fut(30, -2, 80000)]
    ex = pnl_explain(book, m0, m1, R=0.0)
    tot = pnl_rows(ex).iloc[0]
    assert abs(tot["actual"]) > 100
    assert abs(tot["unexpl"]) < 0.01 * abs(tot["actual"])
    assert tot["smile"] + tot["surf"] == pytest.approx(tot["vega"], rel=1e-9)


def test_pnl_split_attributes_a_rule_move_to_smile():
    """If the smile moves exactly by rule R (forward up, SVI m shifted by -R x), the vol change
    is all 'smile' under that R, and the 'surf' part vanishes to first order."""
    R, x = 0.5, 0.01
    m0 = market()
    m1 = market(dlnF=x, m_shift=-R * x)
    book = [opt(30, 70000, "P", 1), opt(30, 90000, "C", 1)]
    ex = pnl_explain(book, m0, m1, R=R)
    assert np.all(np.abs(ex["surf"]) < 0.02 * np.abs(ex["smile"]))
    ex_wrong = pnl_explain(book, m0, m1, R=1.0)                         # sticky-strike calls it all surface
    assert np.all(np.abs(ex_wrong["smile"]) == 0)


def test_estimate_R_recovers_the_true_rule():
    rng = np.random.default_rng(7)
    R_true, rows, lnF, a = 0.4, [], 0.0, 0.0
    for j in range(240):                                               # 240 one-minute snapshots
        x = rng.normal(0, 0.002)
        lnF += x
        a += rng.normal(0, 0.0002)                                     # independent vol noise
        rows += surface_rows(now=NOW + j * 60 * NS, dlnF=lnF, m_shift=-R_true * lnF, a_shift=a)
    est = estimate_R(pairs(snapshots(rows, 60)))
    assert abs(est["R"] - R_true) < max(3 * est["se"], 0.05)
    assert est["intervals"] > 200


def test_sample_book_is_delta_hedged_under_its_rule():
    m = market()
    book = sample_book(m, hedge_rule_R=0.3)
    tot = aggregate(position_greeks(book, m, R=0.3)).query("kind == 'total'").iloc[0]
    assert abs(tot["deltaspot"]) < 1e-6
    assert {r["kind"] for r in book} == {"option", "future"}


def test_var_es_and_kupiec():
    pnl = -np.arange(1, 101, dtype=float)                             # losses 1..100
    r = varmod.var_es(pnl)
    assert r["var99"] == 100 and r["es99"] == 100
    assert r["es975"] == pytest.approx((100 + 99 + 98) / 3)
    assert varmod.kupiec(1, 100) == pytest.approx(1.0)
    assert varmod.kupiec(10, 100) < 0.001


def test_fhs_rescales_to_current_volatility():
    dates = pd.date_range("2025-01-01", periods=400, freq="D")
    rng = np.random.default_rng(1)
    r = np.r_[rng.normal(0, 0.01, 300), rng.normal(0, 0.04, 100)]       # calm, then volatile
    hist = pd.DataFrame({"date": dates, "spot": 80000 * np.exp(np.r_[0, np.cumsum(r[:-1])]),
                         "dvol": 50.0 + rng.normal(0, 0.1, 400)})
    mv = varmod.moves(hist)
    hs_r, _ = varmod.scenarios(mv.tail(365), "hs")
    fhs_r, _ = varmod.scenarios(mv.tail(365), "fhs")
    assert np.std(fhs_r) > 1.5 * np.std(hs_r)                         # scaled up to today's regime


def test_var_parity_book_is_zero():
    m = market()
    K = 80000
    book = [opt(30, K, "C", 1), opt(30, K, "P", -1), fut(30, -1, K)]
    v = varmod.pnl_vector(book, m, np.array([-0.2, 0.0, 0.15]), np.array([0.3, 0.0, -0.2]), R=0.0)
    assert np.abs(v).max() < 1e-6
