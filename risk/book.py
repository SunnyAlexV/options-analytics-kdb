"""Positions: what the book holds, and a realistic sample book built off the live surface.

A position row (the ``pos`` table):
    sym     instrument, e.g. BTC-25DEC26-90000-C, or BTC-25DEC26 for a future on that expiry
    book    book name (a desk can run several)
    kind    option | future
    expiry  epoch ns;  strike (USD; NaN for futures);  cp  C | P (blank for futures)
    qty     contracts (1 BTC underlying each), negative = short
    entry   entry price in USD (for futures: the price the P&L is measured from)
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
from scipy.special import ndtri

from pricing import _core

from .market import Market

COLS = ("sym", "book", "kind", "expiry", "strike", "cp", "qty", "entry")


def load_csv(path) -> list[dict]:
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out.append({"sym": r["sym"], "book": r["book"], "kind": r["kind"], "expiry": int(r["expiry"]),
                        "strike": float(r["strike"]) if r["strike"] else math.nan, "cp": r["cp"],
                        "qty": float(r["qty"]), "entry": float(r["entry"])})
    return out


def save_csv(rows, path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if (c == "strike" and not np.isfinite(r[c])) else r[c]) for c in COLS})


def label_of(pos: dict) -> str:
    """Expiry label of a position: BTC-25DEC26-90000-C -> BTC-25DEC26."""
    return "-".join(pos["sym"].split("-")[:2])


def arrays(rows, mkt: Market):
    """Book as arrays for the C++ functions. Positions whose expiry has no smile are reported
    in ``missing`` and left out (expired, or not fitted yet)."""
    idx = np.array([mkt.index(label_of(r)) for r in rows], dtype=np.int64)
    ok = idx >= 0
    sel = [r for r, o in zip(rows, ok) if o]
    a = {
        "qty": np.array([r["qty"] for r in sel], dtype=float),
        "K": np.array([r["entry"] if r["kind"] == "future" else r["strike"] for r in sel], dtype=float),
        "slice": idx[ok],
        "call": np.array([r["cp"] == "C" for r in sel], dtype=bool),
        "future": np.array([r["kind"] == "future" for r in sel], dtype=bool),
        "rows": sel,
    }
    return a, [r for r, o in zip(rows, ok) if not o]


# ----------------------------------------------------------------- sample book
# A small BTC volatility desk. Each leg: (expiry target in days, call delta of the strike,
# C/P, quantity in BTC). Strikes are chosen ON THE LIVE SMILE, then snapped to listed strikes.
SAMPLE_LEGS = [
    # long front-month gamma and vega: ATM straddle
    (30, 0.50, "C", +25), (30, 0.50, "P", +25),
    # skew: buy the 3-month 25-delta call, sell the 25-delta put (a risk reversal)
    (90, 0.25, "C", +20), (90, 0.75, "P", -20),
    # calendar: short 1-week ATM calls against long 2-month ATM calls
    (7, 0.50, "C", -30), (60, 0.50, "C", +30),
    # tail hedge: 1-month 10-delta puts
    (30, 0.90, "P", +40),
]


def _strike_for_delta(params, F, T, call_delta):
    """Strike whose forward call delta N(d1) equals call_delta on the smile (fixed-point on k)."""
    k = 0.0
    for _ in range(50):
        w = max(float(_core.svi_w(list(params), np.array([k]))[0]), 1e-12)
        k_new = -ndtri(call_delta) * math.sqrt(w) + 0.5 * w     # d1 = (-k + w/2)/sqrt(w)
        if abs(k_new - k) < 1e-10:
            break
        k = 0.5 * (k + k_new)
    return F * math.exp(k)


def _nice_strike(K: float, F: float) -> float:
    """Round to a strike-like grid when the listed strikes are unknown: steps of 10^(digits-2)
    of the forward (BTC at 80,000 -> 1,000; SOL at 110 -> 10; TRX at 0.33 -> 0.01)."""
    step = 10.0 ** (math.floor(math.log10(F)) - 1)
    return round(K / step) * step


def sample_book(mkt: Market, listed: dict[str, list[float]] | None = None,
                legs=SAMPLE_LEGS, hedge_rule_R: float | None = None, book: str = "sample",
                scale: float = 1.0) -> list[dict]:
    """Resolve SAMPLE_LEGS against the live surface. ``listed``: expiry label -> listed strikes
    (from the ref table), so every leg is a real instrument. With ``hedge_rule_R``, a future on
    the front leg's expiry is added that makes the book's rule delta zero: every forward is
    assumed to move by the same log amount, so it offsets sum(deltaR_i * F_i) / F_hedge.
    For coins with no dated futures (the USDC coins) that hedge is a synthetic forward: valued at
    our parity forward, F - entry per unit, exactly like a future.
    ``scale`` multiplies every quantity: the per-coin books use BTC spot / coin spot, so each leg
    has the same USD notional as the BTC book's."""
    from .core import position_greeks              # local import: core imports this module
    F, T, P = mkt.smiles()
    usable = [i for i in range(len(mkt.labels)) if T[i] * 365 >= 2]
    rows = []
    for days, cd, cp, qty in legs:
        i = min(usable, key=lambda j: abs(T[j] * 365 - days))
        K = _strike_for_delta(P[i], F[i], T[i], cd)
        if listed and listed.get(mkt.labels[i]):
            K = min(listed[mkt.labels[i]], key=lambda s: abs(s - K))
        else:
            K = _nice_strike(K, F[i])
        sym = f"{mkt.labels[i]}-{K:g}-{cp}".replace(".", "d")    # Deribit writes 8.5 as 8d5
        if any(r["sym"] == sym for r in rows):           # two legs on one instrument: merge
            next(r for r in rows if r["sym"] == sym)["qty"] += qty * scale
            continue
        rows.append({"sym": sym, "book": book, "kind": "option", "expiry": int(mkt.expiry[i]),
                     "strike": float(K), "cp": cp, "qty": float(qty) * scale, "entry": math.nan})
    # entry prices: today's model value, so P&L starts from zero
    g = position_greeks(rows, mkt, R=1.0 if hedge_rule_R is None else hedge_rule_R)
    for r, v in zip(rows, g["price"]):
        r["entry"] = float(v)
    if hedge_rule_R is not None:
        i = mkt.index(label_of(rows[0]))
        rows.append({"sym": mkt.labels[i], "book": book, "kind": "future", "expiry": int(mkt.expiry[i]),
                     "strike": math.nan, "cp": "", "qty": -float(np.sum(g["deltaR"] * g["F"]) / F[i]), "entry": float(F[i])})
    return rows
