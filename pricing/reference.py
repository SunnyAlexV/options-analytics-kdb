"""Pure-Python reference implementation (NumPy/SciPy) of the same maths.

Two jobs:
  1. an independent check: the C++ results must match these to ~1e-12
  2. the baseline for benchmarks: how much faster is C++ than plain Python?

Deliberately written the straightforward way (SciPy's brentq root-finder,
one option at a time), as a typical first Python version would be.
"""
import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm


def price(F, K, T, sigma, call=True, r=0.0):
    D = np.exp(-r * T)
    sd = sigma * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    c = D * (F * norm.cdf(d1) - K * norm.cdf(d2))
    p = D * (K * norm.cdf(-d2) - F * norm.cdf(-d1))
    return np.where(call, c, p)


def implied_vol(target, F, K, T, call=True, r=0.0):
    """One option at a time with Brent's method on [1e-8, 20]."""
    f = lambda s: price(F, K, T, s, call, r) - target  # noqa: E731
    try:
        return brentq(f, 1e-8, 20.0, xtol=1e-14, rtol=1e-14, maxiter=200)
    except ValueError:
        return np.nan
