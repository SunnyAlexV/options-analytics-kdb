# Phase 5 walkthrough: portfolio risk, P&L explain and VaR

**What was built:** a second real-time process, `risk`. It takes the surface engine's fitted smiles and answers the questions a trading desk asks every few seconds:

- What is the book worth?
- What happens if BTC moves, or vols move?
- Where did today's P&L come from?
- How much could we lose tomorrow?

```
cpp/src/risk.cpp          full revaluation of a book under scenarios and smile rules (C++)
cpp/tests/test_risk.cpp   7 GoogleTests
risk/market.py            market state: forward + arbitrage-free SVI smile per expiry
risk/book.py              positions; the sample book, built on the live smile and delta-hedged
risk/core.py              Greeks in desk units, buckets, scenario grid, P&L explain
risk/dynamics.py          measuring how the smile moves when BTC moves; scoring smile rules
risk/var.py               VaR / ES: historical and filtered historical simulation, Kupiec backtest
risk/rows.py              rows exactly as published (shared with the schema test)
risk/__main__.py          the live process
engine/ipc.py             the tickerplant subscriber, now shared by engine and risk
tests/test_risk.py        10 pytest tests;  tests/test_rte.py: the process over the wire protocol
scripts/eval_smile_rules.py  run the smile-rule evaluation
results/phase5_smile_rules.md  the numbers behind the smile-rule decision
```

## 1. Architecture: a chain of real-time subscribers

```
feed ─► tickerplant ─► surface engine ─► tickerplant ─► risk ─► tickerplant ─► RDB / HDB / gateway
         (quote, ref,      (iv, fwd,                      (pos, risk, scen,
          snap, spot)       surface)                       pnl, vares)
```

This is the standard kdb+ pattern of chained real-time engines (RTEs). Each process subscribes to what it needs, and publishes its results **back through the tickerplant**. Every risk number is therefore:
- logged;
- replayable after a crash;
- saved to the HDB at end of day.

You can ask "what was our vega at 14:05 last Tuesday?" with one query:

```q
select from risk where date=2026.10.06, kind=`total, time within 14:05 14:06
```

The subscriber code (the `.u.sub` handshake and the PyKX details) moved into `engine/ipc.py`, so both processes use the same tested code.

**A q lesson from this phase:** column and table names must not be q keywords. My first schema had a table called `var`, and columns `value`, `first` and `last`.
- `var` and `last` are reserved words.
- `value` and `first` are built-in functions, so `select value from risk` would *call* `value` instead of reading the column.

They're now `vares`, `mtm`, `wstart` and `wend`, and a check over all 15 tables confirms there are no other clashes.

## 2. Valuation: what a Deribit option is worth in USD

A Deribit option on 1 BTC is worth, in BTC:

  V_btc = Black76(F, K, T, σ) / F        (lesson 04: Deribit's measured D ≈ 1)

The desk reports in USD, so the position's USD value is V_btc × F = **Black76(F, K, T, σ)**. That is exactly the price our C++ library computes.

The hedge instrument is modelled as a linear future on an expiry's forward, worth q·(F − entry). Deribit's own futures are inverse too. This is a stated simplification: it changes how a hedge is sized, but not how the option risk is measured.

**Time.** Each fitted smile gives total variance w(k) = σ(k)²·T_fit. Valuing it a moment later, with T_now years left, we keep each point's *vol* (the standard "sticky in time" choice):

  w_now(k) = w_fit(k) · T_now / T_fit

Raw SVI is linear in (a, b), so that simply scales a and b (`risk/market.py`).

## 3. Smile dynamics: the one choice risk numbers depend on

When BTC moves, how does the smile move? Two textbook answers:
- **Sticky-strike:** each strike keeps its vol.
- **Sticky-moneyness:** the smile slides along with the price.

They give **different deltas**, so a desk must choose. One parameter covers both, and everything in between. That parameter is Bergomi's **skew-stickiness ratio R**.

After a log forward move x = ln(F′/F), the new smile in log-moneyness k′ = ln(K/F′) is:

  σ_new(k′) = σ_old(k′ + R·x)

- **R = 1:** k′ + x = ln(K/F), the old k. Each strike keeps its vol (sticky-strike).
- **R = 0:** σ_new(k′) = σ_old(k′). The smile moves with the price (sticky-moneyness).

**Why it's called that.** Take the ATM point, K = F′, so k′ = 0. Its vol moves by:

  dσ_ATM = σ_old(R·x) − σ_old(0) ≈ R · σ′(0) · x  =  R · skew · x

So R is the ATM vol move divided by what the skew alone would imply. The C++ test `AtmVolMovesBySkewTimesRTimesMove` checks this by central differences.

### Delta under rule R

At a fixed strike, σ(K; F) = σ_old(ln(K/F₀) − (1 − R)·ln(F/F₀)), so

  ∂σ/∂F = −(1 − R) · σ′(k) / F

and by the chain rule:

  **Δ_R = Δ_Black + vega · (R − 1) · σ′(k) / F**,   with σ′(k) = w′(k) / (2σT) from SVI

For a put-skewed smile (σ′ < 0) and R < 1, the correction is positive. With BTC's skew, the difference is large enough to matter for hedging. The C++ test `RuleDeltaMatchesFiniteDifference` checks this formula against full revaluation for R = 0, 0.6 and 1.

### Gamma under rule R

Gamma under rule R is computed by central differences of full revaluation, so it includes the smile's own curvature exactly. The bumps are multiplicative, F·e^{±h}, so the formula needs one correction.

Let G(x) = V(F·eˣ). Then G″(0) = F²·V″ + F·V′, so

  V″ = [V(+h) − 2V + V(−h)] / (F·h)² − V′/F

Under R = 1 this must reproduce Black-76's analytic gamma, and the test `test_sticky_strike_gamma_is_black76_gamma` checks it does, to 1e-4.

## 4. Greeks in desk units (`risk/core.py`)

| column | meaning | from |
|---|---|---|
| `mtm` | value, USD | q·Black76 |
| `delta` | BTC per forward, smile fixed per strike | q·Δ |
| `deltaR` | BTC, under the smile rule | q·Δ_R |
| `deltapa` | premium-adjusted delta: what a BTC-margined account hedges | q·(Δ − V/F) |
| `deltaspot` | spot-equivalent BTC (all forwards move with spot) | Σ q·Δ_R·F_e / S |
| `cashdelta` | USD P&L of +1% in every forward | q·Δ_R·F·0.01 |
| `gamma` | change in delta (BTC) per +1% | q·Γ_R·F·0.01 |
| `cashgamma` | USD from gamma alone for a 1% move | q·Γ_R·(0.01F)²/2 |
| `vega` | USD per vol point | q·vega/100 |
| `theta` | USD per calendar day (crypto trades daily) | q·θ/365 |
| `vanna` | change in delta (BTC) per vol point | q·vanna/100 |
| `volga` | change in vega (USD/pt) per vol point | q·volga/10⁴ |

### Vega buckets

Vega is reported three ways, because one number hides where the risk sits:
1. **By expiry:** the term structure.
2. **By delta:** each option's call delta N(d₁) picks the nearest of 10C, 25C, ATM, 25P and 10P. Put deltas are converted: a 25-delta put has call delta 0.75.
3. **By smile shape:** the exposure to +1 vol point of ATM vol, of the 25-delta risk reversal and of the 25-delta butterfly, the three numbers Phase 4 publishes per expiry. Each is a smile bump written as a function of call delta x:

     φ_RR(x) = 2(0.5 − x)        → +½ at the 25C (x = 0.25), −½ at the 25P (x = 0.75), 0 at ATM
     φ_BF(x) = ((x − 0.5)/0.25)²  → 1 at both 25-deltas, 0 at ATM

   so RR = σ(25C) − σ(25P) rises by exactly 1 point and BF by exactly 1 point. Each exposure is Σ vega·φ(x).

## 5. Scenario grid

The grid crosses 11 BTC moves (−30% to +30%) with 7 vol shifts (−20 to +20 points). Every one of the 77 cells is a **full revaluation** off the fitted smiles, with the smile moving per rule R, all in one C++ call.

There's no Taylor approximation, which matters: a −30% BTC day is outside where Greeks are accurate. The C++ test `ParityBookHasNoRiskInAnyScenario` builds a call − put − future book (exactly riskless by put-call parity) and checks it shows zero P&L in every cell, for both rules.

## 6. P&L explain

From market state 0 to state 1, each option's actual change in value is split, using time-0 Greeks:

  delta Δ·dF  +  gamma ½Γ·dF²  +  vega ν·dσ  +  theta θ·dt  +  vanna ν_F·dF·dσ  +  volga ½ν_σ·dσ²  +  **unexplained**

Here dσ = σ₁(K) − σ₀(K) is the change in the option's own vol **at its strike**. The unexplained part is the honest quality measure. The system test prints it in section [2c] (sum of |actual| vs sum of |unexplained|) and fails if more than 5% is unexplained.

**A point worth being precise about.** Because dσ is measured at a fixed strike, this decomposition doesn't depend on the smile rule. The total explained, and so the unexplained, comes out the same whatever R is. So "unexplained P&L" can't be what picks the smile rule, and isn't used for that (section 7).

The rule enters a second split of the vega term:
- **`smile`:** ν × (the vol change rule R predicts from the price move alone);
- **`surf`:** ν × (everything else), the market actually re-marking vols.

The test `test_pnl_split_attributes_a_rule_move_to_smile` builds a market where the smile moves *exactly* by rule R = 0.5, and checks that it all lands in `smile`.

## 7. Choosing the smile rule from data

What matters to a trader is **which rule hedges better**. That is Hull and White's criterion for comparing deltas. So, on recorded data (`risk/dynamics.py`):

1. **Snapshots.** Take the market every h seconds (h = 1, 5, 15 minutes). On each expiry, pick standard points: call deltas 10, 20, …, 90. These are fixed points on the smile, so densely listed strike regions don't dominate.
2. **Estimate R.** Rule R predicts the fixed-strike vol change dσ(K) ≈ −(1 − R)·x·σ′(k). Regressing the observed change y on z = −x·σ′(k) through the origin gives slope β = 1 − R, so **R̂ = 1 − β**. All points in one interval share the same x, so they aren't independent; the standard error is **clustered by interval**.
3. **Score out of sample.** Estimate R on half the intervals, then score on the other half, and swap. The candidates are R = 0, R = 1 and R̂, scored by:
   - vol-prediction error;
   - **delta-hedged P&L error**: each option's change in value minus Δ_R·dF.
4. **Decision rule, written down before seeing the data:** pooled tenors, 5-minute horizon, hedged-P&L RMSE. A rule must win on **both** folds. Otherwise the better of sticky-strike and sticky-moneyness on average is kept, since a simple named rule shouldn't lose to an estimate on noise. The rule also refuses to decide with fewer than 20 intervals per fold.

The test `test_estimate_R_recovers_the_true_rule` simulates 4 hours of smiles moving under R = 0.4, with independent vol noise, and checks the estimator recovers it.

**Running it.** The decision needs at least 20 five-minute intervals per fold, so at least about 3.5 hours of data. The best data is what the live system itself published: start everything, leave it running for 4 hours or more (keep the computer awake), then

```bash
python scripts/eval_smile_rules.py --gw 5013 --start 2026.10.09 --report
```

It reads the engine's smiles back through the gateway (RDB for today, HDB for earlier days), prints every horizon, tenor and fold, applies the decision rule and writes `results/phase5_smile_rules.md`. Set `RISK_R` in `config.env` to the decided value.

## 8. VaR and Expected Shortfall (`risk/var.py`)

**History.** Deribit's `btc_usd` index (the index the options settle on) and **DVOL**, Deribit's 30-day BTC implied-vol index, daily since 2021. It is cached to a CSV so every result can be reproduced.

**Scenarios.** Each historical day d becomes one scenario for *today's* book:

  every forward × e^{r_d},   r_d = ln(S_d / S_{d−1})
  every vol × e^{v_d},        v_d = ln(DVOL_d / DVOL_{d−1})
  one day passes,             dt = 1/365

The smile moves per rule R. Price and vol moves come from the same day, so their correlation is kept. That correlation is strong in crypto, and treating the two separately would get the risk of a long-vega book badly wrong.

**HS vs FHS.**
- **Plain historical simulation (HS)** weights the last 365 days equally. A calm year then understates risk in a volatile week.
- **Filtered HS (FHS)** rescales each day's move by (today's volatility / that day's volatility), using EWMA with λ = 0.94:

    σ²_t = λ·σ²_{t−1} + (1 − λ)·r²_{t−1}

  This is done separately for the price and the vol series. The test `test_fhs_rescales_to_current_volatility` builds a calm-then-volatile history and checks FHS scales up.

**Measures.**
- **VaR 99%:** the loss exceeded on 1% of days.
- **ES 97.5%:** the average loss on the worst 2.5% of days. This is what Basel's FRTB uses: it looks *into* the tail, where VaR only marks its edge.
- **ES 99%.**

**Backtest.** For each of the last 365 days, VaR is forecast from the 365 days before it, then compared with the P&L today's book would have made on that day's actual moves. The exceptions are tested with **Kupiec's proportion-of-failures test**: with x exceptions in n days at rate p = 1%,

  LR = −2 [ (n − x)·ln(1 − p) + x·ln p − (n − x)·ln(1 − x/n) − x·ln(x/n) ] ~ χ²(1)

A small p-value means the exception rate is wrong. The live process logs the exceptions and the p-value each time it computes VaR, and stores them in the `vares` table (`btexc`, `btdays`, `kupiec`).

**Limitations, stated rather than hidden:**
- One vol factor (a 30-day index) scales the whole surface proportionally. Term-structure and skew moves are not in this history; the scenario grid and the vega buckets cover them.
- The backtest uses today's book, so it tests the *method* on this book's shape, not a real trading history.
- Once our HDB holds a year of fitted surfaces, the same code can run on those, and the `src` column will say so.

## 9. The sample book

The sample book is a small BTC vol desk, built on the **live** smile, so strikes are always sensible:
- a 1-month ATM straddle (long gamma and vega);
- a 3-month 25-delta risk reversal (skew);
- a 1-week vs 2-month ATM call calendar;
- 1-month 10-delta puts (a tail hedge);
- a future that zeroes the spot-equivalent delta under the chosen rule.

Strikes are found by solving N(d₁(k)) = target on the fitted smile, then snapped to listed strikes from the `ref` table. Load your own positions with `RISK_BOOK=path.csv` (columns in `risk/book.py`).

## 10. What the tests prove

| test | proves |
|---|---|
| C++ `StickyStrike…`, `StickyMoneyness…` | each rule moves the smile as defined |
| C++ `AtmVolMovesBySkewTimesRTimesMove` | R is the skew-stickiness ratio |
| C++ `ParityBookHasNoRiskInAnyScenario` | full revaluation is internally consistent (put-call parity) |
| C++ `RuleDeltaMatchesFiniteDifference` | the Δ_R formula is right |
| `test_parity_book_has_no_risk` | the Python layer aggregates correctly |
| `test_sticky_strike_gamma_is_black76_gamma` | the log-grid gamma formula is right |
| `test_rule_delta_matches_scenario_revaluation` | Greeks and the scenario grid agree |
| `test_pnl_explain_small_move_is_explained` | Taylor terms leave < 1% unexplained on a small move |
| `test_pnl_split_attributes_a_rule_move_to_smile` | the smile / surface split works |
| `test_estimate_R_recovers_the_true_rule` | the R estimator is unbiased on known dynamics |
| `test_var_es_and_kupiec`, `test_fhs_…`, `test_var_parity_…` | VaR maths, FHS scaling, consistency |
| `test_risk_process_publishes_…` (test_rte) | the live process over kdb+'s wire protocol |
| `test_every_table_converts_…` (test_feed) | all 15 tables' rows convert to the right q types |
