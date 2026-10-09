"""Market state for risk: one fitted smile and forward per expiry, as of a moment in time.

Built from the surface engine's ``surface`` rows (the arbitrage-free SVI fit, columns
afa..afsigma), so risk is computed off exactly the surface the system publishes.

Time: an SVI fit gives total variance w(k) = sigma(k)^2 * T_fit. Valued at a later moment
with T_now left, we keep each point's implied VOL (the standard "sticky in time" choice),
so w_now(k) = w_fit(k) * T_now / T_fit. Raw SVI is linear in (a, b) for fixed
(rho, m, sigma), so that is just a and b scaled by T_now / T_fit.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

NS = 1_000_000_000
YEAR_NS = 365 * 24 * 3600 * NS
AF = ("afa", "afb", "afrho", "afm", "afsigma")


@dataclass
class Market:
    asof: int                 # epoch ns the state refers to
    spot: float               # BTC index (USD); used only to express delta in spot-equivalent BTC
    labels: list              # expiry labels, e.g. "BTC-25DEC26", sorted by expiry
    expiry: np.ndarray        # epoch ns
    F: np.ndarray             # forward per expiry (USD)
    T_fit: np.ndarray         # year fraction at fit time
    params: np.ndarray        # (n, 5) arbitrage-free SVI parameters, total variance at T_fit

    @classmethod
    def from_surface(cls, rows, asof: int, spot: float | None = None) -> "Market":
        """Latest surface row per expiry; expiries already past ``asof`` are dropped."""
        latest = {}
        for r in rows:
            latest[r["sym"]] = r                      # rows arrive in time order: last one wins
        keep = sorted((r for r in latest.values() if r["expiry"] > asof and np.isfinite(r["afa"])),
                      key=lambda r: r["expiry"])
        F = np.array([r["F"] for r in keep], dtype=float)
        if spot is None or not np.isfinite(spot):
            spot = float(F[0]) if len(F) else float("nan")   # nearest forward: within a few bp of spot
        return cls(asof, float(spot), [r["sym"] for r in keep],
                   np.array([r["expiry"] for r in keep], dtype=np.int64), F,
                   np.array([r["T"] for r in keep], dtype=float),
                   np.array([[r[c] for c in AF] for r in keep], dtype=float).reshape(-1, 5))

    def index(self, label: str) -> int:
        return self.labels.index(label) if label in self.labels else -1

    def T(self, now: int | None = None) -> np.ndarray:
        now = self.asof if now is None else now
        return (self.expiry - now) / YEAR_NS

    def smiles(self, now: int | None = None):
        """(F, T_now, params rescaled to T_now) as arrays for the C++ risk functions."""
        T = np.maximum(self.T(now), 1e-9)
        P = self.params.copy()
        scale = T / self.T_fit
        P[:, 0] *= scale
        P[:, 1] *= scale
        return self.F, T, P
