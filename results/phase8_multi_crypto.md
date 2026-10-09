# Phase 8a: every Deribit option — BTC, ETH and seven USDC coins — 9 Oct 2026

Data: 25 minutes of all three Deribit option feeds recorded together (12:23–12:48 UTC, 9 Oct 2026):
BTC (inverse, 950 options), ETH (inverse, 844), USDC (linear, 3,910 across seven coins);
2.44 million book updates. Reproduce:

    for g in BTC ETH USDC; do python -m feed --currency $g --record --minutes 25 --out ~/rec/$g & done; wait
    python scripts/cross_convention.py --rec ~/rec/BTC ~/rec/ETH ~/rec/USDC

## 1. Inverse vs linear: the same coin from two independent books

Deribit lists BTC and ETH options twice: coin-settled (inverse premium, in BTC/ETH) and
USDC-settled (linear premium, in USDC). Separate order books, conventions, contract sizes and
ticks. Each book is fitted on its own; one comparison point per minute. Vol figures in vol
points; "<1vp" is the share of minutes in which the two books' values were within 1 vol point.

```
expiry            days  pts | ATM inv    lin   diff  <1vp | RR inv    lin   diff  <1vp | BF inv    lin   diff |   F gap
BTC-12OCT26        2.8   26 |   25.60  25.16  +0.49  100% |  -3.87  -3.73  -0.09  100% |  +1.12  +1.03  +0.08 |   +0.3bp
BTC-13OCT26        3.8   26 |   28.50  28.38  +0.15  100% |  -3.17  -2.86  -0.30  100% |  +1.15  +1.08  +0.07 |   -0.2bp
BTC-16OCT26        6.8   26 |   32.18  32.04  +0.13  100% |  -1.80  -1.78  -0.00  100% |  +1.07  +1.05  +0.02 |   +0.3bp
BTC-23OCT26       13.8   26 |   32.76  32.64  +0.11  100% |  -0.64  -0.59  -0.07  100% |  +1.27  +1.20  +0.08 |   +0.4bp
BTC-30OCT26       20.8   26 |   33.36  33.33  +0.03  100% |  -0.76  -0.56  -0.21  100% |  +1.16  +1.09  +0.07 |   +1.7bp
BTC-27NOV26       48.8   26 |   36.77  36.74  +0.03  100% |  -0.80  -0.80  -0.00  100% |  +1.06  +1.06  +0.01 |   +1.7bp
BTC-25DEC26       76.8   26 |   37.32  37.37  -0.04  100% |  -0.88  -0.85  -0.04  100% |  +1.14  +1.11  +0.03 |   +3.3bp
ETH-12OCT26        2.8   26 |   32.69  32.98  -0.33  100% |  -8.11  -8.16  +0.02  100% |  +1.63  +1.54  +0.08 |   +1.0bp
ETH-13OCT26        3.8   26 |   35.85  36.38  -0.57  100% |  -6.39  -6.46  +0.06  100% |  +1.55  +1.42  +0.14 |   +0.9bp
ETH-16OCT26        6.8   26 |   40.79  41.00  -0.20  100% |  -4.86  -4.82  -0.01  100% |  +1.58  +1.54  +0.04 |   +0.3bp
ETH-23OCT26       13.8   26 |   42.60  42.69  -0.10  100% |  -3.12  -3.06  -0.03  100% |  +1.55  +1.51  +0.04 |   +0.2bp
ETH-30OCT26       20.8   26 |   44.30  44.39  -0.13  100% |  -2.32  -2.17  -0.13  100% |  +1.43  +1.40  +0.03 |   -0.4bp
ETH-27NOV26       48.8   26 |   48.59  48.68  -0.09   96% |  -1.85  -1.63  -0.23  100% |  +1.44  +1.51  -0.07 |   +1.5bp
ETH-25DEC26       76.8   26 |   49.59  49.59  -0.00   92% |  -1.18  -0.99  -0.16   92% |  +1.52  +1.53  -0.02 |   +2.3bp

ATM vol, inverse minus linear: median -0.02 vol pts, point-weighted mean |diff| 0.17 vol pts over 14 expiries
```

**Reading:** across 14 expiries the two books' ATM vols differ by a median of −0.02 vol points;
risk reversals and butterflies agree to about a tenth of a vol point, and the parity forwards to
a few basis points (the USDC forward runs slightly below the coin-settled one at longer tenors,
consistent with the different margin currency). A convention error would show up as a gap of
tens of vol points, not tenths.

## 2. Every asset: fits, forwards and throughput

One engine per asset, all nine replayed in one process on the recorded clock. "~30d" is the
expiry nearest 30 days. The forward gap is our parity forward minus Deribit's own, measured on
every refit; "|gap|/se" is that gap over its regression standard error (about 1 means the two
agree to within estimation noise).

```
2,438,631 quotes over 25.0 min replayed in 469 s  ->  5,204 quotes/s processed vs 1,627/s arriving  (headroom x3.2)
AVAX_USDC  deribit_usdc  expiries fitted  5 | ~30d AVAX_USDC-30OCT26  ATM 73.2% RR25 +4.69 BF25 +2.55 inband 100% | parity D median 1.00009, |F - Deribit F| median 5.7 bp | over time: median gap -0.4 bp, median |gap|/se 0.70, und age p50 0.0s p99 0s
BTC        deribit       expiries fitted 10 | ~30d BTC-30OCT26        ATM 33.2% RR25 -0.80 BF25 +1.17 inband 99% | parity D median 0.99996, |F - Deribit F| median 1.5 bp | over time: median gap +0.8 bp, median |gap|/se 0.55, und age p50 0.0s p99 0s
BTC_USDC   deribit_usdc  expiries fitted  7 | ~30d BTC_USDC-30OCT26   ATM 33.2% RR25 -0.64 BF25 +1.10 inband 100% | parity D median 1.00037, |F - Deribit F| median 1.6 bp | over time: median gap +0.0 bp, median |gap|/se 0.52, und age p50 0.0s p99 0s
ETH        deribit       expiries fitted 10 | ~30d ETH-30OCT26        ATM 44.4% RR25 -2.07 BF25 +1.37 inband 100% | parity D median 0.99985, |F - Deribit F| median 2.7 bp | over time: median gap +2.4 bp, median |gap|/se 0.69, und age p50 0.0s p99 0s
ETH_USDC   deribit_usdc  expiries fitted  7 | ~30d ETH_USDC-30OCT26   ATM 44.6% RR25 -2.40 BF25 +1.28 inband 100% | parity D median 0.99997, |F - Deribit F| median 2.4 bp | over time: median gap +1.7 bp, median |gap|/se 0.87, und age p50 0.0s p99 0s
HYPE_USDC  deribit_usdc  expiries fitted  5 | ~30d HYPE_USDC-30OCT26  ATM 54.8% RR25 +3.38 BF25 +2.08 inband 100% | parity D median 0.99962, |F - Deribit F| median 2.8 bp | over time: median gap +3.3 bp, median |gap|/se 1.47, und age p50 0.0s p99 0s
SOL_USDC   deribit_usdc  expiries fitted  5 | ~30d SOL_USDC-30OCT26   ATM 51.0% RR25 +3.10 BF25 +2.37 inband 91% | parity D median 0.99896, |F - Deribit F| median 1.1 bp | over time: median gap -0.0 bp, median |gap|/se 0.42, und age p50 0.0s p99 0s
TRX_USDC   deribit_usdc  expiries fitted  5 | ~30d TRX_USDC-30OCT26   ATM 22.7% RR25 +0.40 BF25 +1.13 inband 96% | parity D median 0.99839, |F - Deribit F| median 1.2 bp | over time: median gap +0.9 bp, median |gap|/se 0.41, und age p50 0.0s p99 0s
XRP_USDC   deribit_usdc  expiries fitted  5 | ~30d XRP_USDC-30OCT26   ATM 54.4% RR25 +4.14 BF25 +3.22 inband 100% | parity D median 0.99958, |F - Deribit F| median 3.0 bp | over time: median gap -0.8 bp, median |gap|/se 0.45, und age p50 0.0s p99 0s
```

Live, each feed group runs in its own engine process (`FEED_GROUPS="BTC ETH USDC"`), so the
headroom per process is higher than the single-process replay figure above.

## 3. Bugs found on the new data (all fixed, with regression tests)

| Bug | Symptom | Fix |
|---|---|---|
| ETH/USDC trade ids are strings (`"ETH-313151839"`) | the feed reconnected on every trade, losing ~10 s of every instrument (the earlier USDC recording: 23 such reconnects) | `normalise.trade_id`; a malformed message is skipped and counted, never costs the connection |
| SVI Lee bound set at 4 (the SSVI constant) instead of 2 | a sparse 7-day SOL smile's wings exploded | `kLeeMax = 2` in `cpp/src/svi.cpp` |
| 25-delta strike interpolated over a non-monotone N(d₁) | SOL 25-delta butterfly of +1,691 vol points | `delta_strike` takes the first crossing out from the money, else NaN |
| risk bootstrap read `last price from spot` across all coins | BTC risk could start from SOL's price | bootstrap filters `where asset=`BTC` |
