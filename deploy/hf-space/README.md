---
title: BTC Options Desk
emoji: 📈
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Live BTC options analytics (kdb+, C++, Python), replayed
---

A replay of a recorded session of [options-analytics-kdb](https://github.com/SunnyAlexV/options-analytics-kdb):
Deribit BTC options captured with kdb+tick, implied vols and arbitrage-free SVI smiles fitted
in C++, portfolio risk, P&L explain and VaR, shown in the same dashboard that runs on the live system.

The recording plays at 10x speed and loops. Everything shown was computed by the system itself;
see the repository for the code, the tests and the write-ups behind each number.
