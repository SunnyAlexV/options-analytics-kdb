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
| **One config file + launcher** | `config.yaml` (ports, paths, instruments); `start.sh` / `stop.sh` / `status.sh` | The whole system starts with one command. Good command-line practice. | 2 |
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

0. **Foundations** — machine setup ✅, first q session, first commit
1. **Data** — feed handler
2. **kdb+ core** — tickerplant, real-time DB, historical DB
3. **C++ pricing** — Black-76, IV solver, full Greek set, tests
4. **Vol surface** — SVI calibration and arbitrage checks
5. **Risk** — portfolio Greeks, scenarios, P&L explain, VaR
6. **Dashboard**
7. **Polish** — benchmarks, CI, README, design document, demo video
8. **Multi-asset** — see below

## Phase 8: multi-asset (after the crypto version is complete)

- **Design for it from day one:**
  - an `asset` column and an instrument reference table in every q schema
  - a pricer interface where the pricing model is a choice, not hard-coded
  - feed handlers that all output the same normalised row format
- **Candidates:**
  - ETH options (same feed)
  - US equity and ETF options via yfinance snapshots: American exercise means a binomial or finite-difference pricer, plus dividends and interest rates
  - index options (European, Black-Scholes with a dividend yield)
- **Constraint:** free options data outside crypto is limited and delayed. Choose sources when this phase starts.
