# Phase 1 walkthrough: the feed handler

**What it does:** connects to Deribit, receives live BTC options data, converts every message into a standard row, checks data quality, and passes the rows on in batches.

**Where it's going:** for now the rows go to the screen and to recording files. In Phase 2 they go to the kdb+ tickerplant instead.

## 1. A measurement that changed the design

We agreed to take quotes from `ticker.{instrument}.100ms` for every option. Before writing any code, I measured each Deribit channel for 20 seconds across all 956 BTC options:

| Channel | Messages/s | Data rate | What it carries |
|---|---|---|---|
| `ticker.{inst}.100ms` | **~1,000** | 770 KB/s | Quotes **plus** Deribit's mark, IV and Greeks |
| `ticker.{inst}.agg2` | ~900 | 680 KB/s | Same, aggregated (barely smaller) |
| `book.{inst}.none.1.100ms` | **~340** | 81 KB/s | Best bid and ask, sent **only when they change** |
| `markprice.options.btc_usd` | 1 (≈180 rows) | 18 KB/s | Deribit marks and IVs in bulk |
| `trades.option.BTC.100ms` | ~0.1 | tiny | Every trade |
| `deribit_price_index.btc_usd` | 1 | tiny | Spot index |

**Why the ticker channel is so heavy:** every time the BTC index ticks, Deribit recomputes the mark price, IV and Greeks of all ~950 options and re-sends each one, even when no quote has changed. Stored in q, that would be about **85 million rows a day**, and most of them would be Deribit's model output rather than market data.

**The decision:**
- **Quotes:** the `book` channel, which carries real market events only (a bid or ask changed).
- **Deribit's marks, forwards and open interest:** one REST snapshot every 10 seconds. That's enough to validate our IVs and to know each expiry's forward.

The result is about a third of the data, and it's cleaner. **Measure before you design** is a good interview story, and it's now recorded in PLAN.md.

## 2. The files

```
feed/
  schema.py     the columns of every table: the single source of truth
  normalise.py  raw Deribit message -> standard row (pure functions)
  quality.py    per-minute data-quality monitor
  sinks.py      where rows go: screen stats, or recording files
  deribit.py    the handler itself: connection, subscriptions, heartbeats, reconnects, batching
  __main__.py   command line: python -m feed ...
tests/
  test_feed.py  8 tests, using real recorded Deribit messages
  fixtures/deribit_samples.json
```

### schema.py: why a separate schema file?

Every table's columns are defined **once**, with q's own type letters (`p` timestamp, `s` symbol, `f` float, `j` long). In Phase 2, the q table definitions will be **generated from this file**, so Python and q can never disagree about a column. One test checks that every row produced matches its schema exactly.

Every table has an `asset` column. That's the multi-asset preparation from the plan.

There are two timestamps on every market-data row:
- `time`: when Deribit says the event happened.
- `recv`: when we received it.

The difference between them is the latency.

### normalise.py: pure functions

Each function takes one Deribit message and returns rows. No network, no clock: the receive time is passed in. That's what makes the code testable without a live connection.

Two details worth knowing:
- **An empty book side becomes a null.** If an option has no bid, `bid` is `None`, which becomes a q null. We never fill in a fake zero, because a zero bid would look like a real price to every calculation downstream.
- **Times arrive in milliseconds and are stored in nanoseconds.** Nanoseconds is q's native timestamp precision.

### quality.py: data-quality monitor

The monitor sees every quote and trade. Once a minute it emits a `dq` row containing:
- the number of quotes and trades,
- **crossed** quotes (bid ≥ ask, which should never persist),
- **one-sided** quotes (no bid, or no ask),
- how many instruments updated,
- **median and worst latency**.

### sinks.py: where rows go

The handler doesn't know or care where its rows end up. It calls `publish(table, rows)` on whatever sink it was given:
- `StatsSink` prints rows per second for each table.
- `FileSink` records to `~/kdbdata/raw/<date>/<table>.jsonl.gz`. These recordings feed replay mode later.

In Phase 2, a `TickerplantSink` will plug in here without changing anything else. This pattern of programming against an interface rather than a specific implementation is one you'll be asked about in developer interviews.

### deribit.py: the handler

Four jobs run at the same time using `asyncio`: Python's way of handling many waiting operations in one thread.

1. **Connection loop.** Opens the websocket and subscribes in chunks of 100 channels. It then reads messages and routes each one by channel name: `book` becomes a quote, `trades` becomes trades, and so on.
2. **Flush loop.** Every 100 ms, it hands everything buffered to the sink as one batch. q inserts one batch of 300 rows far faster than 300 single rows.
3. **Snapshot loop.** Every 10 seconds, it fetches Deribit's marks, forwards and open interest over REST.
4. **Reference loop.** Hourly, it refreshes the instrument list. It subscribes to newly listed options and unsubscribes from expired ones, so the feed can run for days.

**Resilience, which is what interviewers probe:**
- **Heartbeats.** We ask Deribit to send a heartbeat every 10 seconds and answer each one. If **nothing** arrives for 30 seconds, the connection is treated as dead even though it never closed. Silently dead connections are the most dangerous kind of failure, because everything looks fine while no data arrives.
- **Exponential backoff.** After a failure, it waits 1 s, then 2, 4, 8, … up to 30 s between reconnect attempts. That way it doesn't hammer a server that's struggling.
- **Gap log.** Every outage becomes a `gap` row (start, end, reason), so later analysis knows exactly where data is missing.

## 3. What the tests showed (run in my workspace, 8 Oct 2026)

- **Unit tests:** 8 passed.
- **Live run, 100 seconds:**
  - subscribed to 956 options
  - 130–630 quotes per second (about 270 on average)
  - **0 crossed quotes**
  - about 2–4% of quotes one-sided
- **Kill test:** I closed the connection deliberately after 12 seconds. The feed reconnected within 1.5 seconds and wrote one `gap` row with the correct reason.
- **Recording size:** about 500 KB of compressed quotes per 100 seconds, roughly 0.4 GB a day.

**A note on latency:** my workspace measured a median of about 350 ms, but that includes a proxy. Your machine will show different numbers. Also, `recv - time` compares **your** clock with **Deribit's**, so if your clock is off by 200 ms, every latency figure is off by 200 ms. WSL's clock is known to drift after the laptop sleeps. Phase 2 will add a clock check.

## 4. Run it yourself

In the Ubuntu terminal, from the project folder:

```bash
pytest
```
You should see `8 passed`.

```bash
python -m feed --minutes 2
```
This shows live rates every 5 seconds, plus a `[dq]` line each minute. Press **Ctrl+C** to stop early.

```bash
python -m feed --record --minutes 5
```
This also records to `~/kdbdata/raw/`.

## 5. Questions to think about before Phase 2

1. Look at a `[dq]` line. Why does the **first** minute show fewer quotes than later ones?
2. One-sided quotes cluster in particular options. Using what you saw in lesson 1, which ones would you expect, and why?
3. `snap` produces about 95 rows per second (956 options every 10 seconds), compared with about 270 for `quote`. For the vol surface, do we need Deribit's snapshot every 10 seconds, or would every 60 be enough? What do we lose?
4. If Deribit sends the same trade twice after a reconnect, what would happen to our `trade` table, and which column would let us detect it?
