# Phase 6 walkthrough: the dashboard and its public demo

**What was built:** a dark trading-desk dashboard (Dash + Plotly) with three pages: Market, Risk and System.

- On your machine it reads the live system through the kdb+ gateway every 2 seconds.
- For everyone else, a **public demo** replays a recorded session through the very same analytics and charts. It runs free on Streamlit Community Cloud, with no kdb+, no C++ and no licence.

```
dashboard/sources.py    every view defined twice: a q query (live) and a pandas function (replay)
dashboard/analytics.py  what is derived from the views: smiles, density, chain, VRP, carry, risk tiles...
dashboard/figures.py    one Plotly figure per chart
dashboard/theme.py      colour tokens (validated dark palette), CSS, table style
dashboard/app.py        layout and callbacks
dashboard/bundle.py     a day of every table as Parquet: from kdb+ or rebuilt from a recording
dashboard/demo.py       the demo as a WSGI app (gunicorn)
scripts/make_demo_bundle.py, scripts/deploy_demo.py, deploy/hf-space/
tests/test_dashboard.py 9 tests;  scripts/system_test.py section [2d] runs every q query live
```

## 1. Where the data comes from

The standard kdb+ client pattern: **send the question to the data, not the data to the question.**

The dashboard never pulls today's million quote rows. Each panel asks for a small **view**, and q computes it next to the data:

| view | q (runs on the RDB, via the gateway) | rows back |
|---|---|---|
| latest per instrument | `0!select by sym from iv` | ~950 |
| index, 1-minute bars | `0!select price:last price by time:0D00:01 xbar time from spot` | ≤ 1,440 |
| smile metrics per minute | `0!select last atmvol, last rr25, last bf25, last F, last T by sym, time:0D00:01 xbar time from surface` | expiries × minutes |
| feed → tickerplant delay | `0!select ms:1e-6*med `long$time-`timespan$recv by time:0D00:01 xbar time from quote ...` | ≤ 1,440 |
| latest risk snapshot | `select from risk where time=max time` | ~10 |

**PyKX runs in its own process.** The first live run on your machine died with a bare "Segmentation fault": a crash in native code, not a Python error. PyKX loads kdb+'s own native library into its process. Next to the dashboard's native stack (Dash, pyarrow, ...), that can crash depending on library versions. Here it happened in a different import order than on your machine, which shows how version-dependent it is.

The fix is isolation, the same reason the test servers run as separate processes: `KdbWorker` is the **only** place PyKX is imported. It runs the queries in a child process and sends back plain DataFrames. If the worker ever dies, the next refresh starts a new one. The bundle maker uses the same worker. `faulthandler` is now on, so any future native crash prints where it happened. A wire-level test runs every view through the worker against a stand-in gateway and checks the answers equal the replay's.

The gateway gained two entry points, `.gw.rdbq` and `.gw.hdbq`, which forward a prepared query to the RDB or HDB.

**One honest caveat:** these entry points run *any* q expression. That's fine on your own machine, but a shared gateway would accept only a list of named queries.

**Testing.** Every view is also defined as a **pandas function with the same meaning** (`P` next to `Q` in `sources.py`). A test asserts that the two sets of definitions match one for one. The replay demo uses the pandas versions. I couldn't run q while building this, so the system test's new section [2d] runs **every q query** against the real gateway and checks that the key views return data.

## 2. The Market page, and the maths behind each panel

**30-day ATM vol (our own DVOL-like number).** Variance, not vol, adds up over time, so we interpolate *total variance* σ²T linearly in T between the two expiries either side of 30 days:

  σ₃₀ = √( w(30d) / (30/365) ),  w = interpolation of σ²_ATM·T

**Smile.** For the chosen expiry, the chart shows:
- every quoted option as its mid IV, with bid–ask error bars;
- Deribit's mark IV for comparison;
- our raw and arbitrage-free SVI fits.

Out-of-the-money options are shown on each side: puts below the forward, calls above.

**Implied distribution.** The risk-neutral density comes straight from the arbitrage-free smile (Gatheral 2004). With d₋(k) = −k/√w − √w/2:

  p(k) = g(k) / √(2πw) · exp(−d₋²/2),   g(k) = (1 − k·w′/(2w))² − (w′²/4)(1/w + 1/4) + w″/2

Two tests check this formula:
- it integrates to 1, within 10⁻⁶;
- it matches the Breeden–Litzenberger density, ∂²C/∂K² computed numerically from Black-76 prices, to within 10⁻⁴ of its peak.

The chart plots it against the BTC price at expiry.

**3D surface in standardised moneyness.** The first version used raw ln(K/F). A 3-day smile then had to be drawn out to ±0.6, about ±5 of its own standard deviations, and its wings shot up to 140% vol, drowning everything else. The fix is to plot each expiry against z = ln(K/F) / (σ_ATM·√T), so the same z covers the same range of deltas on every expiry.

**Option chain and "rich / cheap".**

  rich = (mid IV − fitted IV) / half-spread

Above +1, the mid is rich to the arbitrage-free smile by more than half the spread; below −1 it's cheap.

The test of this found something real. Each IV is computed the moment its quote arrives, on the **forward known at that moment**. If the forward moves before the next refit, calls look rich and puts cheap at the same strike. So the chain compares each IV with the fit at **that row's own forward**. On live data the refit runs every 0.5 s, so the residual effect is small.

**Forwards table.** For each expiry:
- **basis:** F/S − 1;
- **annualised carry:** ln(F/S)/T;
- **parity forward minus Deribit's**, in USD;
- **BTC discount factor D** from the parity regression, and the BTC rate it implies, r = −ln(D)/T;
- **fit quality.**

**Variance risk premium, 5 years.** DVOL is the market's implied vol for the *next* 30 days. So the fair comparison is with the realised vol of the **following** 30 days, not the past 30. A test checks the alignment on a series whose volatility jumps on a known day. The subtitle reports the average premium and how often implied exceeded the realised vol that followed.

**Realised vs implied today:**
- realised: 1-minute log returns, a 30-minute rolling standard deviation, annualised by √(minutes in a year), since crypto never closes;
- implied: the 30-day ATM vol, minute by minute.

**Flow:**
- the trade tape, with USD prices (price × index);
- the put/call volume ratio and the share of buyer-initiated volume;
- open interest by strike, and traded volume by strike.

These are two separate charts because their scales differ by about 100 times.

## 3. The Risk page

- **Tiles:** value, spot delta, cash delta, gamma, vega, theta, VaR 99%, ES 97.5% and the smile rule R in use.
- **Scenario heatmap:** 11 BTC moves × 7 vol shifts. It uses a **diverging** blue (gain) ↔ red (loss) scale with zero pinned to a neutral grey, so the colour itself says gain or loss. The axes are categorical, so every cell is the same size.
- **P&L explain waterfall:** the cumulative terms add up exactly to the actual P&L, because "unexplained" is one of the bars. A test checks the sum.
- **Vega buckets:** by expiry, by delta, and by smile shape (ATM, RR and BF).
- **Greeks and P&L over the day** (small multiples).
- **Positions with live Greeks:** each option's per-contract Greeks from the `iv` table, times its quantity. A future counts as delta 1.
- **The VaR table,** with the backtest and Kupiec's p-value.

## 4. The System page

- Quote updates per minute.
- Latency, exchange → feed (median and max), and feed → tickerplant on the live system.
- Crossed and one-sided books, and feed outages.
- **Fit quality per expiry:** quotes used, RMSE, the share of quotes inside their bid–ask, and the extra error paid to remove arbitrage.
- Rows per table.

## 5. Design rules applied

- **Colour:** the dark steps of a validated palette. A validator script checked the lightness band, the chroma floor, colour-blind separation (worst adjacent ΔE 8.4, target ≥ 8) and contrast against the chart surface.
- **Series:** categorical colours in a fixed order. Status colours are reserved and never stand in for a series.
- **One y-axis per chart.** Two measures of different scale get two charts: open interest vs volume, and ATM vol vs RR/BF over time.
- **Legends:** whenever there are two or more series, placed under the title. Gridlines are hairlines.
- **Zoom survives the refresh.** Charts have fixed ids and `uirevision`, so your zoom and 3D camera stay put while data updates every 2 s.

## 6. The public demo

```bash
# 1. a bundle: from the live system (best; exactly what ran) ...
python scripts/make_demo_bundle.py --gw 5013 --date 2026.10.09 --out demo/bundle
#    ... or rebuilt offline from feed recordings (one folder per feed group)
python scripts/make_demo_bundle.py --recording ~/rec/BTC ~/rec/ETH ~/rec/USDC --out demo/bundle
# 2. thin it to one row per instrument per minute, into the folder the demo app reads
python scripts/thin_bundle.py demo/bundle streamlit_app/bundle --every 60
# 3. commit and push; Streamlit Community Cloud redeploys from GitHub
```

**Why Streamlit, not the Dash app itself.** The first plan was a Hugging Face Space running the Dash
app in Docker (`deploy/hf-space/`, `scripts/deploy_demo.py`, both kept). In July 2026 Hugging Face
stopped hosting Docker Spaces on free accounts. Streamlit Community Cloud is free, deploys straight
from the GitHub repo, and gives apps up to 2.7 GB of memory; the demo uses about 450 MB.

**One set of numbers, two front ends.** `streamlit_app/app.py` is only a layout. It imports the same
`ReplaySource` (dashboard/sources.py), the same calculations (`dashboard/analytics.py`), the same Plotly
figures (`dashboard/figures.py`) and the same table formatting (`dashboard/tables.py`, shared with the
Dash app since this change). So the demo cannot drift from what the live dashboard computes.

- **The replay clock** runs at 10x real time and loops; the source is cached once per server
  (`st.cache_resource`), so every visitor sees the same moment, like a live market.
- **Refreshing** uses `st.fragment(run_every=3)`: only the page body reruns, not the header.
- **Pausing** ("Live replay" off) swaps the clock for a slider over every minute of the session; the
  same views are computed at that moment (`ReplaySource.views(names, now)`).
- **Dependencies:** `streamlit_app/requirements.txt` sits next to the entrypoint, so Community Cloud
  installs only the demo's six packages, not the project's kdb+/C++ environment. The theme
  (`.streamlit/config.toml`) must sit at the repository root.
- **Thinning:** the committed bundle keeps the last row per instrument per minute (risk: one whole
  snapshot per minute, never a mix of two moments). 44 MB became 24 MB with no visible change at chart
  resolution.

**How it was verified:** the six pinned packages were installed in a clean environment, the app was
run headless with Streamlit's `AppTest` (every page, live and paused; `tests/test_streamlit_app.py`)
and in a real server, and each page was rendered in a headless browser with no errors.

## 7. What the tests prove

| test | proves |
|---|---|
| `test_svi_density_integrates_to_one_and_matches_breeden_litzenberger` | the density formula |
| `test_svi_w_matches_cpp` | the numpy SVI equals the C++ one exactly |
| `test_constant_maturity_interpolates_total_variance` | 30-day ATM is variance-interpolated |
| `test_vrp_uses_realised_vol_of_the_following_days` | VRP compares like with like |
| `test_every_view_has_a_q_and_a_pandas_definition` | live and replay stay in step |
| `test_bundle_has_every_table` | recording → engine → risk → bundle, all 15 tables |
| `test_all_views_analytics_and_figures_render` | every view, analytic and chart on that bundle; waterfall adds up |
| `test_scenario_matrix_puts_the_largest_vol_shift_on_top` | the heatmap reads the right way up |
| `test_app_builds` | the app assembles |
| system test [2d] | every q query runs on the real gateway; the page is served |
