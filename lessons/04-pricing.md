# Phase 3 walkthrough: the C++ pricing library

**What was built:** a C++ library that prices options, solves for implied vol and computes the full Greek set. It's exposed to Python through pybind11, tested in both languages, and validated against Deribit's own numbers.

```
cpp/include/oak/black76.hpp   the interface: conventions and units for every function
cpp/src/black76.cpp           the maths
cpp/src/bindings.cpp          pybind11: NumPy arrays in, NumPy arrays out
cpp/tests/test_black76.cpp    GoogleTest suite (7 tests)
pricing/__init__.py           Python API: broadcasting, BTC conversions, expiry helpers
pricing/reference.py          pure-Python version, for cross-checks and benchmarks
tests/test_pricing.py         pytest suite (8 tests)
scripts/validate_deribit.py   live comparison with Deribit's mark IVs
scripts/bench_iv.py           C++ vs Python timings
CMakeLists.txt, pyproject.toml  build: scikit-build-core runs CMake when you pip install
```

## 1. Black-76

Each Deribit expiry has its own forward F, so options are priced on the forward:

$$C = D\,[F\,N(d_1) - K\,N(d_2)],\qquad P = D\,[K\,N(-d_2) - F\,N(-d_1)]$$

$$d_{1,2} = \frac{\ln(F/K) \pm \tfrac12\sigma^2T}{\sigma\sqrt T},\qquad D = e^{-rT}$$

Deribit uses r = 0, because the forward already contains the cost of carry.

**One implementation detail that matters:** N(x) is computed as ½·erfc(−x/√2), not ½(1 + erf(x/√2)). For a deep out-of-the-money option, N(d) is around 10⁻²⁰. In the erf version, 1 + erf(…) adds two numbers of opposite sign that almost cancel, and all 16 digits are lost. erfc computes the small tail directly.

## 2. Inverse options: why a BTC premium is the USD price divided by F

A Deribit call pays (S_T − K)⁺ / S_T **in BTC**. In USD that's (S_T − K)⁺, an ordinary call payoff. Pricing it in each currency and converting gives:

$$V_{\text{BTC}} = \frac{P^{\text{USD}}(0,T)\,\mathbb E^{\text{USD}}[(S_T-K)^+]}{S_0} = P^{\text{BTC}}(0,T)\,\frac{\text{Black}(F,K,T,\sigma)}{F}$$

The second step uses F = S₀ · P^BTC / P^USD, the forward implied by the two currencies' discount factors (P^BTC and P^USD are the prices today of 1 BTC and 1 USD paid at T).

**Deribit sets the BTC discount factor to 1.** So:

$$V_{\text{BTC}} = \text{Black}(F)/F$$

That's `pricing.to_btc(price, F)`. It's also why converting at spot "failed" in testing: using S₀ is only correct if you also discount at the USD rate implied by the basis, and Deribit doesn't.

**Premium-adjusted delta.** A BTC-based trader measures P&L in BTC, and the premium itself is in BTC. Differentiating V_BTC = V/F:

$$F\,\frac{\partial V_{\text{BTC}}}{\partial F} = \Delta - \frac{V}{F}$$

That's `premium_adjusted_delta`, the number of BTC to hedge with. A unit test checks it against a finite difference of the BTC price.

## 3. The Greeks

All Greeks are derivatives of the USD value with the forward held fixed. Each one is checked in C++ against **bump and revalue**: nudge the input, reprice, and compare. It's checked for 8 parameter sets × calls and puts, with relative error below 10⁻⁴ to 10⁻⁶.

Write n(·) for the normal density, s = σ√T, and a dot for a derivative with respect to calendar time t (= −∂/∂T). Then:

| Greek | Formula |
|---|---|
| Δ (delta) | call D·N(d₁); put −D·N(−d₁) |
| Γ (gamma) | D·n(d₁) / (F·s) |
| vega | D·F·n(d₁)·√T |
| Θ (theta) | −D·F·n(d₁)·σ / (2√T) + r·V |
| ρ (rho) | −T·V (the forward is fixed, so only discounting moves) |
| vanna (∂Δ/∂σ) | −D·n(d₁)·d₂ / σ |
| volga (∂vega/∂σ) | vega · d₁·d₂ / σ |
| charm (Δ̇) | r·Δ + D·n(d₁)·d₂ / (2T) |
| veta (vėga) | vega · (r − (1 + d₁d₂)/(2T)) |
| speed (∂Γ/∂F) | −(Γ/F)·(1 + d₁/s) |
| zomma (∂Γ/∂σ) | Γ·(d₁d₂ − 1) / σ |
| colour (Γ̇) | Γ·(r + (1 − d₁d₂)/(2T)) |

The time Greeks all rest on one identity: **∂d₁/∂T = −d₂/(2T)**. To see it, write d₁ = a·T^(−½) + b·T^(½), with a = ln(F/K)/σ and b = σ/2, and differentiate. For example, charm follows from:

$$\frac{\partial}{\partial T}\big[D\,N(d_1)\big] = -rD\,N(d_1) - D\,n(d_1)\,\frac{d_2}{2T}$$

Units: vega is per 1.00 of vol, so divide by 100 for "per vol point". Theta is per year, so divide by 365 for per day. Crypto trades every calendar day.

## 4. The implied-vol solver

**The problem:** find σ with Black(σ) = market price. Price rises strictly with σ, so there's at most one answer.

1. **Use the out-of-the-money side.** Convert the input with put-call parity, C − P = D(F − K). An in-the-money price is mostly intrinsic value, so its time value, the only part that carries vol information, sits in its last few digits.
2. **Check the no-arbitrage bounds.** An out-of-the-money option must be worth between 0 and D·F (call) or D·K (put). Outside that range, no vol exists, so the solver returns a **status code** rather than a number: 1 = below intrinsic / no time value, 2 = above the maximum, 4 = bad input.
3. **Start from a good guess:** the larger of
   - Brenner–Subrahmanyam's √(2π/T)·p/(DF), from the at-the-money approximation, and
   - Manaster–Koehler's √(2|ln F/K|/T), where vega is largest.
4. **Safeguarded Newton.** Each step is σ ← σ − (price − target)/vega, with a bracket [lo, hi] that always contains the answer and shrinks every iteration. If a Newton step would leave the bracket, or vega is near zero, it **bisects** instead. Newton is fast near the answer; bisection guarantees convergence everywhere else.

**A bug found by the tests:** I first stopped when the price error fell below 10⁻¹²·F. For an option worth 10⁻⁵ USD, that tolerance is about 10⁸ times the price, so the solver stopped far too early, with vol errors up to 5×10⁻⁵. The tolerance is now **relative to the option's own price**.

**How accurate can it be?** A double holds about 16 significant digits, so the input price is only known to about ε·price, where ε ≈ 2.2×10⁻¹⁶. Dividing by vega converts that into the smallest vol error *any* solver could achieve:

$$\text{limit} = 4\,\varepsilon\,\text{price}/\text{vega}$$

On 454 round trips (moneyness ±150%, 1 day to 3 years, vols 5% to 400%):
- the worst error is **0.57 of that limit**;
- the worst absolute error is 2.9×10⁻⁸, on a deep in-the-money input where the information simply isn't in the number;
- the most iterations needed was 23, with a median of 6.

## 5. Results

**Benchmark** (`python scripts/bench_iv.py`, 1,000 options):

| Task | C++ per option | Python baseline per option | Speed-up |
|---|---|---|---|
| Price, whole chain | 0.08 µs | 0.43 µs (NumPy, vectorised) | ~5× |
| Implied vol, whole chain | **0.62 µs** | 3,910 µs (SciPy `brentq`, looped) | **~6,000×** |
| Price + 12 Greeks | 0.17 µs | — | — |

These are my workspace's numbers; run the script for yours. NumPy is already compiled C under the hood, so the pricing speed-up is modest. The implied-vol speed-up is huge because the Python version calls a root-finder once per option, and every iteration pays Python's per-call overhead. These numbers involve no kdb+, so they can be published.

**Validation against Deribit** (`python scripts/validate_deribit.py`):
- **Price check:** Black-76 at Deribit's `mark_iv`, divided by F, reproduces Deribit's BTC mark price to a median of about 10⁻⁵ BTC.
- **Vol check:** on about 726 well-conditioned options, our implied vols match `mark_iv` to a **median of 0.5–3 bp**, depending on the snapshot. Deribit rounds `mark_iv` to 1 bp.
- **"Well-conditioned"** means one vol point moves the price by at least one tick (0.0001 BTC). For the other ~234, mostly options a few days from expiry, a rounding-sized price difference becomes a huge vol difference, so they're reported separately rather than averaged in.
- **Where the remaining gaps come from:** divide each option's price gap by its delta, and you get the forward shift that would explain it. Across snapshots it was a few dollars to about $20 on $82,000, and larger while BTC was moving. That's consistent with each mark and its reported forward being computed a moment apart. Phase 4 can test this cleanly using our own synchronised quotes.

## 6. Build and run

```bash
pip install -e .                     # compiles the C++ and installs `pricing` (rerun after C++ changes)
pytest                               # 19 Python tests (11 feed + 8 pricing)
cmake -S . -B build/cpp-tests -DOAK_BUILD_TESTS=ON -DOAK_BUILD_PYTHON=OFF
cmake --build build/cpp-tests -j
ctest --test-dir build/cpp-tests --output-on-failure      # 7 C++ tests
python scripts/validate_deribit.py
python scripts/bench_iv.py
```

Try it in Python:

```python
import pricing as px
px.price(F=82_000, K=[75_000, 82_000, 90_000], T=30/365, sigma=0.45, call="C")
px.greeks(82_000, 90_000, 30/365, 0.45, "C")["vega"] / 100     # USD per vol point
px.implied_vol(1_500, 82_000, 90_000, 30/365, "C")
```

## 7. Questions

1. Why does the solver convert an in-the-money price to its out-of-the-money twin first? Work through C = 20,000.0001 with intrinsic value 20,000: how many digits of time value survive in a double?
2. `rho` here is −T·V, much smaller than the textbook Black–Scholes rho. Why? (Hint: what is held fixed?)
3. For a BTC trader, is a long call's premium-adjusted delta larger or smaller than its plain delta? What does that mean for the hedge?
4. The vol agreement with Deribit is worse when BTC is moving fast. Design a test, using our own `quote` and `snap` tables, that would confirm or rule out the timing explanation.
