"""Evaluation harness: which engine design choices actually make the surface better?

Every configuration is replayed through the real engine on the same recorded data
and scored on two things:

1. Out-of-sample accuracy. Each time a smile is fitted at time t, use it to price
   every quote for that expiry that arrives in (t, t + horizon] -- quotes the fit
   has not seen. Score each one in units of that quote's own half-spread:
       z = (model price - quote mid) / half-spread      (all in BTC)
   |z| <= 1 means the prediction landed inside the bid-ask band. Scoring in PRICE
   against real quotes makes every configuration face the same target, whatever
   forward or weights it uses internally.

2. Stability. How much ATM vol and the 25-delta risk reversal move between
   successive refits. A surface that jumps around while the market is quiet is
   fitting noise.

As a reference, Deribit's own mark price (from the `snap` table) is scored the
same way: does our surface predict the next quotes better than Deribit's mark?
"""
from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass

import numpy as np

import pricing as px
from pricing import _core

from .core import YEAR_NS, EngineConfig
from .replay import replay
from .smile import PARAMS

NS = 1_000_000_000


@dataclass
class Score:
    name: str
    n: int
    med_abs_z: float
    inside: float        # fraction |z| <= 1
    p90_abs_z: float
    jitter_atm_bp: float
    jitter_rr_bp: float
    fit_ms: float


def _future_quotes(data):
    """Two-sided quotes per expiry label, as arrays sorted by receive time."""
    ref = {r["sym"]: r for r in data["ref"] if r.get("cp") in ("C", "P")}
    by = {}
    for q in data["quote"]:
        r = ref.get(q["sym"])
        b, a = q.get("bid"), q.get("ask")
        if r is None or b is None or a is None or not (a > b > 0):
            continue
        label = q["sym"].rsplit("-", 2)[0]
        by.setdefault(label, []).append((q["recv"], float(r["strike"]), r["cp"] == "C",
                                         int(r["expiry"]), 0.5 * (a + b), 0.5 * (a - b), q["sym"]))
    out = {}
    for label, rows in by.items():
        rows.sort(key=lambda x: x[0])
        t, K, c, e, mid, hs, sym = map(np.array, zip(*rows))
        out[label] = {"t": t, "K": K, "call": c, "exp": e, "mid": mid, "hs": hs, "sym": sym}
    return out


def _score_predictions(emissions, fq, horizon_s, key):
    z_all = []
    for (t, label, params, F, D) in emissions[key]:
        q = fq.get(label)
        if q is None:
            continue
        lo, hi = np.searchsorted(q["t"], [t + 1, t + horizon_s * NS])
        if hi <= lo:
            continue
        K, call, mid, hs = q["K"][lo:hi], q["call"][lo:hi], q["mid"][lo:hi], q["hs"][lo:hi]
        T = (q["exp"][lo:hi] - q["t"][lo:hi]) / YEAR_NS
        k = np.log(K / F)
        keep = (np.abs(k) <= 1.5) & (T > 0)
        if not keep.any():
            continue
        # total variance from the fitted smile, vol at each quote's own time to expiry
        vol = np.sqrt(np.maximum(_core.svi_w(list(params), k[keep]), 0) / T[keep])
        model = D * px.price(F, K[keep], T[keep], vol, call[keep]) / F
        z_all.append((model - mid[keep]) / hs[keep])
    return np.concatenate(z_all) if z_all else np.array([])


def evaluate(data, cfg: EngineConfig, name: str, horizon_s: float = 10.0) -> list[Score]:
    emissions = {"raw": [], "af": []}
    series = {}

    last = {}

    def on_output(now, ivs, fwd, surf, eng):
        for r in surf:
            label = r["sym"]
            raw = [r[p] for p in PARAMS]
            if last.get(label) == raw:      # unchanged smile re-published: not a new fit, skip
                continue
            last[label] = raw
            F, D, _ = eng.fwd.get(label, (r["F"], 1.0, ""))
            emissions["raw"].append((now, label, raw, F, D))
            emissions["af"].append((now, label, [r["af" + p] for p in PARAMS], F, D))
            series.setdefault(label, []).append((r["atmvol"], r["rr25"]))

    t0 = time.perf_counter()
    eng = replay(data, copy.deepcopy(cfg), on_output)
    fit_ms = 1e3 * eng.stats["fit_s"] / max(eng.stats["refits"], 1)
    fq = _future_quotes(data)

    jit_atm, jit_rr = [], []
    for s in series.values():
        a = np.array(s)
        if len(a) > 2:
            jit_atm.append(np.median(np.abs(np.diff(a[:, 0]))))
            jit_rr.append(np.median(np.abs(np.diff(a[:, 1]))))
    out = []
    for key in ("raw", "af"):
        z = np.abs(_score_predictions(emissions, fq, horizon_s, key))
        out.append(Score(f"{name} [{key}]", len(z),
                         float(np.median(z)) if len(z) else math.nan,
                         float(np.mean(z <= 1)) if len(z) else math.nan,
                         float(np.percentile(z, 90)) if len(z) else math.nan,
                         1e4 * float(np.median(jit_atm)) if jit_atm else math.nan,
                         1e4 * float(np.median(jit_rr)) if jit_rr else math.nan, fit_ms))
    print(f"  {name}: replay {time.perf_counter() - t0:.0f}s", flush=True)
    return out


def score_deribit_marks(data, horizon_s: float = 10.0) -> Score:
    """Reference: Deribit's own mark price as the prediction of the next quotes' mids."""
    fq = _future_quotes(data)
    by_sym = {}
    for label, q in fq.items():
        for i, s in enumerate(q["sym"]):
            by_sym.setdefault(s, []).append(i)
    z = []
    for r in data["snap"]:
        mark = r.get("mark")
        if mark is None or not math.isfinite(mark):
            continue
        label = r["sym"].rsplit("-", 2)[0]
        q = fq.get(label)
        if q is None:
            continue
        lo, hi = np.searchsorted(q["t"], [r["recv"] + 1, r["recv"] + horizon_s * NS])
        sel = np.arange(lo, hi)[q["sym"][lo:hi] == r["sym"]]
        if len(sel):
            z.append((mark - q["mid"][sel]) / q["hs"][sel])
    z = np.abs(np.concatenate(z)) if z else np.array([])
    return Score("Deribit mark (reference)", len(z), float(np.median(z)), float(np.mean(z <= 1)),
                 float(np.percentile(z, 90)), math.nan, math.nan, math.nan)


def print_table(scores: list[Score]) -> None:
    print(f"\n{'configuration':40s} {'n':>9s} {'med|z|':>7s} {'in band':>8s} {'p90|z|':>7s} "
          f"{'ATM jit':>8s} {'RR jit':>7s} {'fit ms':>7s}")
    for s in scores:
        print(f"{s.name:40s} {s.n:9,d} {s.med_abs_z:7.3f} {s.inside:8.1%} {s.p90_abs_z:7.2f} "
              f"{s.jitter_atm_bp:7.2f}bp {s.jitter_rr_bp:6.2f}bp {s.fit_ms:7.1f}")
    print("z = (model price - next quote mid) / that quote's half-spread, over the next 10 s; "
          "jitter = median change between successive new fits of the same expiry")
