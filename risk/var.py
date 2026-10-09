"""Value at Risk and Expected Shortfall by historical simulation, with full revaluation.

History (Deribit public API, cached to a CSV so every result is reproducible):
  - btc_usd index, Deribit's settlement index (6-hourly points; we take 00:00 UTC each day)
  - DVOL, Deribit's 30-day BTC implied volatility index (daily candles; close = end of day)
Each historical day d becomes one scenario for today's book:
    every forward moves by    r_d = ln(S_d / S_{d-1})
    every vol is scaled by    exp(v_d),  v_d = ln(DVOL_d / DVOL_{d-1})
    one day passes            dt = 1/365
and the smile moves with the forward under the chosen smile rule R. Spot and vol moves come
from the same day, so their correlation (large in crypto) is kept.

Methods:
  HS   plain historical simulation: the last N days, equally weighted.
  FHS  filtered historical simulation (Barone-Adesi et al.; Hull & White 1998): each day's
       move is rescaled by (today's EWMA volatility / that day's EWMA volatility), lambda = 0.94,
       separately for the price and the vol series. Old calm days are scaled up when the
       market is volatile now, and vice versa.
Measures: VaR 99% (loss exceeded on 1% of days), ES 97.5% (Basel FRTB's measure: the average
loss on the worst 2.5% of days) and ES 99%. Losses are positive numbers in USD.

Limitation, stated in every report: one vol factor (DVOL, a 30-day index) scales the whole
surface proportionally. Term-structure and skew moves are not in this history; the scenario
grid and vega buckets cover them. When our own HDB has a year of fitted surfaces, the same
code can run on those instead (source column says which).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import chi2

from pricing import _core

from .book import arrays
from .history import fetch_history  # noqa: F401  (re-exported: var.fetch_history)
from .market import Market

LAMBDA = 0.94


def moves(hist: pd.DataFrame) -> pd.DataFrame:
    """Daily log moves r (price) and v (vol), plus their EWMA volatilities known BEFORE each day."""
    h = hist.sort_values("date").reset_index(drop=True)
    out = pd.DataFrame({"date": h["date"].iloc[1:].to_numpy(),
                        "r": np.diff(np.log(h["spot"].to_numpy())),
                        "v": np.diff(np.log(h["dvol"].to_numpy()))})
    for c in ("r", "v"):
        x = out[c].to_numpy()
        var = np.empty(len(x))
        var[0] = np.var(x[: min(30, len(x))])                      # seed with the first month
        for i in range(1, len(x)):
            var[i] = LAMBDA * var[i - 1] + (1 - LAMBDA) * x[i - 1] ** 2
        out[f"s{c}"] = np.sqrt(var)                                  # forecast for day i (uses i-1)
        out[f"s{c}_now"] = math.sqrt(LAMBDA * var[-1] + (1 - LAMBDA) * x[-1] ** 2)   # for tomorrow
    return out


def pnl_vector(rows, mkt: Market, r, v, R: float, horizon_days: float = 1.0) -> np.ndarray:
    """Book P&L (USD) under each (r, v) scenario: one C++ call."""
    a, _ = arrays(rows, mkt)
    F, T, P = mkt.smiles()
    m = len(r)
    vals = _core.book_values(a["qty"], a["K"], a["slice"], a["call"], a["future"], F, T, P,
                             np.asarray(r, float), np.zeros(m), np.exp(np.asarray(v, float)),
                             np.full(m, horizon_days / 365), R)
    base = _core.book_values(a["qty"], a["K"], a["slice"], a["call"], a["future"], F, T, P,
                             np.zeros(1), np.zeros(1), np.ones(1), np.zeros(1), R)[0]
    return vals - base


def var_es(pnl: np.ndarray) -> dict:
    """VaR 99%, ES 97.5%, ES 99% as positive USD losses (empirical: the worst ceil(N p) days)."""
    loss = np.sort(-np.asarray(pnl))[::-1]
    n = len(loss)
    k99, k975 = max(int(math.ceil(0.01 * n)), 1), max(int(math.ceil(0.025 * n)), 1)
    return {"var99": float(loss[k99 - 1]), "es975": float(loss[:k975].mean()),
            "es99": float(loss[:k99].mean()), "n": n}


def scenarios(mv: pd.DataFrame, method: str):
    if method == "hs":
        return mv["r"].to_numpy(), mv["v"].to_numpy()
    if method == "fhs":
        return (mv["r"] * mv["sr_now"] / mv["sr"]).to_numpy(), (mv["v"] * mv["sv_now"] / mv["sv"]).to_numpy()
    raise ValueError(method)


def compute(rows, mkt: Market, hist: pd.DataFrame, R: float, window: int = 365,
            source: str = "deribit index+DVOL") -> pd.DataFrame:
    """VaR/ES rows for HS and FHS over the last `window` days."""
    mv = moves(hist).tail(window)
    out = []
    for method in ("hs", "fhs"):
        r, v = scenarios(mv, method)
        res = var_es(pnl_vector(rows, mkt, r, v, R))
        out.append({"method": method, "R": R, "window": len(mv), "src": source,
                    "first": mv["date"].iloc[0], "last": mv["date"].iloc[-1], **res})
    return pd.DataFrame(out)


def kupiec(exceptions: int, n: int, p: float = 0.01) -> float:
    """Kupiec proportion-of-failures test: p-value for 'the exception rate is p'."""
    x = exceptions
    if n == 0:
        return float("nan")
    phat = x / n
    ll0 = (n - x) * math.log(1 - p) + x * math.log(p)
    ll1 = ((n - x) * math.log(1 - phat) if x < n else 0) + (x * math.log(phat) if x > 0 else 0)
    return float(chi2.sf(-2 * (ll0 - ll1), 1))


def backtest(rows, mkt: Market, hist: pd.DataFrame, R: float, window: int = 365, test_days: int = 365):
    """Hypothetical-P&L backtest of VaR 99%: for each of the last `test_days` days, forecast VaR
    from the `window` days before it (HS, and FHS scaled to that day's EWMA forecast), then compare
    with the P&L TODAY'S book would have made on that day's actual moves.
    Tests the method on this book's shape; it is not a backtest of a real trading history."""
    mv = moves(hist).reset_index(drop=True)
    start = max(window, len(mv) - test_days)
    a_r, a_v = mv["r"].to_numpy(), mv["v"].to_numpy()
    sr, sv = mv["sr"].to_numpy(), mv["sv"].to_numpy()
    real = pnl_vector(rows, mkt, a_r[start:], a_v[start:], R)
    res = {}
    for method in ("hs", "fhs"):
        exc, fc = 0, []
        for j, t in enumerate(range(start, len(mv))):
            r, v = a_r[t - window:t], a_v[t - window:t]
            if method == "fhs":                                    # scale to day t's forecast vol
                r = r * sr[t] / sr[t - window:t]
                v = v * sv[t] / sv[t - window:t]
            var99 = var_es(pnl_vector(rows, mkt, r, v, R))["var99"]
            fc.append(var99)
            exc += int(-real[j] > var99)
        n = len(mv) - start
        res[method] = {"exceptions": exc, "days": n, "expected": 0.01 * n, "kupiec_p": kupiec(exc, n),
                       "mean_var99": float(np.mean(fc))}
    return res
