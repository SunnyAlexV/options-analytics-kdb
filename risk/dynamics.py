"""How does BTC's smile move when the price moves? Measure it, then score the smile rules.

Rule family (cpp/include/oak/risk.hpp): after a log forward move x, the vol at strike K is
    sigma_new(K) = sigma_old(k - (1 - R) x),  k = ln(K/F_old)
so to first order the vol AT A FIXED STRIKE changes by
    d sigma(K) = -(1 - R) x sigma'(k)                      (sigma' = d sigma / dk)
R = 1 sticky-strike (no change), R = 0 sticky-moneyness (the smile slides with F).

Estimation: regress the observed fixed-strike vol change y = sigma_{t+h}(K) - sigma_t(K) on
z = -x sigma'(k) through the origin. Slope beta = 1 - R, so R_hat = 1 - beta.
All strikes in one interval share the same x, so they are not independent: the standard
error is clustered by interval.

Scoring (out of sample): for each candidate R, on intervals NOT used to estimate it,
  - vol error:    observed sigma_{t+h}(K) minus the rule's prediction from the move alone
  - hedged P&L:   each option's change in value minus its rule delta times dF (Hull-White's
                  criterion: the better smile rule gives the better hedge)
Each option is a standardised point on its smile (call deltas 10..90), one unit long, so
no strike region is over-weighted by how densely Deribit lists strikes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.special import ndtr

import pricing as px
from pricing import _core

from .market import NS, Market

CALL_DELTAS = np.array([0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90])
TENORS = [("< 2w", 0, 14), ("2w-2m", 14, 60), ("> 2m", 60, 10_000)]


def snapshots(surface_rows, every_s: float, min_days: float = 2.0) -> list[Market]:
    """Market state every ``every_s`` seconds: the latest fit of each expiry at that moment.
    Rows need a ``now`` (publication time, ns) field."""
    rows = sorted(surface_rows, key=lambda r: r["now"])
    if not rows:
        return []
    t, end, i, latest, out = rows[0]["now"], rows[-1]["now"], 0, {}, []
    step = int(every_s * NS)
    t += step
    while t <= end:
        while i < len(rows) and rows[i]["now"] <= t:
            latest[rows[i]["sym"]] = rows[i]
            i += 1
        m = Market.from_surface(latest.values(), asof=t)
        keep = m.T() * 365 >= min_days
        if keep.any():
            out.append(_subset(m, keep))
        t += step
    return out


def _subset(m: Market, keep) -> Market:
    idx = np.flatnonzero(keep)
    return Market(m.asof, m.spot, [m.labels[i] for i in idx], m.expiry[idx], m.F[idx], m.T_fit[idx],
                  m.params[idx])


def _strikes(m: Market):
    """For every expiry: strikes at the standard call deltas on its smile."""
    F, T, P = m.smiles()
    kg = np.linspace(-3, 3, 2001)
    out = []
    for i in range(len(m.labels)):
        w = np.maximum(_core.svi_w(list(P[i]), kg), 1e-12)
        nd1 = ndtr((-kg + 0.5 * w) / np.sqrt(w))                # call delta, falls as k rises
        out.append(F[i] * np.exp(np.interp(CALL_DELTAS, nd1[::-1], kg[::-1])))
    return out


def pairs(snaps: list[Market]) -> pd.DataFrame:
    """One row per (interval, expiry, standard strike): everything the estimators need."""
    recs = []
    for j, (m0, m1) in enumerate(zip(snaps[:-1], snaps[1:])):
        F0, T0, P0 = m0.smiles()
        F1, T1, P1 = m1.smiles()
        Ks = _strikes(m0)
        for i, lab in enumerate(m0.labels):
            i1 = m1.index(lab)
            if i1 < 0:
                continue
            K = Ks[i]
            s = np.full(len(K), i, dtype=np.int64)
            s1 = np.full(len(K), i1, dtype=np.int64)
            x = float(np.log(F1[i1] / F0[i]))
            sig0 = _core.smile_vol(F0, T0, P0, s, K, 0.0, 1.0)
            sig1 = _core.smile_vol(F1, T1, P1, s1, K, 0.0, 1.0)
            slope = _core.smile_slope(F0, T0, P0, s, K)
            call = K >= F0[i]
            g = px.greeks(F0[i], K, T0[i], sig0, call)
            v1 = px.price(F1[i1], K, T1[i1], sig1, call)
            for n in range(len(K)):
                recs.append((j, m0.asof, lab, T0[i] * 365, CALL_DELTAS[n], K[n], F0[i], F1[i1] - F0[i], x,
                             sig0[n], sig1[n], slope[n], g["price"][n], g["delta"][n], g["vega"][n], v1[n]))
    cols = ["pair", "t", "expiry", "days", "cdelta", "K", "F0", "dF", "x", "sig0", "sig1", "slope",
            "V0", "D", "vega", "V1"]
    df = pd.DataFrame(recs, columns=cols)
    df["tenor"] = pd.cut(df["days"], [t[1] for t in TENORS] + [TENORS[-1][2]], labels=[t[0] for t in TENORS],
                         right=False)
    return df


def estimate_R(df: pd.DataFrame) -> dict:
    """R_hat = 1 - beta from y = beta z, clustered (by interval) standard error."""
    y = (df["sig1"] - df["sig0"]).to_numpy()
    z = (-df["x"] * df["slope"]).to_numpy()
    if len(y) < 10 or np.sum(z * z) == 0:
        return {"R": np.nan, "se": np.nan, "n": len(y), "intervals": df["pair"].nunique()}
    szz = np.sum(z * z)
    beta = np.sum(z * y) / szz
    u = y - beta * z
    # cluster-robust variance: sum over intervals of (sum z u)^2, / (sum z^2)^2
    s = pd.Series(z * u).groupby(df["pair"].to_numpy()).sum().to_numpy()
    G = len(s)
    se = np.sqrt(G / max(G - 1, 1) * np.sum(s ** 2)) / szz
    return {"R": 1 - beta, "se": se, "n": len(y), "intervals": G}


def score(df: pd.DataFrame, R: float) -> dict:
    """Out-of-sample errors of rule R on the rows given."""
    pred = df["sig0"] - (1 - R) * df["x"] * df["slope"]               # first-order rule prediction
    vol_err = (df["sig1"] - pred).to_numpy() * 100                     # vol points
    deltaR = df["D"] + df["vega"] * (R - 1) * df["slope"] / df["F0"]
    hedged = (df["V1"] - df["V0"] - deltaR * df["dF"]).to_numpy()
    hedged_bp = hedged / df["F0"].to_numpy() * 1e4                    # per 1 BTC option, bp of F
    return {"vol_rmse": float(np.sqrt(np.mean(vol_err ** 2))), "vol_mae": float(np.mean(np.abs(vol_err))),
            "hedge_rmse_bp": float(np.sqrt(np.mean(hedged_bp ** 2)))}


@dataclass
class RuleResult:
    horizon_s: float
    tenor: str
    fold: str                          # "A->B": R estimated on half A, scored on half B
    R_hat: float
    R_se: float
    intervals: int
    scores: dict                       # rule name -> score dict on the scored half


NAMED = {"sticky-moneyness (R=0)": 0.0, "sticky-strike (R=1)": 1.0}


def evaluate_rules(surface_rows, horizons=(60, 300, 900)) -> list[RuleResult]:
    """For each horizon and tenor group (and all tenors pooled), in both directions:
    estimate R on one half of the intervals, score R=0, R=1 and the estimate on the other."""
    out = []
    for h in horizons:
        df = pairs(snapshots(surface_rows, h))
        if df.empty:
            continue
        cut = df["pair"].max() / 2
        halves = {"A": df[df["pair"] <= cut], "B": df[df["pair"] > cut]}
        for fold, (tr_name, te_name) in {"A->B": ("A", "B"), "B->A": ("B", "A")}.items():
            train, test = halves[tr_name], halves[te_name]
            for name in ["all"] + [t[0] for t in TENORS]:
                tr = train if name == "all" else train[train["tenor"] == name]
                te = test if name == "all" else test[test["tenor"] == name]
                if len(tr) < 50 or len(te) < 50:
                    continue
                est = estimate_R(tr)
                rules = dict(NAMED, **{"estimated": est["R"]})
                out.append(RuleResult(h, name, fold, est["R"], est["se"], est["intervals"],
                                      {k: score(te, r) for k, r in rules.items()}))
    return out


def decide(results: list[RuleResult], horizon_s: float = 300, metric: str = "hedge_rmse_bp",
           min_intervals: int = 20) -> dict:
    """The pre-registered decision rule (results/phase5_smile_rules.md):
    pooled tenors, 5-minute horizon, hedged P&L RMSE. A rule must win on BOTH folds;
    otherwise the named rule with the lower average error is kept."""
    folds = [r for r in results if r.tenor == "all" and r.horizon_s == horizon_s]
    if len(folds) < 2 or min(r.intervals for r in folds) < min_intervals:
        return {"R": float("nan"), "why": f"not enough data: each fold needs at least {min_intervals} "
                                          f"{horizon_s:.0f}-second intervals"}
    winners = {min(r.scores, key=lambda k: r.scores[k][metric]) for r in folds}
    if len(winners) == 1:
        w = winners.pop()
        R = NAMED.get(w, round(float(np.mean([r.R_hat for r in folds])) / 0.05) * 0.05)
        return {"R": R, "rule": w, "why": "won on both folds"}
    avg = {k: np.mean([r.scores[k][metric] for r in folds]) for k in NAMED}
    w = min(avg, key=avg.get)
    return {"R": NAMED[w], "rule": w, "why": f"no rule won both folds ({sorted(winners)}); "
                                           f"kept the better named rule on average"}


def print_results(results: list[RuleResult]) -> None:
    for r in results:
        print(f"\nhorizon {r.horizon_s:>4.0f} s | tenor {r.tenor:<6} | fold {r.fold} | R_hat = {r.R_hat:+.2f} "
              f"± {r.R_se:.2f}  ({r.intervals} intervals)")
        print(f"  {'rule':<28}{'vol RMSE (pts)':>16}{'vol MAE (pts)':>15}{'hedged P&L RMSE (bp of F)':>28}")
        best = min(s["hedge_rmse_bp"] for s in r.scores.values())
        for name, s in r.scores.items():
            label = f"estimated (R={r.R_hat:+.2f})" if name == "estimated" else name
            mark = "  <- best hedge" if s["hedge_rmse_bp"] == best else ""
            print(f"  {label:<28}{s['vol_rmse']:>16.4f}{s['vol_mae']:>15.4f}{s['hedge_rmse_bp']:>28.3f}{mark}")
