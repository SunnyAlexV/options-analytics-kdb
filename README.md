# options-analytics-kdb

A live options analytics system for BTC options on Deribit. It is built the way an options desk builds one:

- **kdb+/q** for tick capture, storage and queries, on KX's standard kdb+tick architecture
- **C++** for pricing, implied vol and calibration
- **Python** for the data feed, orchestration and the dashboard

> **Status:** Phases 0–4 complete (feed, kdb+ core, C++ pricing, live vol surface); Phase 5 (risk and P&L explain) next. See [PLAN.md](PLAN.md) for the full design and roadmap.

## Architecture

```
Deribit ──► Feed handler (Python) ──► Tickerplant (q) ──► Real-time DB (q) ──┐
            book · trades · index      logs every update   today, in memory   ├──► Gateway (q) ──► Dashboard
            snapshots · ref data            │                    │ end of day  │
                                            │                    ▼             │
                                            │             Historical DB (q) ───┘
                                            │             one folder per date
                                            └──► Calc engine (Python + C++): IV · Greeks · SVI · risk   [Phase 3+]
```

## What it computes

Phase 3 onwards:

- **Implied vols** — Black-76 on each expiry's forward, with inverse (BTC-settled) conventions.
- **Greeks** — first, second and cross order.
- **The volatility surface** — SVI/SSVI, with arbitrage checks.
- **Surface metrics** — risk reversals, butterflies, term structure.
- **Realised vol and the variance risk premium.**
- **Portfolio risk** — scenario grids, P&L explain, VaR.

## Quick start

Runs on Linux, or on Windows through WSL. See [SETUP.md](SETUP.md).

```bash
bash scripts/get_kdb_tick.sh     # once: fetch KX's kdb+tick (pinned, hash-checked)
pip install -e .                 # compile the C++ pricing library into the `pricing` package
bash scripts/start.sh            # tickerplant, HDB, RDB, gateway, live feed, surface engine
bash scripts/status.sh
bash scripts/stop.sh
python scripts/system_test.py    # end-to-end test on a throwaway copy of the system
pytest                           # unit tests (Python)
python scripts/validate_deribit.py   # our implied vols vs Deribit's mark IVs, live
python scripts/bench_iv.py           # C++ vs Python timings
python scripts/eval_surface.py --recording ~/kdbdata/raw   # score surface design choices
```

## Pricing library (C++, Phase 3)

- **Black-76** on each expiry's forward, with Deribit's inverse (BTC-settled) convention.
- **Greeks:** price plus 12 Greeks — first, second and cross order — each verified against bump-and-revalue.
- **Implied vol:** a safeguarded Newton solver, accurate to within the double-precision limit.

On 1,000 options:

| | C++ | SciPy `brentq` loop |
|---|---|---|
| Implied vol, per option | ~0.6 µs | ~3,900 µs |

Against Deribit's own marks, our implied vols match `mark_iv` to a median of about 1 bp on well-conditioned options. Deribit rounds `mark_iv` to 1 bp.

## Live volatility surface (Phase 4)

A streaming real-time engine subscribes to the tickerplant. For every quote it computes bid, ask and mid implied vols. For each expiry it estimates the forward **and the BTC discount factor** from put-call parity across the whole chain. It then fits **two SVI smiles** per expiry (raw, and arbitrage-free under butterfly, calendar and Lee constraints) and publishes everything back through the tickerplant.

**Design choices are decided by an evaluation harness**, not by assumption: each configuration predicts the next 10 seconds of quotes out of sample, and is checked on two separate halves of the data.

- **Parity forward:** beat Deribit's own forward by ~11% (adopted).
- **Quote-size weights:** no consistent gain (rejected).
- **The final surface:** ties Deribit's own marks in one half and trails them by ~9% in the other, built from public top-of-book quotes alone.

Details are in [results/phase4_harness.md](results/phase4_harness.md) and [lessons/05](lessons/05-surface-engine.md).

## Credits and licences

- The tickerplant, pub/sub and RDB scripts are **KX's standard [kdb+tick](https://github.com/KxSystems/kdb-tick)**, used unmodified. They are downloaded at a pinned commit rather than redistributed here.
- kdb+ requires a free [KDB-X Community Edition](https://developer.kx.com) licence. The licence file and binaries are not part of this repository.
- This project's own code is [MIT](LICENSE)-licensed.
