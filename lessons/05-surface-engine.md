# Phase 4 walkthrough: the real-time surface engine

**What was built:** a streaming engine that turns every quote into an implied vol, estimates each expiry's forward from the whole option chain, and fits two smiles per expiry: one as the market quotes it, one arbitrage-free.

Every design choice was then **tested on real data** rather than assumed.

```
engine/core.py         SurfaceEngine: the calculations, transport-independent
engine/forward.py      forward + BTC discount factor from put-call parity (weighted regression)
engine/smile.py        quotes -> fit inputs; fitted smile -> ATM vol, 25-delta RR and BF
engine/__main__.py     the live process: stream mode (tickerplant subscriber) or poll mode
engine/replay.py       drive the engine through recorded data on a simulated clock
engine/harness.py      score design choices out of sample
cpp/src/svi.cpp        SVI calibration in C++: quasi-explicit + Levenberg-Marquardt + arbitrage constraints
cpp/tests/test_svi.cpp 8 GoogleTests
tests/test_engine.py   7 pytest tests
scripts/eval_surface.py   run the harness
results/phase4_harness.md the numbers behind the decisions below
```

## 1. The principle: use all the information, weight it by reliability, prove it

The brief was "process the most information". The refinement agreed was that **extra information only helps if it's weighted by how reliable it is**, and that every choice should be **proven by out-of-sample tests**. Section 7 shows two choices where the data overruled the default.

## 2. Architecture

```
tickerplant ──(quote, snap, ref)──► surface engine ──(iv, fwd, surface)──► tickerplant ──► RDB/HDB/gateway
```

- **Stream mode** is the standard kdb+ real-time engine. Like the RDB, it calls `.u.sub` and receives every update as `(upd; table; rows)`.
  - **Subtle detail:** the subscription replies can interleave with live updates. Updates that arrive during subscription are buffered, not mistaken for replies.
- **Poll mode** reads only rows it hasn't seen, by row index, every 100 ms. It's also lossless, and uses nothing but plain queries.
- **Every quote → an implied vol immediately:** bid IV, ask IV and mid IV, plus delta, gamma, vega and theta. That's 284,356 quotes → 284,356 IV rows in the 29-minute test.
- **Changed expiries → refit at most every 0.5 s.** Fits are warm-started from the previous fit (0.09 ms), with a cold multi-start at least every 60 s (8.6 ms) so the fit can't drift into a bad local minimum. A warm fit that comes out worse than the previous one is automatically redone cold.
- Results go **back through the tickerplant**, so they're logged, recoverable after a crash, saved at end of day and queryable through the gateway, like market data.

## 3. The forward, from put-call parity across the whole chain

A Deribit call pays (S−K)⁺/S BTC and a put pays (K−S)⁺/S BTC. So a call minus a put pays (S−K)/S = 1 − K/S BTC, exactly, with **no model** involved. Valuing it under BTC as numeraire, where E[1/S_T] = 1/F:

$$C_{\text{BTC}}(K) - P_{\text{BTC}}(K) = D_{\text{BTC}}\Big(1 - \frac{K}{F}\Big)$$

This is **a straight line in K**: intercept D and slope −D/F. A weighted least-squares fit across every strike with two-sided call and put quotes gives both:

- **F = −intercept/slope.**
- **D = the intercept.** D is the BTC discount factor: the price today of 1 BTC paid at expiry.

How the fit is set up:
- **Weights:** 1/variance of each strike's mid difference, using each quote's half-spread as its uncertainty (never below half a tick).
- **Outliers:** one pass drops points more than 4 standard deviations from the line.
- **Standard errors:** come from the fit, inflated if the line fits worse than the spreads imply.

**What it found** (29 minutes of live data, medians):

| Expiry | Days | D | ± | Parity F − Deribit F (USD) | ± |
|---|---|---|---|---|---|
| 11 Oct | 2.5 | 1.0001 | 0.0096 | −1 | 23 |
| 23 Oct | 14.5 | 1.0002 | 0.0050 | +3 | 22 |
| 27 Nov | 49.5 | 1.0002 | 0.0018 | −6 | 16 |
| 25 Dec | 77.5 | 1.0001 | 0.0007 | −1 | 19 |
| 26 Mar | 168.5 | 1.0000 | 0.0011 | −7 | 22 |
| 25 Jun | 259.5 | 1.0001 | 0.0011 | **−45** | 25 |
| 24 Sep | 350.5 | 1.0009 | 0.0022 | **−48** | 40 |

**Two findings:**
1. **D = 1 within its standard error at every expiry.** Phase 3 *inferred* that Deribit uses a zero BTC interest rate; here it's **measured** from market prices.
2. **For the two longest expiries, the option market implies a forward about $45–48 below Deribit's.** That's roughly 2 standard errors, so it's probably a real gap between where futures trade and what options imply, not noise.

## 4. From quotes to a smile

**Building each expiry's input:**
- At every strike, take **whichever of the call or put has the tighter market in vol**. Put-call parity means they carry the same vol information.
- Drop quotes with a half-spread above 15 vol points, strikes beyond e^±1.5 of the forward, and expiries under 2 days.
- **Weight = 1/(half-spread)²**, with the half-spread floored at 0.25 vol points so no single quote can dominate. This is the maximum-likelihood weight if each mid is a noisy measurement with uncertainty proportional to its half-spread.

**SVI** (Gatheral 2004) describes total variance w = σ²T as a function of log-moneyness k = ln(K/F):

$$w(k) = a + b\left(\rho(k-m) + \sqrt{(k-m)^2+\sigma^2}\right)$$

**Stage 1: Zeliade's quasi-explicit method.** Substitute y = (k − m)/σ, c = bσ and d = ρbσ. Then

$$w = a + d\,y + c\sqrt{y^2+1}$$

For fixed (m, σ), that's **linear** in (a, d, c), with linear constraints 0 ≤ a ≤ max w, |d| ≤ c and |d| ≤ 4σ − c. It's a small convex quadratic programme, and the optimum always lies on one face of the feasible region. So the code solves the problem for **every** set of up to 3 active constraints (42 tiny linear systems) and keeps the best feasible one. That's an exact global optimum, with no iteration.

Nelder–Mead then searches the remaining two parameters (m, σ), starting from the best of a 7×6 grid of starting points.

**Stage 2: Levenberg–Marquardt** on all 5 parameters. It minimises vol errors measured in half-spreads, with the parameters mapped so the constraints hold automatically: b = eᵘ, ρ = tanh v, σ = eʷ.

## 5. Arbitrage: detected and removed, and both versions published

**Butterfly arbitrage.** Gatheral's density function is

$$g(k) = \Big(1-\frac{k w'}{2w}\Big)^2 - \frac{w'^2}{4}\Big(\frac1w+\frac14\Big) + \frac{w''}{2}$$

The implied probability density is g(k)/√(2πw)·exp(−d₂²/2), so **g < 0 anywhere means negative probability**, which is arbitrage. A C++ test checks this formula against the density computed by brute force from option prices (∂²C/∂K²). It also checks that Gatheral & Jacquier's (2014) published example really does have g < 0.

**Calendar arbitrage.** At every k, total variance must not fall as expiry lengthens: w(k, T₂) ≥ w(k, T₁).

**Lee's bound.** b(1 + |ρ|) ≤ 4: the wings can't be steeper than no-arbitrage allows.

**The arbitrage-free fit** adds penalties for every violation on a grid of strikes, raising the penalty from 10² to 10¹², and fits expiries in order, shortest first, so each respects the one before it.

*A test caught a classic weakness here.* A finite penalty always leaves a tiny violation, where the pull of the data balances the penalty: the minimum density was −4×10⁻⁶ instead of ≥ 0. The fix is to aim slightly **inside** each constraint (g ≥ 10⁻⁴, w ≥ w_prev·(1 + 10⁻⁴)), so the leftover lands on the safe side. On the Gatheral–Jacquier example, the minimum density goes from −0.033 to +0.0001, at a cost of 0.25 vol points of fit.

**On live data:**
- The 12-Oct smile's extrapolated wings crossed the 11-Oct smile in **100%** of fits. Removing that cost 18.5 bp of fit.
- The 30-Oct smile crossed in 51% of fits.

That `arbgap` column (the cost of removing arbitrage) is a signal in its own right. Getting it right needed one more fix: the raw-fit calendar check first covered only the quoted strikes, while the arbitrage-free fit enforces 25% beyond them, where extrapolated wings can cross. Both now use the same grid.

## 6. Desk metrics

- **ATM vol:** σ(k = 0).
- **25-delta risk reversal:** vol(25Δ call) − vol(25Δ put). Negative means puts are bid.
- **25-delta butterfly:** average of those two vols, minus ATM vol.

Finding each 25-delta strike means solving N(d₁(k)) = 0.25 on the fitted smile. The first version called SciPy's scalar normal distribution function about 150,000 times in a one-minute replay, and that took **87% of all runtime**. It now evaluates the smile on a 4,001-point grid in one vectorised call and interpolates, making the whole replay **5× faster**.

Live (medians over 29 minutes): every expiry shows the classic crypto shape.
- **Risk reversal:** −0.2 to −2.5 vol points, more negative the shorter the expiry.
- **Butterfly:** about +1 vol point.
- **ATM vol:** rising from 30% at 2 days to 39% at a year.

## 7. The evaluation harness: deciding with evidence

**Method:**
- Replay identical recorded data through the real engine under each configuration, changing one thing at a time.
- Each new smile fitted at time t predicts the **price** of every quote for that expiry over the next 10 seconds, quotes it hasn't seen.
- Score each prediction as z = (model − mid)/half-spread. |z| ≤ 1 means the prediction landed inside the bid-ask band.
- Scoring in price against real quotes gives every configuration the same target.
- **Deribit's own mark price** is scored the same way, as a reference.
- To avoid tuning to one stretch of market, the ~29 minutes were split into **two halves**, and only conclusions that held in both were adopted.

| Configuration | Median \|z\|, half 1 | Median \|z\|, half 2 | Decision |
|---|---|---|---|
| Deribit forward | 0.175 | 0.165 | |
| **Parity forward** | **0.155** | **0.147** | **adopted: ~11% better in both halves** |
| + quote-size weights | 0.156 | 0.146 | mixed, 10× more parameter jitter: rejected |
| OTM only vs best side | 0.174 vs 0.175 | 0.163 vs 0.165 | a tie: kept best side |
| Refit every 2 s | 0.177 | 0.167 | worse |
| Refit every 0.1 s | 0.176 | 0.165 | no better than 0.5 s |
| Arbitrage-free vs raw | +0.6–1% | +3.4–3.6% | a small cost, as expected |
| *Deribit mark (reference)* | *0.154* | *0.135* | |

The full tables are in `results/phase4_harness.md`.

**What the data overruled.** My first gate only used the parity forward when its standard error was below 0.02% of F. That was so strict the engine fell back to Deribit's forward almost every time. The harness showed the parity forward predicts quotes **better**, so the gate is now 0.1%. I had also expected quote-size weights to help; they didn't, consistently.

**Honest comparison with Deribit.** Our best surface **ties Deribit's own marks in the first half and trails them by about 9% in the second**. Deribit sees information we don't (its order flow and full book depth), and its marks are per instrument rather than one smooth smile per expiry. Matching it from public top-of-book quotes alone is a solid result. Closing the gap is a natural next experiment, for example adding trades, or using SSVI across expiries.

## 8. Run it

```bash
pip install -e .                                   # rebuild: the C++ now includes svi.cpp
pytest                                             # 26 Python tests
cmake --build build/cpp-tests -j && ctest --test-dir build/cpp-tests    # 15 C++ tests
bash scripts/stop.sh; bash scripts/start.sh        # the schema changed: the tickerplant must restart
python scripts/system_test.py                      # now also tests the engine, in both modes
```

Then query it from q:

```q
h:hopen 5011
h"select by sym from surface"                                 / latest smile per expiry
h"select sym, atmvol, rr25, bf25, inband, arbgap from select by sym from surface"
h"select by sym from fwd"                                     / parity vs Deribit forward
h"select from iv where sym=`BTC-25DEC26-80000-C"              / every IV for one option
```

Run the harness on your own recording:

```bash
python -m feed --record --minutes 30
python scripts/eval_surface.py --recording ~/kdbdata/raw --minutes 14
python scripts/eval_surface.py --recording ~/kdbdata/raw --start 14 --minutes 15
```

## 9. Questions

1. Why is C − P model-free for inverse options, while C and P individually need a model? What would break the line in K?
2. Zeliade's method enumerates 42 constraint combinations. Why is the best *feasible* one guaranteed to be the global optimum? (Hint: convexity.)
3. The arbitrage-free fit costs about 1–4% in prediction accuracy. When would a desk accept that cost, and when would it want the raw fit?
4. The parity forward sits about $45 below Deribit's for long expiries. Design a test that tells apart "futures trade rich" from "our regression is biased for long expiries".
