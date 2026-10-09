# Benchmarks — 10 Oct 2026

Machine: Intel(R) Xeon(R) Processor @ 2.10GHz, 2 logical cores, Linux 6.18.44-fc-v80; Python 3.13.16, NumPy 2.5.3.
Reproduce: `python scripts/benchmarks.py --recording <recordings>/BTC <recordings>/ETH <recordings>/USDC` (recordings made with `python -m feed --currency <group> --record`). Each time is the best of several runs. The numbers belong to this machine: rerun the command to measure yours.

## 1. Pricing and implied vol (C++ via pybind11)

A synthetic chain of 1,000 options (strikes ±100% in log-moneyness, 1 day to 1.5 years, vols 30–120%), per option. The solver returned a vol for 990 of 1,000 prices; error against the true vol: median 4.4e-16, worst 2.7e-04. Every price it declined is at least 7.3 standard deviations from the money, where the price is zero or pure intrinsic value to double precision, so no vol can be recovered; the 5 solves off by more than 1e-8 are all at least 5.9 standard deviations out.

| task | C++ per option | baseline | baseline per option | speed-up |
|---|---|---|---|---|
| Black-76 price | 0.05 µs | NumPy vectorised | 0.21 µs | 4× |
| implied vol (safeguarded Newton) | 0.43 µs | SciPy brentq, per option | 2.30 ms | 5,302× |
| price + 12 Greeks | 0.11 µs | – | – | – |

## 2. Smile fitting (the surface engine's full refit)

10 expiries (7 to 277 days), 660 quotes, each expiry: put-call parity forward and discount factor, bid/mid/ask implied vols, raw SVI and arbitrage-free SVI (butterfly, calendar and Lee constraints).

| measure | time |
|---|---|
| full refit, all 10 expiries | 180.27 ms |
| per expiry | 18.03 ms |
| share of it spent in the SVI fits | 96% |

The live engine refits the expiries whose quotes changed, at most every 0.5 s.

## 3. Portfolio risk (full revaluation in C++ on the fitted smiles)

| task | sample book | 1,000-option book |
|---|---|---|
| position Greeks (price, delta, gamma, vega, theta, vanna, volga, smile-rule delta) | 1.75 ms | 11.55 ms |
| scenario grid: 11 spot moves × 7 vol shifts, each cell a full revaluation | 297.26 µs | 7.78 ms |
| VaR/ES: 365 historical days × 2 methods (HS, FHS), full revaluation | 4.50 ms | – |

The sample book is risk/book.py's, built on a three-expiry test market: 6 options and 1 futures hedge (two of its target legs map to the same option on this market; the live book has seven options and a hedge).

## 4. Surface engine throughput on recorded data

2,438,631 recorded Deribit quote updates over 25.0 minutes, 9 assets, replayed through fresh engines in one process on a simulated clock (implied vol for every quote, refits as live).

| measure | value |
|---|---|
| processed | 5,750 quotes/s |
| arriving (live rate in the recording) | 1,627 quotes/s |
| headroom | 3.5× |

Live, each feed group (BTC, ETH, USDC) has its own engine process, so headroom per process is higher.

## What is not here

No timing that involves kdb+/q (tickerplant, RDB, gateway queries, feed → tickerplant delay) is published. The project's rule, set with the KDB-X Community licence terms in mind, is to publish such numbers only with KX's written approval (PLAN.md, Decisions). `python scripts/system_test.py` prints them locally.
