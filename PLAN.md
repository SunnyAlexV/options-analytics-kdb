# options-analytics-kdb — Project plan

Agreed 8 Oct 2026. This file is the single source of truth for scope and design decisions; update it when a decision changes.

## Goal

A scaled-down version of an options desk's analytics system: live market data → kdb+/q tick stack → C++ pricing and calibration → risk and P&L → live dashboard. Built for BTC options on Deribit first, then extended to other asset classes.

## Architecture

| # | Component | Language | Role |
|---|---|---|---|
| 1 | Feed handler | Python | Deribit websocket/REST → normalised rows → tickerplant |
| 2 | Tickerplant | q (port 5010) | Logs every update to disk, publishes to subscribers |
| 3 | Real-time DB | q (port 5011) | Today's data in memory |
| 4 | Historical DB | q (port 5012) | Past days on disk, partitioned by date |
| 5 | Calc engine | Python + C++ (pybind11) | IVs, Greeks, surface fit, risk; publishes results back to the tickerplant |
| 6 | Gateway | q (port 5013) | Single query entry point; routes to the real-time and historical DBs |
| 7 | Dashboard | Streamlit + PyKX | Live and historical views; talks only to the gateway |

### Production-grade additions (agreed 8 Oct 2026)

These are the pieces that separate a demo from a system a desk would actually run.

| Addition | What it does | Why it matters | Phase |
|---|---|---|---|
| **Gateway** (q, port 5013) | Single entry point for every query; routes "today" to the real-time DB and "past days" to the historical DB, and joins the two | Clients never need to know where data lives. This is the standard kdb+ pattern. | 2 |
| **Reference data table** | One row per instrument: asset, expiry (08:00 UTC), strike, put/call, contract size, tick size; refreshed daily | Market data rows stay small; multi-asset works by adding rows, not columns | 1–2 |
| **Crash recovery** | The real-time DB replays the tickerplant's log file on restart | No data lost if a process dies. Tested by deliberately killing the real-time DB mid-session. | 2 |
| **Replay mode** | Feed a recorded day back through the same pipeline at chosen speed (1×, 10×, max) | Deterministic tests, demos when the market is quiet, and backtests of the P&L explain | 2 |
| **Resilient feed handler** | Reconnects with backoff, uses Deribit heartbeats, detects gaps, flags stale or crossed quotes | Real feeds drop. Handling it is what interviewers probe. | 1 |
| **Data-quality table** | Counts per minute of crossed quotes, null bids, stale instruments and outliers | Visible proof the data was checked before it was modelled | 1–2 |
| **Latency stamps** | Each row carries a timestamp from every stage it passes through (feed received, tickerplant, calc) | End-to-end latency measured per stage (kept private under the KDB-X licence) | 2–5 |
| **Health monitor** | Each process publishes a heartbeat; the dashboard shows which processes are up, plus their memory and queue sizes | Operational visibility | 6 |
| **Alerts** | Arbitrage violations, IV jumps, feed gaps → alert table → dashboard banner | Turns analytics into something a trader would act on | 5–6 |
| **One config file + launcher** | `config.env` (ports, paths); `start.sh` / `stop.sh` / `status.sh` | The whole system starts with one command. Good command-line practice. | 2 |
| **Tests in all three languages** | pytest (Python), GoogleTest (C++), a small q test harness | Correctness provable in every layer | 2–3 |

## Decisions

| Topic | Decision | Later upgrade |
|---|---|---|
| C++ integration | pybind11 into the Python calc engine | Same library loaded into q as a shared object (stretch) |
| Data scope | Store every BTC option; fit the surface only to liquid points (2 days–1 year expiry, tight bid-ask spreads) | — |
| Pricing model | Black-76 on each expiry's forward; inverse (BTC-settled) conventions handled explicitly | Pluggable models for other assets |
| Surface | Raw SVI per expiry, plus butterfly and calendar arbitrage checks | SSVI (arbitrage-free by construction) |
| P&L explain | A fixed, realistic hypothetical book (e.g. short 25-delta strangle + long calendar spread) | — |
| Quote source | `book.{inst}.none.1.100ms` (top of book, sent only on change, ~250–350 rows/s); Deribit marks/forwards via a REST snapshot every 10 s. **Not** `ticker.{inst}.100ms`: measured at ~1,000 msgs/s because it re-sends every option when the index moves. | Live forwards from futures, plus our own put-call-parity forwards |
| Tick architecture | **KX's standard kdb+tick** (`tick.q`, `u.q`, `r.q`) used unmodified, pinned to commit `85c08ff` and downloaded by `scripts/get_kdb_tick.sh` (KX's repo has no licence file, so it isn't redistributed). Our code: generated schema, small RDB/HDB wrappers, gateway, scripts | — |
| Table conventions | kdb+tick's: first columns `time` (stamped by the tickerplant) and `sym`; exchange and feed times kept as `exch`, `recv`. High-cardinality ids (trade ids) are longs, never symbols | — |
| Day boundary | Midnight UTC; every q process runs with `TZ=UTC` | — |
| IV solver | Our own safeguarded Newton (bisection fallback, relative tolerance), statuses instead of guesses for arbitrageable prices. Jäckel's "Let's Be Rational" to be added later as a benchmark | Jäckel LBR |
| Build | scikit-build-core + CMake + pybind11 (`pip install -e .`); C++ tests with GoogleTest | — |
| IVs computed | Bid, ask and mid IV for every option (the bid-ask IV width becomes the SVI fit weight in Phase 4) | — |
| BTC premium convention | V_btc = Black76(F)/F, i.e. zero BTC discount rate, as Deribit does (derived in lessons/04) | — |
| Surface engine | Streaming kdb+ real-time engine (tickerplant subscriber); poll mode as a lossless fallback; IV per quote, smile refit per changed expiry at most every 0.5 s (harness: 0.1 s no better, 2 s worse) | — |
| Forward | Put-call parity regression across all strikes (gives F and the BTC discount factor D); Deribit's forward as fallback when the regression's std error exceeds 0.1% of F (harness: ~11% better out of sample) | Futures as a third source |
| Smile fit | SVI, Zeliade quasi-explicit (exact inner QP) + Levenberg-Marquardt, weights 1/half-spread², best side per strike; quote-size weights rejected by the harness | SSVI across expiries |
| Arbitrage | Raw and arbitrage-free fits both published; butterfly (g ≥ 0), calendar and Lee constraints via penalty continuation with safety margins | SSVI (arbitrage-free by construction) |
| Repo licence | MIT (the KDB-X licence forbids linking with copyleft code) | — |
| Benchmarks | Publish C++ vs Python timings only; no published timings that involve q unless KX approves in writing | — |
| CI | GitHub Actions runs the C++ and Python tests; q tests run locally | — |

## Metrics

### Per option
- Price (BTC and USD), implied vol, and a check against Deribit's own `mark_iv`
- First-order Greeks: delta, gamma, vega, theta, rho
- Second-order and cross Greeks: vanna, volga (vomma), charm, veta, speed, zomma, colour
- Inverse-option conventions: premium-adjusted delta; Greeks in both BTC and USD terms
- Delta under three smile assumptions:
  - sticky-strike (the plain Black-76 number)
  - sticky-moneyness
  - smile-consistent, i.e. delta including the surface's slope

### Per expiry (the surface)
- ATM vol term structure
- 25-delta risk reversal and butterfly
- Skew slope and curvature
- SVI parameters and fit error (RMSE)
- Arbitrage flags
- Implied forward and basis versus the index
- Put-call parity deviations

### Market
- Realised vol from index ticks: close-to-close, Parkinson, Garman-Klass, Yang-Zhang
- Implied minus realised vol (variance risk premium)
- Bid-ask spreads, volume and open interest by strike and expiry

### Portfolio
- Aggregated Greeks
- Vega bucketed by expiry
- Spot × vol scenario grid
- P&L explain: Greek-predicted versus actual P&L, plus the unexplained residual
- Historical-simulation VaR

## Phases

0. **Foundations** — machine setup ✅, first q session ✅, first commit ✅
1. **Data** — feed handler ✅
2. **kdb+ core** — tickerplant, real-time DB, historical DB ✅
3. **C++ pricing** — Black-76, IV solver, full Greek set, tests ✅ (calc engine wiring into the tickerplant comes with Phase 4)
4. **Vol surface** — live engine, parity forwards, SVI raw + arbitrage-free, evaluation harness ✅
5. **Risk** — portfolio Greeks, vega buckets, scenario grid, P&L explain, VaR/ES with backtest ✅ (smile-rule verdict, 4 h of live BTC data on 9 Oct 2026: sticky-moneyness, R = 0, won both folds; [results/phase5_smile_rules.md](results/phase5_smile_rules.md))
6. **Dashboard** — Dash + Plotly, Market / Risk / Portfolio / System pages over the gateway; public demo on Streamlit Community Cloud (a replay of the 4-hour session) ✅
7. **Polish** — CI (C++, Python and demo jobs on every push), benchmarks (`results/benchmarks.md`), design document (`docs/DESIGN.md`) ✅; demo video still to record
8. **Multi-asset** — see below. 8a (every Deribit coin: ETH + 7 USDC coins, inverse and linear conventions) ✅; 8b (a book per coin and a joint VaR/ES with contributions) ✅; India on hold (no broker API account)

## Phase 8: multi-asset (after the crypto version is complete)

- **Design for it from day one:**
  - an `asset` column and an instrument reference table in every q schema
  - a pricer interface where the pricing model is a choice, not hard-coded
  - feed handlers that all output the same normalised row format
- **Scope (agreed 9 Oct 2026): crypto, US, India, UK.** Asia excluded.

| Market | Instruments | Data route (non-professional prices, Oct 2026) |
|---|---|---|
| Crypto | BTC, ETH and all seven Deribit USDC-settled coins (linear) — **done (Phase 8a)**; per-coin books and joint VaR — **done (8b)** | Deribit websocket, free |
| US | SPX/SPY index options, single stocks; CME futures options (FX incl. GBP, rates, crude) | Interactive Brokers API: OPRA $1.50/mo, CME $1.25/mo, each waived above $20/mo commissions. ThetaData Standard ($80/mo) later for full SPX chains |
| India | NSE Nifty and Bank Nifty options (European, cash-settled, weekly expiries) | Upstox market-data websocket (free) or Zerodha Kite Connect (Rs 500/mo) |
| UK | FTSE 100 index options (ICE Futures Europe); GBP via CME FX options | Live FTSE is expensive (ICE Financials ~$122/mo via IB): start with ICE end-of-day settlements (to confirm) + CME GBP options live |

- **New per market:** a conventions module (exercise style, settlement, calendar and sessions, day count,
  quote currency, lot size), a rate curve (SOFR / Indian T-bills / SONIA), dividends implied from
  put-call parity (the forward regression already estimates F and D), an American-exercise pricer in C++
  (US single stocks, SPY), and a trading-day vol clock for exchange-traded markets.
- **Cross-asset:** books valued in USD with live FX; joint VaR on the same historical days for every
  asset (correlations kept); an asset selector on the dashboard.
- **Licensing:** paid exchange data cannot be shown publicly in real time. The public demo replays
  crypto live-recorded data, and for licensed markets only delayed recordings or derived numbers,
  per each source's terms.
- **Order:** ETH -> India (Nifty) -> US (SPX/SPY, then single stocks, then CME futures options) -> UK.
