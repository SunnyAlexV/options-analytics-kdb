# options-analytics-kdb

A live options analytics system for BTC options on Deribit. It is built the way an options desk builds one:

- **kdb+/q** for tick capture, storage and queries
- **C++** for pricing, implied vol and calibration
- **Python** for the data feed, orchestration and the dashboard

> **Status:** Phase 0 (foundations) — under active development. See [PLAN.md](PLAN.md) for the full design and roadmap.

## Architecture

```
Deribit ──► Feed handler (Python) ──► Tickerplant (q) ──► Real-time DB (q) ──┐
                                           │                                  ├──► Gateway (q) ──► Dashboard (Streamlit)
                                           ├──► Calc engine (Python + C++)    │
                                           │      IV · Greeks · SVI · risk    │
                                           └──► end of day ──► Historical DB (q)
```

## What it computes

- **Implied vols** — Black-76 on each expiry's forward, with inverse (BTC-settled) conventions.
- **Greeks** — first, second and cross order.
- **The volatility surface** — SVI/SSVI, with arbitrage checks.
- **Surface metrics** — risk reversals, butterflies, term structure.
- **Realised vol and the variance risk premium.**
- **Portfolio risk** — scenario grids, P&L explain, VaR.

## Setup

Runs on Linux, or on Windows through WSL. See [SETUP.md](SETUP.md).

kdb+ requires a free [KDB-X Community Edition](https://developer.kx.com) licence. The licence file and binaries are not part of this repository.

## Licence

[MIT](LICENSE).
