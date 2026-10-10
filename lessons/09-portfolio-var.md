# Lesson 9: one book per coin, and a VaR for all of them together

Phase 5 gave BTC a sample book, Greeks, scenarios, P&L explain and a VaR. Phase 8a gave every
Deribit coin a live surface. This lesson joins them: **every coin gets a book, and the risk process
computes one VaR across all of them**, with each coin's share of the risk.

Code: `risk/portfolio.py` (the maths), `risk/history.py` (the data), `risk/__main__.py` (the live
process), `dashboard` Portfolio tab. Results: `results/phase8b_portfolio.md`
(`python scripts/portfolio_report.py --bundle streamlit_app/bundle_all`).

## 1. The books

Each coin holds the same seven legs as the BTC book (ATM straddle, 3-month risk reversal, calendar,
tail puts), chosen on that coin's own fitted smile and snapped to its listed strikes, then
delta-hedged under the smile rule.

**Same USD size.** A BTC leg of 25 contracts is about $2 million of notional. SOL at $110 would need
25 × 80,000 / 110 ≈ 18,000 SOL for the same size, so every quantity is multiplied by
`BTC spot / coin spot` (`risk.portfolio.scale_for`). Without this, the "portfolio" would be BTC plus
rounding errors, and the contributions would only reflect position sizes.

**The hedge.** BTC and ETH hedge with a dated future. The USDC coins have no dated futures on
Deribit, so the hedge is a **synthetic forward**: valued at our own parity forward, `F − entry` per
unit, exactly like a future. It is labelled as such.

## 2. The data: what Deribit has, and what it doesn't

| Market | Daily price | Vol index |
|---|---|---|
| BTC, ETH | since 2016 / 2019 | own DVOL |
| BTC_USDC, ETH_USDC | since Feb 2022 | the coin's own DVOL |
| SOL, XRP, AVAX (USDC) | since Feb 2022 | **none: BTC's DVOL stands in** |
| TRX (USDC) | since Apr 2022 | none: BTC's DVOL |
| HYPE (USDC) | **since Jun 2026 (131 days)** | none: BTC's DVOL |

Two consequences, both handled openly rather than papered over:

- **The vol proxy.** On each historical day the alts' vols move by BTC's DVOL change. Crypto vols move
  together, so this is a reasonable first approximation, but it is an assumption. The report therefore
  also shows a **stress** line: the proxied coins' vol moves × 1.5. If the answer changed little, the
  proxy wouldn't matter; it changes by about 25% (below), so it does.
- **HYPE's short history.** The main VaR uses the last 365 days for the coins that have them. HYPE joins
  only an **all-coins** line, on the 130 days every coin shares. Its missing days are not invented.

## 3. The maths

For a historical day d, every coin moves by **its own actual move that day**:

    forwards of coin a:  F → F · exp(r_a,d),      r_a,d = ln(S_a,d / S_a,d−1)
    vols of coin a:      σ → σ · exp(v_a,d),      v_a,d = ln(VOL_a,d / VOL_a,d−1)

and each book is revalued in full (C++), giving a P&L matrix P[d, a]. The portfolio's P&L on day d is
the row sum. **No correlation is estimated anywhere**: it is in the data. If SOL fell 12% on the day
BTC fell 8%, both moves are in the same scenario, with the fat-tailed joint behaviour crypto actually
showed.

FHS (filtered historical simulation) rescales each coin's moves by its own EWMA volatility ratio
(today's / that day's), exactly as each coin's standalone VaR does. So the standalone numbers inside
the portfolio report equal the standalone VaR to the last digit (a test checks it).

**VaR and ES** are read off the 365 portfolio P&Ls: VaR 99% is the 4th-worst loss
(⌈0.01 × 365⌉ = 4), ES 97.5% the average of the 10 worst (⌈0.025 × 365⌉ = 10).

**Contributions (Euler allocation).** Take the portfolio's 10 worst days. Coin a's contribution is its
own average loss on those same days:

    C_a = −(1/10) Σ_{d ∈ worst 10} P[d, a],      Σ_a C_a = ES_portfolio   (exactly)

A coin can contribute less than its ES alone (its bad days aren't the portfolio's bad days) or, in
principle, more.

**Diversification** = Σ ES_alone − ES_portfolio. For **ES this can never be negative**: on a fixed set
of scenarios, the average of the k worst sums is at most the sum of each part's k worst averages (ES is
subadditive). For **VaR it can be negative**: VaR is not subadditive, which is one reason the Basel
FRTB moved to ES. The report shows both.

**Backtest.** For each of the last 365 days, the joint VaR 99% is forecast from the 365 days before it,
and compared with what today's portfolio would have made on that day's real moves of every coin.
Kupiec's test asks whether the exception count is consistent with 1%.

## 4. What the numbers say (recorded session, 9 Oct 2026)

From `results/phase8b_portfolio.md`, FHS, 1 day, eight coins with a full year:

- **Portfolio ES 97.5% $211k** against **$228k** summed alone: diversification of **$17k, 8%**. Small,
  for two reasons visible in the correlation matrix: crypto moves together (median correlation 0.82),
  and four of the eight books are the same two coins (BTC/BTC_USDC and ETH/ETH_USDC correlate at 1.00).
- **AVAX contributes most (20%)** although every book has the same notional: it is the most volatile
  coin. TRX contributes least (7%): its correlation with the rest is only about 0.4.
- **The vol proxy matters**: scaling the proxied coins' vol moves × 1.5 lifts ES from $211k to $265k
  (+25%). A real vol history for the alts (our own HDB of fitted surfaces, once it is long enough)
  would remove the assumption.
- **The backtest passes**: 2 exceptions in 365 days for FHS (3.65 expected), Kupiec p = 0.34.

## 5. How it runs

One risk process holds every coin's book: the joint VaR needs every position at the same moment.
Greeks, scenarios and P&L explain are published per coin (the `asset` column). Every 15 minutes it
publishes each coin's own VaR (`vares`) and the portfolio report (`port`: one number per row, e.g.
`sym=joint, method=fhs, metric=contrib_es975, asset=SOL_USDC`). A long table keeps the schema fixed
however many coins are added. Dashboard views now take the latest snapshot **per coin**
(`where time=(max;time) fby asset`), since each coin publishes separately.

## 6. Tests

`tests/test_portfolio.py`:
- contributions add up to the joint ES exactly, for HS and FHS;
- each coin's standalone numbers equal `risk/var.py`'s to 12 digits;
- two identical books on identical histories give exactly zero ES diversification and correlation 1;
- a coin with a short history joins only the all-coins line, on the shared days;
- the stress scales only the proxied coins;
- the live process, driven directly, books two coins at equal USD size and publishes both coins' rows
  and the portfolio.

The system test adds a portfolio section: rows published, contributions summing to the ES, and a
non-negative ES diversification, on live data.
