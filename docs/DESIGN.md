# Design document — options-analytics-kdb

A scaled-down options-desk analytics system: live Deribit market data → kdb+/q tick stack → C++ pricing
and calibration → real-time risk → dashboard. This document explains how it is put together, why each
major choice was made, what evidence backs it, and what it does not do. Detail lives in the lessons
(`lessons/01`–`08`) and the result files (`results/`); this page links to them rather than repeating them.

Live demo: <https://options-analytics-kdb.streamlit.app/> (a replay of recorded sessions).

---

## 1. What the system does

For every option Deribit lists (BTC and ETH settled in the coin, and seven coins settled in USDC), in real time:

- captures every top-of-book change, trade, index tick and Deribit's own marks into kdb+, keeping today in memory and past days on disk;
- prices every quote: bid, mid and ask implied vols on each expiry's forward;
- estimates each expiry's **forward and discount factor from put-call parity** across the whole chain;
- fits **two SVI smiles per expiry** (raw, and arbitrage-free), with desk metrics: ATM vol, 25-delta risk reversal and butterfly, term structure, and the implied distribution;
- runs **portfolio risk** on a sample book per coin: Greeks in desk units, vega buckets, a spot × vol scenario grid, P&L explain, each coin's VaR/ES with a backtest, and a **joint VaR/ES across all coins** with each coin's contribution;
- shows all of it on a dark trading-desk dashboard, live from kdb+ or replayed from a recording.

## 2. Architecture

```
Deribit ──► Feed handlers (Python, one per settlement group: BTC · ETH · USDC)
               │ book · trades · index · REST snapshots · reference data, normalised rows
               ▼
            Tickerplant (q, KX kdb+tick) ── logs every update to disk ── publishes to subscribers
               │                    │                         │
               ▼                    ▼                         ▼
     Real-time DB (q)     Surface engines (Python + C++)    Risk (Python + C++)
     today, in memory     IV · parity forward · SVI          Greeks · scenarios · P&L · VaR
               │          publish iv/fwd/surface back ──►    publish pos/risk/scen/pnl/vares/port ──► (tickerplant)
               │ end of day
               ▼
     Historical DB (q), one partition per date
               │
     Gateway (q) ── one entry point: today → RDB, past days → HDB ──► Dashboard (Dash) / bundles (demo)
```

Everything is a **tickerplant subscriber that publishes its results back through the tickerplant**. So the
derived tables (implied vols, forwards, smiles, risk) are logged, replayable, stored at end of day and
queryable through the same gateway as market data. Restarting the RDB, an engine or the risk process loses
nothing: the RDB rebuilds itself from the tickerplant log, and the engines and risk bootstrap from the RDB. (A
feed that reconnects loses the seconds it was down; each gap is recorded in the `gap` table.)

| Process | Language | Port | Role |
|---|---|---|---|
| `feed_BTC`, `feed_ETH`, `feed_USDC` | Python | – | Deribit websocket + REST → normalised rows |
| `tp` | q | 5010 | KX's standard `tick.q`, unmodified |
| `rdb` / `hdb` | q | 5011 / 5012 | Today in memory / past days on disk |
| `gw` | q | 5013 | Routes queries to RDB and HDB, joins them |
| `engine_BTC`, `engine_ETH`, `engine_USDC` | Python + C++ | – | IVs, forwards, SVI fits |
| `risk` | Python + C++ | – | Book Greeks, scenarios, P&L explain, VaR |
| `dash` | Python | 8050 | Dashboard over the gateway |

One config file (`config.env`) and three scripts (`start.sh`, `stop.sh`, `status.sh`) run the whole system.

## 3. Why three languages

| Layer | Language | Reason |
|---|---|---|
| Tick capture, storage, queries | **kdb+/q** | The industry standard for time series of market data: columnar, in-memory today plus partitioned history, and queries that summarise millions of rows next to the data (`select … by 1-minute bars`). The dashboard asks q for small summaries; only those travel. |
| Pricing, implied vol, calibration, revaluation | **C++** (pybind11) | The hot loops: an implied vol for every quote, thousands of SVI objective evaluations per fit, full revaluation of a book across scenarios and 365 historical days. The C++ IV solver is thousands of times faster than a SciPy root-finding loop (3,000–6,000× across runs and machines) ([benchmarks](../results/benchmarks.md)). |
| Feed, orchestration, risk logic, dashboard | **Python** | Websockets, REST, data wrangling and the UI are quick to write and test in Python; the heavy numerics are delegated to C++. |

The split is deliberate rather than "everything in C++": each layer is in the language a desk would use for it.

## 4. Key decisions and the evidence behind them

Each decision below was either measured before it was made, or tested against data after.

### 4.1 Quotes from the `book` channel, not `ticker` (Phase 1)
Measured before writing the feed: Deribit's `ticker` channel re-sends every option's mark and Greeks on each
index tick (~1,000 messages/s, ~85 million rows a day, mostly Deribit's model output). The `book` channel sends
only real changes of best bid or ask (~340 messages/s). **Decision:** quotes from `book`, Deribit's marks from a
REST snapshot every 10 s. About a third of the data, and all of it market events. ([lessons/02](../lessons/02-feed-handler.md))

### 4.2 KX's kdb+tick unmodified, plus a gateway (Phase 2)
The tickerplant, pub/sub and RDB are KX's own scripts, pinned to a commit and downloaded rather than
redistributed. Our q is the schema (generated from one Python definition, so feed and database can't
disagree), small RDB/HDB wrappers and the gateway. Crash recovery is tested by killing the RDB mid-session:
it rebuilds every row from the tickerplant log (`scripts/system_test.py`, section 3). ([lessons/03](../lessons/03-kdb-tick.md))

### 4.3 Our own forward from put-call parity (Phase 4)
For each expiry, a weighted regression of C − P on K across the whole chain gives the forward F and the
discount factor D (inverse options: C − P = D(1 − K/F); linear: C − P = D(F − K); in both F = −a/b).
**Evidence:** in an out-of-sample test (each fitted smile predicts the next 10 s of quotes it hasn't seen,
scored in half-spreads, on two separate halves of a recording), the parity forward beat Deribit's own forward
by 11–13% in both halves, so it was adopted. Quote-size weights were also tested and rejected (no consistent
gain, 10× more parameter jitter). ([results/phase4_harness.md](../results/phase4_harness.md), [lessons/05](../lessons/05-surface-engine.md))

### 4.4 SVI, published raw and arbitrage-free (Phase 4)
Each expiry gets a raw SVI fit and an arbitrage-free one: no butterfly arbitrage (Gatheral's density g(k) ≥ 0),
no calendar arbitrage, and Lee's moment bound on the wings, b(1 + |ρ|) ≤ 2. Constraints are enforced by penalty
continuation with safety margins. Both are published, so the cost of removing arbitrage is visible (about
0.6–3.6% worse out-of-sample, as expected). **Honest benchmark:** the final surface ties Deribit's own marks in
one half of the test and trails them by about 9% in the other, from public top-of-book quotes alone.

### 4.5 The smile rule, chosen by a pre-registered test (Phase 5)
Every delta depends on how the smile moves when spot moves. One parameter, the skew-stickiness ratio R, spans
sticky-moneyness (R = 0) to sticky-strike (R = 1). The decision rule was fixed **before** looking at the data:
pooled tenors, 5-minute horizon, hedged-P&L RMSE, must win on both halves. **Result on 4 hours of live BTC data
(9 Oct 2026):** sticky-moneyness won both halves (18.93 vs 19.05 bp, and 17.75 vs 17.79 bp of the forward). The
margin is small, and a freely fitted R overfit badly (|R̂| in the tens, standard errors as large, 19–24 bp), so the
system runs with R = 0. ([README](../README.md#portfolio-risk-phase-5), [lessons/06](../lessons/06-risk.md))

### 4.6 Full revaluation for risk, in C++ (Phase 5)
Scenario grids and VaR revalue every position on the shifted smile rather than using a Greek expansion, so
large moves and wing effects are captured. It is cheap enough to run live: the 77-cell grid takes under 1 ms for
the sample book and about 8 ms for 1,000 options; VaR/ES over 365 days with two methods about 4 ms. P&L explain
is checked by the system test: the unexplained remainder is cents against hundreds of dollars of P&L.
([benchmarks](../results/benchmarks.md))

### 4.7 Premium conventions in one place (Phase 8a)
Coin-settled options are **inverse** (premium in BTC/ETH, V = D·Black76/F); USDC options are **linear**
(V = D·Black76). The only market-specific code is `engine/conventions.py`; the regression, fits and risk are
shared. **Evidence:** Deribit lists BTC and ETH in both conventions as separate order books. Fitted independently,
they agree to a median of −0.02 vol points in ATM vol over 14 expiries, and the two BTC forwards to 0.8 bp in the
live system test. A convention error would show up as tens of vol points. ([results/phase8_multi_crypto.md](../results/phase8_multi_crypto.md), [lessons/08](../lessons/08-multi-crypto.md))

### 4.8 One VaR across every coin, with correlation from the data (Phase 8b)
Each coin holds the same sample book, sized to the BTC book's USD notional per leg. The joint VaR/ES moves
every coin by **its own actual move on the same historical day**, so correlation (and joint tail behaviour)
comes from the data rather than an estimated matrix. Each coin's contribution to ES is its average loss on
the portfolio's worst days (Euler allocation; the contributions add up exactly). Deribit has no vol index
for most alts, so BTC's DVOL stands in, and a stress line (×1.5 on those vol moves) shows how much that
assumption matters: about 25% of ES. **Result:** diversification is only about 8% of the summed ES (FHS):
crypto moves together (median correlation 0.82), and four of the eight books are the same two coins.
([results/phase8b_portfolio.md](../results/phase8b_portfolio.md), [lessons/09](../lessons/09-portfolio-var.md))

### 4.9 Two front ends over one set of analytics (Phase 6)
The live dashboard is Dash over the kdb+ gateway; every panel's data is defined twice, as a q query and as a
pandas function with the same meaning (`dashboard/sources.py` refuses to load if one is missing). The system
test runs every q version on the live gateway; the unit tests run the pandas versions. The public demo is a Streamlit app that
imports the same calculations, charts and table formatting, replaying a recorded bundle, so the demo cannot
drift from the live numbers. Streamlit replaced the first plan (a Hugging Face Docker Space) when Hugging Face
stopped hosting Docker apps on free accounts in July 2026. ([lessons/07](../lessons/07-dashboard.md))

## 5. Testing

| Layer | How | Where |
|---|---|---|
| C++ | 22 GoogleTest cases: prices vs reference values, every Greek vs bump-and-revalue, IV round trips, SVI constraints, revaluation | `cpp/tests/`, CI |
| Python | 73 pytest tests: feed parsing on recorded Deribit messages, schema ↔ q types, engine on synthetic chains with a known answer, conventions, risk identities, portfolio VaR identities (contributions sum to ES, zero diversification for identical books), dashboard views (pandas vs q), multi-asset routing | `tests/`, CI |
| Demo | Streamlit's headless AppTest: every page, both sessions, several coins | `tests/test_streamlit_app.py`, CI |
| Whole system | Starts every process, collects 60 s of live Deribit data and runs 54 checks (about 4 minutes in all): schemas, data flow, fits, risk, dashboard queries, crash recovery, end of day | `scripts/system_test.py` (local: needs the kdb+ licence) |

CI (`.github/workflows/ci.yml`) runs the first three on every push. Tests that need kdb+ talk to small fake
kdb+ servers written with PyKX in unlicensed mode (`tests/fake_kdb.py`).

## 6. Bugs caught, and what caught them

| Bug | How it showed | Caught by | Fix |
|---|---|---|---|
| A lone `/` line in q opens a block comment | the RDB started with no tables, no error | first real run | removed; a test now scans q files |
| One colon vs two in a q address | every gateway answer had count 1 (it was writing to a file) | system test | `::port` addresses |
| PyKX and Dash in one process | the dashboard segfaulted on the laptop | live run | kdb+ queries moved to a separate worker process |
| SVI Lee bound set at 4 (the SSVI constant), not 2 | a sparse 7-day SOL smile's wings exploded | new data (SOL) | bound corrected in C++, test added |
| 25-delta strike on a non-monotone N(d₁) | a SOL butterfly of +1,691 vol points | new data | first crossing out from the money, else NaN |
| ETH/USDC trade ids carry a prefix (`ETH-313151839`) | the ETH feed reconnected on every trade | new data | id parser; a bad message is skipped, never costs the connection |
| Risk bootstrap read "last spot" across all coins | BTC risk could start from SOL's price | code review for multi-asset | queries filtered by asset |

The pattern: most bugs were found by **running against real data** or by the **end-to-end system test**, not
by unit tests alone. That is why the end-to-end system test exists alongside the unit tests.

## 7. Limitations and next steps

- **Sample books, and a vol proxy.** Each coin holds the same constructed book (seven option legs across four
  expiries, 1 week to 3 months, plus a hedge), not a real trading history. For coins without a Deribit vol index
  the VaR uses BTC's DVOL moves; the stress line shows this moves ES by about 25%. Next: use our own HDB of fitted
  surfaces as each coin's vol history once it is long enough. HYPE has only 131 days of price history, so it
  joins only a separately labelled all-coins figure.
- **The smile-rule verdict is one afternoon.** Four hours of BTC data, a small margin. It should be re-run over
  several days and market regimes before it is trusted.
- **Wings are extrapolated.** Beyond the quoted strikes the arbitrage-free SVI can depart a long way from Deribit's
  marks (visible on some SOL expiries in the demo). Next: SSVI across expiries, or wing constraints tied to the
  last quoted strike.
- **Top-of-book only.** Deribit's marks still beat our surface in one half of the test; Deribit sees depth and
  order flow. Next: add trades and depth.
- **No American exercise, no rate curve.** Fine for Deribit (European, the forward carries the rate). Equity
  markets (planned: US, UK, India) need both.
- **One machine.** Everything runs on one laptop under WSL; the design (tickerplant subscribers, a gateway)
  scales out by moving processes to other hosts, but that has not been done.
- **kdb+ timings are not published**, following the project's licence rule; Python and C++ timings are
  ([benchmarks](../results/benchmarks.md)).
