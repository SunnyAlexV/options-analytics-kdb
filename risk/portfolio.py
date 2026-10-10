"""Joint VaR / ES across every coin's book: one historical day = one scenario for ALL books at once.

For each historical day d, every market moves by its own actual move that day:
    forwards of coin a move by   r_a,d = ln(S_a,d / S_a,d-1)          (its own index)
    its vols are scaled by       exp(v_a,d),  v_a,d = ln(VOL_a,d / VOL_a,d-1)
where VOL is the coin's own DVOL for BTC and ETH (and their USDC markets), and BTC's DVOL for
coins Deribit publishes no vol index for (a stated proxy; risk/history.py). Because every coin
takes the move of the SAME day, the correlation between coins is in the scenarios themselves:
nothing is estimated, and fat-tailed joint moves (a crypto-wide sell-off) are kept as they were.

    portfolio P&L_d = sum over coins of  P&L_a,d      (each a full revaluation in C++)

Reported, for HS and FHS (risk/var.py, the same methods as each coin's own VaR):
  - joint VaR 99%, ES 97.5%, ES 99%
  - each coin's standalone VaR / ES on the same days, and their sums
  - diversification benefit = sum of standalone - joint. For ES this is never negative (ES is
    subadditive on a common set of scenarios); for VaR it can be (VaR is not).
  - each coin's contribution to ES 97.5%: its average P&L on the portfolio's worst 2.5% of days
    (Euler allocation). The contributions add up exactly to the joint ES.
  - two variants: "stress" (the proxied coins' vol moves x1.5, showing how much the proxy matters)
    and "allcoins" (every coin including those with a short history, on the days they all share)
  - a hypothetical-P&L backtest of the joint VaR 99% with Kupiec's test
  - the correlation of daily moves over the window
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .market import Market
from .var import kupiec, moves, pnl_vector, var_es

STRESS = 1.5


def aligned_moves(hists: dict[str, pd.DataFrame], assets) -> pd.DataFrame:
    """Each coin's daily moves (computed on its OWN full history, so its EWMA volatilities are
    identical to its standalone VaR's), inner-joined on date: columns r_<a>, v_<a>, sr_<a>, sv_<a>,
    and the 'now' EWMA forecasts in .attrs."""
    parts, now = [], {}
    for a in assets:
        m = moves(hists[a])
        now[a] = (float(m["sr_now"].iloc[-1]), float(m["sv_now"].iloc[-1]))
        parts.append(m[["date", "r", "v", "sr", "sv"]].rename(
            columns={c: f"{c}_{a}" for c in ("r", "v", "sr", "sv")}).set_index("date"))
    out = pd.concat(parts, axis=1, join="inner").sort_index().reset_index()
    out.attrs["now"] = now
    return out


def _scen(mv: pd.DataFrame, a: str, method: str, vmult: float = 1.0):
    r, v = mv[f"r_{a}"].to_numpy(), mv[f"v_{a}"].to_numpy() * vmult
    if method == "fhs":
        sr_now, sv_now = mv.attrs["now"][a]
        r = r * sr_now / mv[f"sr_{a}"].to_numpy()
        v = v * sv_now / mv[f"sv_{a}"].to_numpy()
    return r, v


def pnl_matrix(books: dict, mkts: dict, mv: pd.DataFrame, method: str, R: float,
               vmult: dict | None = None) -> pd.DataFrame:
    """Days x coins matrix of each book's full-revaluation P&L (USD)."""
    cols = {}
    for a in books:
        r, v = _scen(mv, a, method, (vmult or {}).get(a, 1.0))
        cols[a] = pnl_vector(books[a], mkts[a], r, v, R)
    return pd.DataFrame(cols, index=mv["date"])


def es_contributions(P: pd.DataFrame, level: float = 0.975) -> pd.Series:
    """Each coin's average P&L loss on the portfolio's worst ceil(N (1-level)) days: these sum
    exactly to the joint ES at that level (the same days var_es averages over)."""
    tot = P.sum(axis=1).to_numpy()
    k = max(int(math.ceil((1 - level) * len(tot))), 1)
    worst = np.argsort(tot)[:k]                    # most negative P&L first
    return -P.iloc[worst].mean(axis=0)


def summarise(P: pd.DataFrame) -> dict:
    joint = var_es(P.sum(axis=1).to_numpy())
    alone = {a: var_es(P[a].to_numpy()) for a in P.columns}
    s_var = sum(x["var99"] for x in alone.values())
    s_es = sum(x["es975"] for x in alone.values())
    return {"joint": joint, "alone": alone, "sum_var99": s_var, "sum_es975": s_es,
            "div_var99": s_var - joint["var99"], "div_es975": s_es - joint["es975"],
            "contrib_es975": es_contributions(P).to_dict(), "n": len(P)}


def compute(books: dict, mkts: dict, hists: dict, R: float, window: int = 365,
            proxied=(), stress: float = STRESS) -> dict:
    """The full portfolio report. ``books``/``mkts``/``hists``: coin -> positions / Market /
    daily history. The main set is every coin whose history covers ``window`` days; the
    "allcoins" variant adds the rest on the days every coin shares."""
    assets = [a for a in books if a in mkts and a in hists]
    n_hist = {a: len(hists[a]) - 1 for a in assets}
    main = [a for a in assets if n_hist[a] >= window]
    short = [a for a in assets if a not in main]
    out = {"main": main, "short": short, "window": window, "R": R, "methods": {}}
    mv = aligned_moves(hists, main).tail(window) if main else None
    if mv is not None:
        mv.attrs["now"] = aligned_moves(hists, main).attrs["now"]
        out["first"], out["last"] = mv["date"].iloc[0], mv["date"].iloc[-1]
        out["corr"] = mv[[f"r_{a}" for a in main]].corr().set_axis(main, axis=0).set_axis(main, axis=1)
    for method in ("hs", "fhs"):
        res = {}
        if mv is not None:
            P = pnl_matrix({a: books[a] for a in main}, mkts, mv, method, R)
            res["joint"] = summarise(P)
            vm = {a: stress for a in main if a in proxied}
            res["stress"] = summarise(pnl_matrix({a: books[a] for a in main}, mkts, mv, method, R, vm))
        if short:
            mv_all = aligned_moves(hists, assets)
            mv_all = mv_all.tail(min(window, len(mv_all)))
            res["allcoins"] = summarise(pnl_matrix(books, mkts, mv_all, method, R))
            res["allcoins"]["first"], res["allcoins"]["last"] = mv_all["date"].iloc[0], mv_all["date"].iloc[-1]
        out["methods"][method] = res
    return out


def backtest(books: dict, mkts: dict, hists: dict, R: float, main, window: int = 365,
             test_days: int = 365) -> dict:
    """Hypothetical-P&L backtest of the joint VaR 99% (as risk/var.py does for one book): for each
    of the last ``test_days`` days, forecast VaR from the ``window`` days before it, then compare
    with what today's portfolio would have made on that day's actual moves of every coin."""
    full = aligned_moves(hists, main).reset_index(drop=True)
    start = max(window, len(full) - test_days)
    if start >= len(full):
        return {}
    real_mv = full.iloc[start:].copy()
    real_mv.attrs["now"] = {}
    real = pnl_matrix({a: books[a] for a in main}, mkts, real_mv, "hs", R).sum(axis=1).to_numpy()
    res = {}
    for method in ("hs", "fhs"):
        exc, fc = 0, []
        for j, t in enumerate(range(start, len(full))):
            hist_mv = full.iloc[t - window:t].copy()
            if method == "fhs":                      # scale the past days to day t's forecasts
                for a in main:
                    for c in ("r", "v"):
                        hist_mv[f"{c}_{a}"] = hist_mv[f"{c}_{a}"] * full[f"s{c}_{a}"].iloc[t] / hist_mv[f"s{c}_{a}"]
            hist_mv.attrs["now"] = {}
            P = pnl_matrix({a: books[a] for a in main}, mkts, hist_mv, "hs", R)
            v99 = var_es(P.sum(axis=1).to_numpy())["var99"]
            fc.append(v99)
            exc += int(-real[j] > v99)
        n = len(full) - start
        res[method] = {"exceptions": exc, "days": n, "expected": 0.01 * n, "kupiec_p": kupiec(exc, n),
                       "mean_var99": float(np.mean(fc))}
    return res


def scale_for(asset: str, spot: float, ref_spot: float) -> float:
    """Quantity multiplier for a coin's sample book: the same USD notional per leg as the BTC book."""
    if asset.split("_")[0] == "BTC" or not (np.isfinite(spot) and spot > 0 and np.isfinite(ref_spot)):
        return 1.0
    return float(ref_spot / spot)

