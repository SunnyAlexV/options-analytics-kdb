# Phase 8b: portfolio VaR across every coin — 09 Oct 2026 12:39 UTC

Source: bundle `bundle_all`. Reproduce: `python scripts/portfolio_report.py --bundle streamlit_app/bundle_all`.

Each coin holds the same seven-leg sample book (risk/book.py), sized to the BTC book's USD notional per leg and delta-hedged. Each of the last 365 days moves every coin by its own move that day (price index; vol: own DVOL for BTC and ETH and their USDC markets, BTC's DVOL as a proxy for the rest). Full revaluation in C++; 1-day horizon; USD.

## Headline (FHS)

| measure | value |
|---|---|
| Portfolio ES 97.5% | 211,063 (USD, 1 day, FHS, 8 coins) |
| Portfolio VaR 99% | 212,064 (USD, 1 day, FHS) |
| Sum of coins alone | 228,450 (ES 97.5%, each book on its own) |
| Diversification | 17,386 (8% of the sum) |
| Stress ES 97.5% | 264,860 (proxied coins' vol moves ×1.5) |
| Backtest | 2 / 365 (VaR 99% exceptions, Kupiec p = 0.34) |
| With HYPE_USDC | 224,794 (ES 97.5% on the 130 days all coins share) |

## Variants

**FHS**

| variant | days | VaR 99% | ES 97.5% | ES 99% | sum alone ES 97.5% | diversification (ES) |
|---|---|---|---|---|---|---|
| main: coins with a full year | 365 | 212,064 | 211,063 | 228,098 | 228,450 | 17,386 |
| stress: proxied vol moves x1.5 | 365 | 271,743 | 264,860 | 288,440 | 283,151 | 18,291 |
| all coins, on their shared days | 130 | 229,814 | 224,794 | 239,776 | 242,512 | 17,718 |

**HS**

| variant | days | VaR 99% | ES 97.5% | ES 99% | sum alone ES 97.5% | diversification (ES) |
|---|---|---|---|---|---|---|
| main: coins with a full year | 365 | 233,405 | 228,535 | 255,918 | 260,476 | 31,941 |
| stress: proxied vol moves x1.5 | 365 | 289,233 | 298,517 | 346,304 | 327,894 | 29,377 |
| all coins, on their shared days | 130 | 255,699 | 250,381 | 257,575 | 282,546 | 32,165 |

## By coin (FHS)

Contribution = the coin's average loss on the portfolio's worst 2.5% of days; the contributions add up to the portfolio's ES 97.5%.

| coin | ES 97.5% alone | contribution to ES | share of ES | VaR 99% alone |
|---|---|---|---|---|
| AVAX_USDC | 44,057 | 42,715 | 20% | 44,238 |
| XRP_USDC | 35,075 | 34,058 | 16% | 36,656 |
| SOL_USDC | 32,186 | 30,808 | 15% | 33,389 |
| ETH_USDC | 29,055 | 23,775 | 11% | 30,439 |
| ETH | 28,943 | 23,688 | 11% | 30,332 |
| BTC_USDC | 21,881 | 20,885 | 10% | 22,529 |
| BTC | 21,866 | 20,849 | 10% | 22,446 |
| TRX_USDC | 15,387 | 14,287 | 7% | 16,784 |

Sum of contributions: 211,063.17; portfolio ES 97.5%: 211,063.17.

## Each coin's own VaR (vares table)

| coin | VaR 99% (FHS) | ES 97.5% (FHS) | backtest (FHS) | Kupiec p |
|---|---|---|---|---|
| AVAX_USDC | 44,238 | 44,057 | 4 / 365 | 0.86 |
| BTC | 22,446 | 21,866 | 4 / 365 | 0.86 |
| BTC_USDC | 22,529 | 21,881 | 4 / 365 | 0.86 |
| ETH | 30,332 | 28,943 | 7 / 365 | 0.12 |
| ETH_USDC | 30,439 | 29,055 | 7 / 365 | 0.12 |
| HYPE_USDC | 33,552 | 30,921 | too little history | – |
| SOL_USDC | 33,389 | 32,186 | 2 / 365 | 0.34 |
| TRX_USDC | 16,784 | 15,387 | 4 / 365 | 0.86 |
| XRP_USDC | 36,656 | 35,075 | 3 / 365 | 0.72 |

## Correlation of daily moves

Off-diagonal correlations over the window range from 0.35 to 1.00; median 0.82.

|  | AVAX_USDC | BTC | BTC_USDC | ETH | ETH_USDC | SOL_USDC | TRX_USDC | XRP_USDC |
|---|---|---|---|---|---|---|---|---|
| AVAX_USDC | 1.0 | 0.74 | 0.74 | 0.77 | 0.77 | 0.78 | 0.35 | 0.73 |
| BTC | 0.74 | 1.0 | 1.0 | 0.91 | 0.91 | 0.88 | 0.4 | 0.87 |
| BTC_USDC | 0.74 | 1.0 | 1.0 | 0.91 | 0.91 | 0.88 | 0.4 | 0.87 |
| ETH | 0.77 | 0.91 | 0.91 | 1.0 | 1.0 | 0.89 | 0.4 | 0.82 |
| ETH_USDC | 0.77 | 0.91 | 0.91 | 1.0 | 1.0 | 0.89 | 0.4 | 0.82 |
| SOL_USDC | 0.78 | 0.88 | 0.88 | 0.89 | 0.89 | 1.0 | 0.38 | 0.83 |
| TRX_USDC | 0.35 | 0.4 | 0.4 | 0.4 | 0.4 | 0.38 | 1.0 | 0.38 |
| XRP_USDC | 0.73 | 0.87 | 0.87 | 0.82 | 0.82 | 0.83 | 0.38 | 1.0 |
