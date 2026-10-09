"""Portfolio risk: Greeks, buckets, scenarios and P&L explain for a book of BTC options.

Units (desk conventions, all signed by position quantity):
    mtm        mark-to-market value, USD
    delta      BTC per forward: dV/dF, smile held fixed per strike (Black-76 delta)
    deltaR     BTC per forward under smile rule R:  Delta + vega (R - 1) sigma'(k) / F
    deltapa    premium-adjusted delta (BTC): what a BTC-margined account must hedge, Delta - V/F
    deltaspot  spot-equivalent BTC under rule R: sum of deltaR * F_e / S (all forwards move with spot)
    cashdelta  USD P&L of a +1% move in every forward, first order, rule R
    gamma      change in deltaR (BTC) for a +1% move:  Gamma_R * F / 100
    cashgamma  USD P&L from gamma alone for a 1% move:  Gamma_R (F/100)^2 / 2
    vega       USD per +1 vol point
    theta      USD per calendar day (crypto trades every day)
    vanna      change in delta (BTC) per +1 vol point
    volga      change in vega (USD per vol pt) per +1 vol point
    vatm, vrr, vbf   USD per +1 vol point of ATM vol, 25-delta risk reversal, 25-delta butterfly

Gamma under rule R is a central finite difference of full revaluation, so it includes the
smile's own movement exactly (no approximation of how sigma(K) responds to F).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import pricing as px
from pricing import _core

from .book import arrays, label_of
from .market import YEAR_NS, Market

# Call-delta buckets: N(d1) of each option, reported by the conventional name of the point
# nearest to it. A 25-delta put has call delta 0.75.
DELTA_EDGES = [0.0, 0.175, 0.375, 0.625, 0.825, 1.0000001]
DELTA_NAMES = ["10C", "25C", "ATM", "25P", "10P"]

GRID_SPOT = (-0.30, -0.20, -0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10, 0.20, 0.30)
GRID_VOL = (-20, -10, -5, 0, 5, 10, 20)      # vol points
H = 1e-3                                     # log-move for finite-difference gamma (0.1%)


def _values(a, F, T, P, dlnF=0.0, dvol=0.0, vscale=1.0, dt=0.0, R=1.0, each=False):
    """Book value (or each position's value) under one scenario."""
    sc = [np.array([x], dtype=float) for x in (dlnF, dvol, vscale, dt)]
    if not each:
        return float(_core.book_values(a["qty"], a["K"], a["slice"], a["call"], a["future"],
                                       F, T, P, *sc, R)[0])
    return np.array([float(_core.book_values(a["qty"][i:i + 1], a["K"][i:i + 1], a["slice"][i:i + 1],
                                              a["call"][i:i + 1], a["future"][i:i + 1], F, T, P, *sc, R)[0])
                     for i in range(len(a["qty"]))])


def position_greeks(rows, mkt: Market, R: float = 1.0, now: int | None = None) -> pd.DataFrame:
    """One row per position: unit price and Greeks, then quantity-weighted risk in desk units."""
    a, missing = arrays(rows, mkt)
    F, T, P = mkt.smiles(now)
    n = len(a["qty"])
    if n == 0:
        return pd.DataFrame()
    s = a["slice"]
    Fi, Ti = F[s], T[s]
    opt = ~a["future"]
    vol = np.where(opt, _core.smile_vol(F, T, P, s, np.where(opt, a["K"], Fi), 0.0, R), np.nan)
    g = px.greeks(Fi, np.where(opt, a["K"], Fi), Ti, np.where(opt, vol, 0.2), a["call"])
    zero = np.zeros(n)
    unit = {k: np.where(opt, g[k], zero) for k in ("price", "delta", "gamma", "vega", "theta", "vanna", "volga")}
    unit["price"] = np.where(opt, g["price"], Fi - a["K"])         # future: value per contract
    unit["delta"] = np.where(opt, g["delta"], 1.0)
    slope = np.where(opt, _core.smile_slope(F, T, P, s, np.where(opt, a["K"], Fi)), 0.0)
    deltaR = unit["delta"] + unit["vega"] * (R - 1.0) * slope / Fi

    # rule gamma: central difference of each position's full revaluation in log-forward
    up = _values(a, F, T, P, dlnF=H, R=R, each=True) / a["qty"]
    dn = _values(a, F, T, P, dlnF=-H, R=R, each=True) / a["qty"]
    base = unit["price"]
    # bumps are on a log grid F e^{+-h}: with G(x) = V(F e^x), G''(0) = F^2 V'' + F V', so
    # V'' = [V(+h) - 2V + V(-h)] / (F h)^2 - V'/F
    gammaR = (up - 2 * base + dn) / (Fi * H) ** 2 - deltaR / Fi

    cd = np.where(a["call"], unit["delta"], unit["delta"] + 1.0)   # call delta N(d1)
    bucket = np.where(opt, np.array(DELTA_NAMES)[np.clip(np.digitize(cd, DELTA_EDGES) - 1, 0, 4)], "FUT")
    # smile-shape bumps in call delta x: RR +1 pt moves the 25C up 0.5 and the 25P down 0.5,
    # BF +1 pt moves both 25-deltas up 1, ATM unchanged in both
    phi_rr = 2.0 * (0.5 - cd)
    phi_bf = ((cd - 0.5) / 0.25) ** 2

    q = a["qty"]
    out = pd.DataFrame({
        "sym": [r["sym"] for r in a["rows"]], "book": [r["book"] for r in a["rows"]],
        "expiry": [mkt.labels[i] for i in s], "bucket": bucket, "kind": np.where(opt, "option", "future"),
        "qty": q, "K": a["K"], "F": Fi, "T": Ti, "vol": vol, "calldelta": np.where(opt, cd, np.nan),
        "price": unit["price"], "uvega": unit["vega"],
        "mtm": q * unit["price"],
        "delta": q * unit["delta"],
        "deltaR": q * deltaR,
        "deltapa": q * (unit["delta"] - np.where(opt, unit["price"], 0.0) / Fi),
        "deltaspot": q * deltaR * Fi / mkt.spot,
        "cashdelta": q * deltaR * Fi * 0.01,
        "gamma": q * gammaR * Fi * 0.01,
        "cashgamma": q * 0.5 * gammaR * (Fi * 0.01) ** 2,
        "vega": q * unit["vega"] / 100,
        "theta": q * unit["theta"] / 365,
        "vanna": q * unit["vanna"] / 100,
        "volga": q * unit["volga"] / 1e4,
    })
    out["vatm"] = out["vega"]
    out["vrr"] = out["vega"] * phi_rr
    out["vbf"] = out["vega"] * phi_bf
    out.attrs["missing"] = missing
    out.attrs["R"] = R
    return out


RISK_COLS = ["mtm", "delta", "deltaR", "deltapa", "deltaspot", "cashdelta", "gamma", "cashgamma",
             "vega", "theta", "vanna", "volga", "vatm", "vrr", "vbf"]


def aggregate(pg: pd.DataFrame) -> pd.DataFrame:
    """Risk rows: the total, then by expiry, then by delta bucket (options only)."""
    if pg.empty:
        return pd.DataFrame()
    parts = []
    for book, g in pg.groupby("book"):
        tot = g[RISK_COLS].sum().to_frame().T.assign(book=book, kind="total", bucket="ALL")
        ex = g.groupby("expiry")[RISK_COLS].sum().reset_index().rename(columns={"expiry": "bucket"})
        db = g[g["kind"] == "option"].groupby("bucket")[RISK_COLS].sum().reset_index()
        parts += [tot, ex.assign(book=book, kind="expiry"), db.assign(book=book, kind="delta")]
    out = pd.concat(parts, ignore_index=True)
    out["R"] = pg.attrs.get("R", np.nan)
    return out[["book", "kind", "bucket", "R"] + RISK_COLS]


def scenario_grid(rows, mkt: Market, R: float, spots=GRID_SPOT, vols=GRID_VOL, now=None) -> pd.DataFrame:
    """Full-revaluation P&L for every (spot move, vol shift) pair: one C++ call for the grid."""
    a, _ = arrays(rows, mkt)
    F, T, P = mkt.smiles(now)
    xs, vs = np.meshgrid(np.log1p(np.array(spots)), np.array(vols, dtype=float) / 100, indexing="ij")
    m = xs.size
    vals = _core.book_values(a["qty"], a["K"], a["slice"], a["call"], a["future"], F, T, P,
                             xs.ravel(), vs.ravel(), np.ones(m), np.zeros(m), R)
    base = _values(a, F, T, P, R=R)
    ds, dv = np.meshgrid(spots, vols, indexing="ij")
    return pd.DataFrame({"dspot": ds.ravel(), "dvol": dv.ravel(), "pnl": vals - base, "R": R})


def pnl_explain(rows, m0: Market, m1: Market, R: float = 1.0) -> pd.DataFrame:
    """Explain each position's P&L from market state m0 (at m0.asof) to m1 (at m1.asof).

    Taylor terms use time-0 Greeks, the change in each expiry's forward dF, and the change in
    each option's implied vol AT ITS OWN STRIKE, d sigma = sigma_1(K) - sigma_0(K):
        delta  D dF      gamma  G dF^2/2     vega  V d sigma     theta  Th dt
        vanna  Va dF d sigma                 volga  Vo d sigma^2/2
        unexplained = actual - (sum of the above)
    The vega term is then split by the smile rule: "smile" is the vol change rule R predicts
    from the forward move alone; "surf" is the rest (the market re-marking vols).
    """
    live = [r for r in rows if m0.index(label_of(r)) >= 0 and m1.index(label_of(r)) >= 0]
    if not live:
        return pd.DataFrame()
    a0, _ = arrays(live, m0)
    a1, _ = arrays(live, m1)
    F0, T0, P0 = m0.smiles()
    F1, T1, P1 = m1.smiles()
    s0, s1 = a0["slice"], a1["slice"]
    opt = ~a0["future"]
    K = np.where(opt, a0["K"], F0[s0])
    sig0 = np.where(opt, _core.smile_vol(F0, T0, P0, s0, K, 0.0, 1.0), 0.2)
    sig1 = np.where(opt, _core.smile_vol(F1, T1, P1, s1, K, 0.0, 1.0), 0.2)
    dF = F1[s1] - F0[s0]
    dlnF = np.log(F1[s1] / F0[s0])
    dt = (m1.asof - m0.asof) / YEAR_NS
    q = a0["qty"]

    v0 = _values(a0, F0, T0, P0, each=True)
    v1 = _values(a1, F1, T1, P1, each=True)
    g = px.greeks(F0[s0], K, T0[s0], sig0, a0["call"])
    D = np.where(opt, g["delta"], 1.0)
    z = lambda k: np.where(opt, g[k], 0.0)                         # noqa: E731
    dsig = np.where(opt, sig1 - sig0, 0.0)
    # rule-predicted vol change per option, from the forward move alone
    pred = np.array([float(_core.smile_vol(F0, T0, P0, s0[i:i + 1], K[i:i + 1], dlnF[i], R)[0])
                     for i in range(len(q))])
    dsig_rule = np.where(opt, pred - sig0, 0.0)

    out = pd.DataFrame({
        "sym": [r["sym"] for r in live], "book": [r["book"] for r in live],
        "actual": v1 - v0,
        "delta": q * D * dF,
        "gamma": q * 0.5 * z("gamma") * dF ** 2,
        "vega": q * z("vega") * dsig,
        "theta": q * z("theta") * dt,
        "vanna": q * z("vanna") * dF * dsig,
        "volga": q * 0.5 * z("volga") * dsig ** 2,
        "smile": q * z("vega") * dsig_rule,
        "surf": q * z("vega") * (dsig - dsig_rule),
    })
    terms = ["delta", "gamma", "vega", "theta", "vanna", "volga"]
    out["unexpl"] = out["actual"] - out[terms].sum(axis=1)
    out.attrs.update(R=R, start=m0.asof, end=m1.asof)
    return out


def pnl_rows(ex: pd.DataFrame) -> pd.DataFrame:
    """Book-level P&L explain rows (one per book)."""
    cols = ["actual", "delta", "gamma", "vega", "theta", "vanna", "volga", "unexpl", "smile", "surf"]
    out = ex.groupby("book")[cols].sum().reset_index()
    out["R"] = ex.attrs["R"]
    out["start"], out["end"] = ex.attrs["start"], ex.attrs["end"]
    return out
