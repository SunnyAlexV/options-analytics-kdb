"""Forward and discount factor implied by put-call parity, from the whole chain.

The line fit below serves every premium convention (engine/conventions.py): inverse
(Deribit, shown in detail here) and linear (NSE, MCX), where C - P = D (F - K).

For Deribit's inverse options (prices in BTC), put-call parity is exact and model-free:

    C_btc(K) - P_btc(K) = D_btc * (1 - K / F)

Proof: the call pays (S-K)+/S BTC and the put (K-S)+/S BTC, so C - P pays (S - K)/S
= 1 - K/S BTC at expiry. Its value today is D_btc * (1 - K * E^BTC[1/S_T]), and under
the BTC-numeraire measure E^BTC[1/S_T] = 1/F, where F is the USD forward.

So y = C - P is a straight line in K: y = alpha + beta*K, with alpha = D and
beta = -D/F. A weighted least-squares fit across every strike with two-sided call
and put quotes gives BOTH the forward (F = -alpha/beta) and the BTC discount
factor (D = alpha). Each point is weighted by 1/variance of its mid difference,
where each mid's uncertainty is its half-spread. One outlier-rejection pass drops
points more than 4 standard deviations from the line.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .conventions import DERIBIT, Convention

HALF_TICK_BTC = 0.5e-4      # Deribit's option tick is 0.0001 BTC: no quote is more precise than half of it


@dataclass
class ParityForward:
    F: float          # implied forward, USD
    D: float          # implied BTC discount factor (Deribit's convention assumes 1)
    se_F: float       # standard error of F, USD
    se_D: float
    n: int            # strikes used
    ok: bool


def parity_forward(K, c_bid, c_ask, p_bid, p_ask, min_points: int = 3,
                   conv: Convention = DERIBIT, tick: float | None = None) -> ParityForward:
    """Weighted regression of (call mid - put mid) on strike, prices in premium units
    (BTC for Deribit inverse, USDC for Deribit linear, INR for NSE). No quote is more precise
    than half a tick: the instruments' own tick if given, else the convention's."""
    half_tick = 0.5 * (tick if tick and tick > 0 else conv.tick)
    K, cb, ca, pb, pa = (np.asarray(x, dtype=float) for x in (K, c_bid, c_ask, p_bid, p_ask))
    good = np.isfinite(cb) & np.isfinite(ca) & np.isfinite(pb) & np.isfinite(pa) & (ca >= cb) & (pa >= pb)
    K, cb, ca, pb, pa = K[good], cb[good], ca[good], pb[good], pa[good]
    bad = ParityForward(np.nan, np.nan, np.nan, np.nan, int(len(K)), False)
    if len(K) < min_points:
        return bad

    y = 0.5 * (ca + cb) - 0.5 * (pa + pb)
    var = np.maximum(0.5 * (ca - cb), half_tick) ** 2 + np.maximum(0.5 * (pa - pb), half_tick) ** 2
    keep = np.ones(len(K), dtype=bool)
    for _ in range(2):                                  # fit, drop >4-sigma outliers, refit
        if keep.sum() < min_points:
            return bad
        X = np.column_stack([np.ones(keep.sum()), K[keep]])
        w = 1.0 / var[keep]
        XtW = X.T * w
        cov = np.linalg.inv(XtW @ X)
        alpha, beta = cov @ (XtW @ y[keep])
        resid = (y - alpha - beta * K) / np.sqrt(var)
        dof = max(keep.sum() - 2, 1)
        chi2 = float(np.sum(resid[keep] ** 2) / dof)
        new_keep = np.abs(resid) <= 4.0 * np.sqrt(max(chi2, 1.0))
        if (new_keep == keep).all():
            break
        keep = new_keep
    if not beta < 0:
        return bad
    cov = cov * max(chi2, 1.0)                          # inflate if the line fits worse than the spreads imply
    F, D = conv.parity_FD(alpha, beta)
    grad = np.array([-1.0 / beta, alpha / beta ** 2])  # dF/d(alpha, beta): F = -alpha/beta in both conventions
    se_F = float(np.sqrt(grad @ cov @ grad))
    gD = conv.parity_grad_D()
    se_D = float(np.sqrt(gD @ cov @ gD))
    return ParityForward(float(F), float(D), se_F, se_D, int(keep.sum()), True)
