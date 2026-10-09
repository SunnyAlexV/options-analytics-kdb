"""The surface engine: market data in, implied vols / forwards / fitted smiles out.

Transport-independent: live mode (engine/rte.py), replay and the evaluation harness
all drive this same class, so what is evaluated is exactly what runs live.

    on_ref(rows)        instrument reference data (expiry, strike, call/put)
    on_snap(rows)       Deribit's own forwards (used as fallback and for comparison)
    on_quotes(rows)     -> iv rows, one per quote update, computed immediately
    refit(now_ns)       -> (fwd rows, surface rows) for expiries whose quotes changed,
                           throttled to at most once per `throttle_s`

Row formats match feed/schema.py ("iv", "fwd", "surface"); timestamps are int ns UTC.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

import pricing as px
from pricing import _core

from .forward import parity_forward
from .smile import SliceConfig, build_slice, params_of, quote_ivs, smile_metrics

NS = 1_000_000_000
YEAR_NS = 365 * 24 * 3600 * NS
NAN = float("nan")


def _f(x):
    """None/NaN-safe float."""
    return NAN if x is None else float(x)


@dataclass
class EngineConfig:
    throttle_s: float = 0.5        # refit an expiry at most this often
    cold_every_s: float = 60.0     # full multi-start refit at least this often (guards against drifting)
    full_every_s: float = 10.0     # mark everything dirty this often (safety net)
    fwd_max_rel_se: float = 1e-3   # use the parity forward unless its std error exceeds 0.1% of F
                                   # (harness, 9 Oct 2026: parity beat Deribit's forward by ~11% in
                                   # out-of-sample error on both halves of the data; the old 0.02%
                                   # gate made it fall back to Deribit almost always)
    forward_source: str = "parity" # "parity" (fallback to Deribit) or "deribit"
    slice: SliceConfig = field(default_factory=SliceConfig)


@dataclass
class _Fit:
    raw: dict | None = None
    af: dict | None = None
    last_cold_ns: int = 0


class SurfaceEngine:
    def __init__(self, cfg: EngineConfig | None = None, asset: str = "BTC"):
        self.cfg = cfg or EngineConfig()
        self.asset = asset
        self.ref: dict[str, tuple[int, float, str, str]] = {}   # sym -> (expiry_ns, strike, cp, expiry label)
        self.book: dict[str, tuple] = {}                         # sym -> (bid, bsize, ask, asize, exch)
        self.by_exp: dict[str, set] = {}                          # expiry label -> syms
        self.exp_ns: dict[str, int] = {}
        self.deribit_fwd: dict[str, tuple[float, int]] = {}      # expiry -> (und, exch)
        self.fwd: dict[str, tuple[float, float, str]] = {}       # expiry -> (F, D, source) used for pricing
        self.fits: dict[str, _Fit] = {}
        self.dirty: set[str] = set()
        self.last_refit_ns = 0
        self.last_full_ns = 0
        self.stats = {"quotes": 0, "ivs": 0, "refits": 0, "fit_s": 0.0}

    # ---------------------------------------------------------------- inputs
    def on_ref(self, rows) -> None:
        for r in rows:
            if r.get("kind") not in (None, "option") or r.get("cp") not in ("C", "P"):
                continue
            sym = r["sym"]
            label = sym.rsplit("-", 2)[0]                      # BTC-25DEC26-80000-C -> BTC-25DEC26
            self.ref[sym] = (int(r["expiry"]), float(r["strike"]), r["cp"], label)
            self.by_exp.setdefault(label, set()).add(sym)
            self.exp_ns[label] = int(r["expiry"])

    def on_snap(self, rows) -> None:
        for r in rows:
            info = self.ref.get(r["sym"])
            und = _f(r.get("und"))
            if info and math.isfinite(und):
                self.deribit_fwd[info[3]] = (und, int(r["exch"]))
                if info[3] not in self.fwd:                    # bootstrap pricing until parity is available
                    self.fwd[info[3]] = (und, 1.0, "deribit")

    def on_quotes(self, rows) -> list[dict]:
        """Update the book and return one iv row per quote, priced on the current forward."""
        out_idx, F, D, K, T, cp, bid, ask, rows_ok, src = [], [], [], [], [], [], [], [], [], []
        for r in rows:
            sym = r["sym"]
            info = self.ref.get(sym)
            self.stats["quotes"] += 1
            if info is None:
                continue
            exch = int(r["exch"])
            self.book[sym] = (_f(r["bid"]), _f(r["bsize"]), _f(r["ask"]), _f(r["asize"]), exch)
            self.dirty.add(info[3])
            fw = self.fwd.get(info[3])
            t = (info[0] - exch) / YEAR_NS
            if fw is None or t <= 0:
                continue
            rows_ok.append(r); F.append(fw[0]); D.append(fw[1]); src.append(fw[2])
            K.append(info[1]); T.append(t); cp.append(info[2] == "C")
            bid.append(_f(r["bid"])); ask.append(_f(r["ask"]))
        if not rows_ok:
            return []
        F, D, K, T, cp = map(np.asarray, (F, D, K, T, cp))
        bid, ask = np.asarray(bid), np.asarray(ask)
        biv, aiv, miv = quote_ivs(bid, ask, F, D, K, T, cp)
        g = px.greeks(F, K, T, np.where(np.isfinite(miv), miv, 0.5), cp)
        out = []
        for i, r in enumerate(rows_ok):
            m = miv[i]
            fin = math.isfinite(m)
            out.append({
                "sym": r["sym"], "asset": self.asset, "exch": int(r["exch"]),
                "F": float(F[i]), "fsrc": src[i], "T": float(T[i]),
                "bidiv": float(biv[i]), "askiv": float(aiv[i]), "midiv": float(m),
                "delta": float(g["delta"][i]) if fin else NAN,
                "gamma": float(g["gamma"][i]) if fin else NAN,
                "vega": float(g["vega"][i]) / 100 if fin else NAN,      # USD per vol point
                "theta": float(g["theta"][i]) / 365 if fin else NAN,    # USD per calendar day
            })
        self.stats["ivs"] += len(out)
        return out

    # ---------------------------------------------------------------- refit
    def _expiry_rows(self, label):
        rows = []
        for sym in self.by_exp.get(label, ()):
            b = self.book.get(sym)
            if b is not None:
                _, K, cp, _ = self.ref[sym]
                rows.append((K, cp, b[0], b[1], b[2], b[3]))
        return rows

    def _update_forward(self, label, now_ns) -> dict:
        calls, puts = {}, {}
        for sym in self.by_exp.get(label, ()):
            b = self.book.get(sym)
            if b is None:
                continue
            _, K, cp, _ = self.ref[sym]
            (calls if cp == "C" else puts)[K] = b
        Ks = sorted(set(calls) & set(puts))
        pf = parity_forward(Ks, [calls[k][0] for k in Ks], [calls[k][2] for k in Ks],
                            [puts[k][0] for k in Ks], [puts[k][2] for k in Ks])
        und, und_t = self.deribit_fwd.get(label, (NAN, 0))
        use_parity = (self.cfg.forward_source == "parity" and pf.ok
                      and pf.se_F / pf.F < self.cfg.fwd_max_rel_se)
        if use_parity:
            self.fwd[label] = (pf.F, pf.D, "parity")
        elif math.isfinite(und):
            self.fwd[label] = (und, 1.0, "deribit")
        F_used, D_used, src = self.fwd.get(label, (NAN, NAN, ""))
        return {"sym": label, "asset": self.asset, "expiry": self.exp_ns.get(label, 0),
                "F": pf.F, "D": pf.D, "seF": pf.se_F, "seD": pf.se_D, "n": pf.n,
                "und": und, "undage": (now_ns - und_t) / NS if und_t else NAN,
                "diff": pf.F - und if pf.ok and math.isfinite(und) else NAN,
                "Fused": F_used, "fsrc": src}

    def refit(self, now_ns: int, force: bool = False) -> tuple[list, list]:
        cfg = self.cfg
        if not force and (now_ns - self.last_refit_ns) < cfg.throttle_s * NS:
            return [], []
        if now_ns - self.last_full_ns >= cfg.full_every_s * NS:
            self.dirty |= set(self.by_exp)
            self.last_full_ns = now_ns
        if not self.dirty:
            return [], []
        self.last_refit_ns = now_ns
        fwd_rows = []
        for label in sorted(self.dirty):
            if self.exp_ns.get(label, 0) > now_ns:
                fwd_rows.append(self._update_forward(label, now_ns))
        changed = set(self.dirty)
        self.dirty.clear()

        t0 = time.perf_counter()
        # Raw fits for changed expiries (warm-started; cold multi-start periodically)
        for label in changed:
            F, D, _ = self.fwd.get(label, (NAN, NAN, ""))
            T = (self.exp_ns.get(label, 0) - now_ns) / YEAR_NS
            sl = build_slice(self._expiry_rows(label), F, D, T, cfg.slice)
            st = self.fits.setdefault(label, _Fit())
            if sl is None or len(sl.k) < 5:
                st.raw = None
                continue
            cold = st.raw is None or (now_ns - st.last_cold_ns) >= cfg.cold_every_s * NS
            init = None if cold else params_of(st.raw)
            fit = _core.fit_svi(sl.k, sl.iv, sl.hs, sl.wt, sl.T, False, None, init, cfg.slice.hs_floor)
            if not cold and st.raw is not None:
                # a warm start must not be worse than starting afresh: if it degraded, redo cold
                if fit["wrmse"] > 1.25 * st.raw.get("wrmse", np.inf) + 0.05:
                    fit = _core.fit_svi(sl.k, sl.iv, sl.hs, sl.wt, sl.T, False, None, None, cfg.slice.hs_floor)
                    cold = True
            if cold:
                st.last_cold_ns = now_ns
            fit.update({"T": sl.T, "F": F, "n": len(sl.k), "cold": cold, "slice": sl})
            st.raw = fit

        # Arbitrage-free chain over all fitted expiries, shortest first (calendar needs the previous one)
        prev = None
        for label in sorted(self.fits, key=lambda e: self.exp_ns.get(e, 0)):
            st = self.fits[label]
            if st.raw is None:
                continue
            sl = st.raw["slice"]
            # calendar arbitrage in the RAW fit, against the previous arbitrage-free slice
            # same grid the arbitrage-free fit enforces on: the data range plus 25% each side,
            # because extrapolated wings can cross even when the quoted range does not
            span = max(sl.k.max() - sl.k.min(), 0.05)
            grid = np.linspace(sl.k.min() - 0.25 * span, sl.k.max() + 0.25 * span, 101)
            st.raw["calv"] = (float(np.max(_core.svi_w(prev, grid) - _core.svi_w(params_of(st.raw), grid)))
                              if prev is not None else 0.0)
            af = _core.fit_svi(sl.k, sl.iv, sl.hs, sl.wt, sl.T, True, prev, params_of(st.raw), cfg.slice.hs_floor)
            st.af = af
            prev = params_of(af)
        self.stats["fit_s"] += time.perf_counter() - t0
        self.stats["refits"] += 1

        surf_rows = []
        for label, st in self.fits.items():
            if st.raw is None or st.af is None:
                continue
            raw, af = st.raw, st.af
            key = (tuple(params_of(af)), raw["T"])
            if st.__dict__.get("_mkey") != key:
                st.__dict__["_met"], st.__dict__["_mkey"] = smile_metrics(params_of(af), raw["T"]), key
            met = st.__dict__["_met"]
            surf_rows.append({
                "sym": label, "asset": self.asset, "expiry": self.exp_ns[label], "T": raw["T"],
                "F": raw["F"], "fsrc": self.fwd.get(label, (0, 0, ""))[2], "n": raw["n"],
                "a": raw["a"], "b": raw["b"], "rho": raw["rho"], "m": raw["m"], "sigma": raw["sigma"],
                "rmse": raw["rmse_vol"], "wrmse": raw["wrmse"], "inband": raw["inside_band"],
                "ming": raw["min_g"], "calv": max(raw.get("calv", 0.0), 0.0),
                "afa": af["a"], "afb": af["b"], "afrho": af["rho"], "afm": af["m"], "afsigma": af["sigma"],
                "afrmse": af["rmse_vol"], "afinband": af["inside_band"], "afming": af["min_g"],
                "afcal": af["max_cal"],
                "arbgap": af["rmse_vol"] - raw["rmse_vol"],     # cost of removing arbitrage, in vol
                "atmvol": met["atmvol"], "rr25": met["rr25"], "bf25": met["bf25"],
                "cold": bool(raw["cold"]),
            })
        return fwd_rows, surf_rows
