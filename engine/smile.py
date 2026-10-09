"""Turning one expiry's quotes into SVI fit inputs, and fitted smiles into desk metrics."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.special import ndtr

import pricing as px
from pricing import _core


@dataclass
class SliceConfig:
    min_days: float = 2.0         # expiries closer than this are not fitted (too few meaningful quotes)
    max_hs: float = 0.15          # drop quotes whose half-spread exceeds 15 vol points: no information
    max_abs_k: float = 1.5        # ignore strikes more than e^1.5 (~4.5x) away from the forward
    size_weight: bool = False     # quote-size weights: harness found no consistent gain, 10x more jitter
    side: str = "best"            # "best": tighter of call/put per strike; "otm": OTM only (harness: a tie)
    hs_floor: float = 0.0025      # minimum half-spread used for weights (0.25 vol points)


@dataclass
class SliceData:
    k: np.ndarray
    iv: np.ndarray
    hs: np.ndarray
    wt: np.ndarray
    strike: np.ndarray
    side: np.ndarray              # "C" or "P": which side supplied each point
    T: float
    F: float
    extra: dict = field(default_factory=dict)


def quote_ivs(bid_btc, ask_btc, F, D, K, T, cp):
    """Bid, ask and mid implied vols from BTC quotes.

    BTC price -> undiscounted USD Black price: V_usd = V_btc * F / D  (lessons/04, section 2).
    The mid IV is the IV of the mid price; the half-spread in vol is (askIV - bidIV)/2.
    """
    to_usd = np.asarray(F, dtype=float) / np.asarray(D, dtype=float)
    bid = np.asarray(bid_btc, dtype=float) * to_usd
    ask = np.asarray(ask_btc, dtype=float) * to_usd
    biv = px.implied_vol(bid, F, K, T, cp)
    aiv = px.implied_vol(ask, F, K, T, cp)
    miv = px.implied_vol(0.5 * (bid + ask), F, K, T, cp)
    return biv, aiv, miv


def build_slice(rows: list, F: float, D: float, T: float, cfg: SliceConfig) -> SliceData | None:
    """rows: (strike, cp, bid, bsize, ask, asize) for one expiry, BTC prices.

    At every strike, use whichever side (call or put) has the tighter market in vol.
    Put-call parity makes them carry the same vol information, so this keeps the
    best quote per strike instead of throwing half the chain away by "OTM only".
    """
    if T * 365 < cfg.min_days or not np.isfinite(F) or not rows:
        return None
    a = np.array([(r[0], r[1] == "C", r[2], r[3], r[4], r[5]) for r in rows], dtype=float)
    K, call, bid, bsz, ask, asz = a.T
    ok = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask >= bid)
    K, call, bid, bsz, ask, asz = K[ok], call[ok].astype(bool), bid[ok], bsz[ok], ask[ok], asz[ok]
    if len(K) == 0:
        return None
    biv, aiv, miv = quote_ivs(bid, ask, F, D, K, T, call)
    hs = 0.5 * (aiv - biv)
    good = np.isfinite(biv) & np.isfinite(aiv) & np.isfinite(miv) & (hs > 0) & (hs <= cfg.max_hs)
    good &= np.abs(np.log(K / F)) <= cfg.max_abs_k
    if good.sum() == 0:
        return None
    K, call, miv, hs, size = K[good], call[good], miv[good], hs[good], np.minimum(bsz[good], asz[good])

    if cfg.side == "otm":                 # conventional alternative: OTM options only
        otm = np.where(call, K >= F, K < F)
        K, call, miv, hs, size = K[otm], call[otm], miv[otm], hs[otm], size[otm]
        if len(K) == 0:
            return None
    # keep the tighter side per strike
    order = np.lexsort((hs, K))
    K, call, miv, hs, size = K[order], call[order], miv[order], hs[order], size[order]
    first = np.r_[True, K[1:] != K[:-1]]
    K, call, miv, hs, size = K[first], call[first], miv[first], hs[first], size[first]

    wt = np.ones(len(K))
    if cfg.size_weight:   # deeper quotes count more, with diminishing returns
        wt = np.sqrt(np.maximum(size, 1e-9) / np.median(np.maximum(size, 1e-9)))
        wt = np.clip(wt, 0.25, 4.0)
    return SliceData(np.log(K / F), miv, hs, wt, K, np.where(call, "C", "P"), T, F)


# ------------------------------------------------------------------ metrics
PARAMS = ("a", "b", "rho", "m", "sigma")


def params_of(fit: dict) -> list:
    return [fit[p] for p in PARAMS]


def smile_vol(params, k, T):
    return np.sqrt(np.maximum(_core.svi_w(list(params), np.atleast_1d(np.asarray(k, dtype=float))), 0) / T)


_KGRID = np.linspace(-5.0, 5.0, 4001)       # log-moneyness grid for delta-to-strike conversion


def delta_strike(params, T, call_delta: float) -> float:
    """Log-moneyness k where the forward call delta N(d1) equals call_delta, on the fitted smile.

    d1(k) = (-k + w(k)/2) / sqrt(w(k)). Evaluated on a fine grid in one vectorised call, then
    interpolated (N(d1) falls as k rises, so the grid is reversed for np.interp).
    """
    w = np.maximum(_core.svi_w(list(params), _KGRID), 1e-12)
    nd1 = ndtr((-_KGRID + 0.5 * w) / np.sqrt(w))
    return float(np.interp(call_delta, nd1[::-1], _KGRID[::-1]))


def smile_metrics(params, T) -> dict:
    """ATM vol, 25-delta risk reversal and butterfly, in vol (decimal).

    RR25 = vol(25-delta call) - vol(25-delta put)        (the skew: > 0 means calls are bid)
    BF25 = (vol(25d call) + vol(25d put))/2 - ATM vol     (the curvature / wing premium)
    A 25-delta put has call delta 0.75 (put delta = N(d1) - 1 = -0.25).
    """
    atm = float(smile_vol(params, 0.0, T)[0])
    kc, kp = delta_strike(params, T, 0.25), delta_strike(params, T, 0.75)
    vc, vp = float(smile_vol(params, kc, T)[0]), float(smile_vol(params, kp, T)[0])
    return {"atmvol": atm, "rr25": vc - vp, "bf25": 0.5 * (vc + vp) - atm}
