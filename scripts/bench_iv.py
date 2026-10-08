"""Benchmark: C++ (pybind11) vs pure Python for pricing and implied vol.

    python scripts/bench_iv.py

Uses a synthetic chain shaped like Deribit's (~1,000 options), so it needs no
network and gives the same workload every run. Reports time per option.
These numbers involve no kdb+, so they are fine to publish (PLAN.md, licence notes).
"""
import time

import numpy as np

import pricing as px
from pricing import reference as ref

rng = np.random.default_rng(42)
N = 1_000
F = np.full(N, 80_000.0)
K = F * np.exp(rng.uniform(-1.0, 1.0, N))
T = rng.uniform(1 / 365, 1.5, N)
S = rng.uniform(0.3, 1.2, N)
C = rng.random(N) < 0.5
P = px.price(F, K, T, S, C)


def best_of(fn, repeat=5):
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return min(times)


rows = []
t_cpp = best_of(lambda: px.price(F, K, T, S, C), 20)
t_np = best_of(lambda: ref.price(F, K, T, S, C), 20)
rows.append(("price, whole chain", t_cpp, "NumPy vectorised", t_np))

t_cpp = best_of(lambda: px.implied_vol(P, F, K, T, C), 20)
t_py = best_of(lambda: [ref.implied_vol(*a) for a in zip(P, F, K, T, C)], 3)
rows.append(("implied vol, whole chain", t_cpp, "SciPy brentq loop", t_py))

t_cpp = best_of(lambda: px.greeks(F, K, T, S, C), 20)
rows.append(("price + 12 Greeks", t_cpp, "-", float("nan")))

print(f"{N:,} options, best of several runs\n")
print(f"{'task':28s} {'C++ per option':>15s}   {'Python baseline':>20s} {'per option':>12s} {'speed-up':>9s}")
for task, tc, name, tp in rows:
    sp = f"{tp / tc:,.0f}x" if np.isfinite(tp) else "-"
    tp_s = f"{tp / N * 1e6:,.2f} us" if np.isfinite(tp) else "-"
    print(f"{task:28s} {tc / N * 1e6:>12.3f} us   {name:>20s} {tp_s:>12s} {sp:>9s}")
