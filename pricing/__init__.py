"""Black-76 pricing, implied volatility and Greeks, computed in C++ (pricing._core).

Every function accepts scalars or NumPy arrays and broadcasts them like NumPy
does, so a whole option chain is priced in one call:

    >>> import pricing as px
    >>> px.price(F=80_000, K=[70_000, 80_000, 90_000], T=0.25, sigma=0.5, call=True)

Units: F, K and prices in USD; T in years (ACT/365); sigma as a decimal (0.5 = 50%).
``call`` may be booleans or the strings "C"/"P". See cpp/include/oak/black76.hpp
for the exact definition of every Greek.

Deribit's options are *inverse* (premium paid and settled in BTC). Use
``to_btc`` / ``from_btc`` to convert prices and ``premium_adjusted_delta`` for
the delta a BTC-denominated trader actually hedges with.
"""
from __future__ import annotations

import numpy as np

from . import _core

SECONDS_PER_YEAR = 365 * 24 * 3600

IV_STATUS = {0: "ok", 1: "below_intrinsic", 2: "above_maximum", 3: "no_convergence", 4: "bad_input"}

GREEKS = ("price", "delta", "gamma", "vega", "theta", "rho", "vanna",
          "volga", "charm", "veta", "speed", "zomma", "colour")


def _calls(call) -> np.ndarray:
    c = np.asarray(call)
    if c.dtype.kind in "USO":                      # "C"/"P" strings
        return np.char.upper(c.astype(str)) == "C"
    return c.astype(bool)


def _prep(*arrays):
    """Broadcast inputs to a common shape and flatten to contiguous 1-D arrays."""
    b = np.broadcast_arrays(*[np.asarray(a) for a in arrays])
    shape = b[0].shape
    flat = [np.ascontiguousarray(x, dtype=float).ravel() for x in b[:-1]]
    flat.append(np.ascontiguousarray(b[-1], dtype=bool).ravel())
    return shape, flat


def _out(x: np.ndarray, shape):
    x = x.reshape(shape)
    return x.item() if x.ndim == 0 else x


def price(F, K, T, sigma, call=True, r=0.0):
    """Black-76 option value (USD)."""
    shape, a = _prep(F, K, T, sigma, r, _calls(call))
    return _out(_core.price(*a), shape)


def greeks(F, K, T, sigma, call=True, r=0.0) -> dict:
    """Dict of price and all 12 Greeks (see module docstring for units)."""
    shape, a = _prep(F, K, T, sigma, r, _calls(call))
    return {k: _out(v, shape) for k, v in _core.greeks(*a).items()}


def implied_vol(price, F, K, T, call=True, r=0.0, tol=1e-12, max_iter=100, full=False):
    """Implied vol from a USD option price. NaN where no vol fits.

    With ``full=True`` also returns the status codes (see IV_STATUS) and iteration counts.
    """
    shape, a = _prep(price, F, K, T, r, _calls(call))
    sig, status, iters = _core.implied_vol(*a, tol, max_iter)
    sig = _out(sig, shape)
    if full:
        return sig, _out(status, shape), _out(iters, shape)
    return sig


# ---------------------------------------------------------------- inverse options
def to_btc(price_usd, F):
    """USD option value -> BTC premium (Deribit quotes in BTC): V_btc = V_usd / F."""
    return np.asarray(price_usd) / np.asarray(F)


def from_btc(price_btc, F):
    """BTC premium -> USD option value."""
    return np.asarray(price_btc) * np.asarray(F)


def premium_adjusted_delta(F, K, T, sigma, call=True, r=0.0):
    """Delta of an inverse option in BTC terms: delta - V_usd / F.

    The premium is itself paid in BTC, so it rises and falls with the BTC price.
    A BTC-denominated trader hedging with this number is hedged in BTC P&L.
    """
    g = greeks(F, K, T, sigma, call, r)
    return np.asarray(g["delta"]) - np.asarray(g["price"]) / np.asarray(F)


# ------------------------------------------------------------------ conventions
def year_fraction(start_ns, expiry_ns):
    """ACT/365 year fraction between two UTC epoch-nanosecond timestamps."""
    return (np.asarray(expiry_ns, dtype=float) - np.asarray(start_ns, dtype=float)) / 1e9 / SECONDS_PER_YEAR


def deribit_expiry_ns(sym: str) -> int:
    """'BTC-25DEC26-80000-C' -> expiry (08:00 UTC on 25 Dec 2026) as epoch ns."""
    import datetime as dt
    code = sym.split("-")[1]                        # e.g. 25DEC26 or 9OCT26
    d = dt.datetime.strptime(code[:-5].zfill(2) + code[-5:], "%d%b%y")
    return int(d.replace(hour=8, tzinfo=dt.timezone.utc).timestamp()) * 1_000_000_000
