"""Table schemas: the single source of truth for every table in the system.

``scripts/gen_schema.py`` turns this file into ``q/tick/sym.q``, the schema file
KX's tickerplant loads, so the Python and q sides can never drift apart.

Type letters are q's own type characters:
    n = timespan   p = timestamp   s = symbol   f = float   j = long   b = boolean

kdb+tick conventions (KX's standard tick.q requires these)
----------------------------------------------------------
- Every table's first two columns are ``time`` (timespan) and ``sym`` (symbol).
- ``time`` is added by the **tickerplant** when the row arrives, as the time of
  day (UTC, because every q process runs with TZ=UTC). The feed never sends it.
- ``sym`` is what subscribers filter on and what the historical database sorts
  and indexes by, so it holds the instrument (or asset, for summary tables).

Our own timestamps, carried as ordinary columns:
- ``exch``  when Deribit says the event happened
- ``recv``  when our feed handler received it
So every market-data row carries three times: exchange -> feed -> tickerplant,
which is how latency is measured at each hop.

Every table carries ``asset`` so ETH and other asset classes can be added
without schema changes (PLAN.md, phase 8).

Symbols vs longs: symbols are stored once in a global ``sym`` file on disk, so
a column with millions of distinct values (like trade ids) must NOT be a
symbol, or that file grows without limit. That is why ``tradeid`` is a long.
"""

TP_COLS = [("time", "n")]          # added by the tickerplant, never sent by the feed

# Columns each publisher (feed handler or surface engine) sends, in order (``sym`` always first).
FEED_SCHEMAS: dict[str, list[tuple[str, str]]] = {
    # Top-of-book quote: one row each time the best bid or ask changes.
    "quote": [
        ("sym", "s"), ("asset", "s"), ("exch", "p"), ("recv", "p"),
        ("bid", "f"), ("bsize", "f"), ("ask", "f"), ("asize", "f"),
    ],
    # Every option trade.
    "trade": [
        ("sym", "s"), ("asset", "s"), ("exch", "p"), ("recv", "p"),
        ("price", "f"), ("size", "f"), ("side", "s"), ("iv", "f"),
        ("idx", "f"), ("tradeid", "j"),
    ],
    # Spot index, about once a second. (Named "spot", not "index", to avoid
    # confusion with q's own vocabulary.)
    "spot": [
        ("sym", "s"), ("asset", "s"), ("exch", "p"), ("recv", "p"), ("price", "f"),
    ],
    # Snapshot of Deribit's own marks, forwards and open interest, every 10 s.
    # Deribit's mark IV is used only to validate our own IVs, never as an input.
    "snap": [
        ("sym", "s"), ("asset", "s"), ("exch", "p"), ("recv", "p"),
        ("mark", "f"), ("markiv", "f"), ("und", "f"), ("idx", "f"),
        ("oi", "f"), ("vol", "f"),
    ],
    # Instrument reference data, sent at start-up and hourly.
    "ref": [
        ("sym", "s"), ("asset", "s"), ("kind", "s"), ("expiry", "p"),
        ("strike", "f"), ("cp", "s"), ("csize", "f"), ("tick", "f"),
        ("mintrade", "f"), ("listed", "p"),
    ],
    # Per-minute data-quality summary (sym = asset).
    "dq": [
        ("sym", "s"), ("asset", "s"), ("minute", "p"), ("quotes", "j"),
        ("trades", "j"), ("crossed", "j"), ("onesided", "j"),
        ("symsupdated", "j"), ("latmed", "f"), ("latmax", "f"),
    ],
    # One row per feed outage (sym = asset).
    "gap": [
        ("sym", "s"), ("asset", "s"), ("start", "p"), ("end", "p"), ("reason", "s"),
    ],
    # ---- published by the surface engine (engine/), Phase 4 ----
    # Implied vols and Greeks, one row per quote update.
    "iv": [
        ("sym", "s"), ("asset", "s"), ("exch", "p"), ("F", "f"), ("fsrc", "s"), ("T", "f"),
        ("bidiv", "f"), ("askiv", "f"), ("midiv", "f"),
        ("delta", "f"), ("gamma", "f"), ("vega", "f"), ("theta", "f"),
    ],
    # Forward per expiry: our put-call-parity regression vs Deribit's (sym = expiry, e.g. BTC-25DEC26).
    "fwd": [
        ("sym", "s"), ("asset", "s"), ("expiry", "p"), ("F", "f"), ("D", "f"), ("seF", "f"),
        ("seD", "f"), ("n", "j"), ("und", "f"), ("undage", "f"), ("diff", "f"),
        ("Fused", "f"), ("fsrc", "s"),
    ],
    # Fitted smile per expiry: raw SVI and arbitrage-free SVI side by side, plus desk metrics.
    "surface": [
        ("sym", "s"), ("asset", "s"), ("expiry", "p"), ("T", "f"), ("F", "f"), ("fsrc", "s"),
        ("n", "j"),
        ("a", "f"), ("b", "f"), ("rho", "f"), ("m", "f"), ("sigma", "f"),
        ("rmse", "f"), ("wrmse", "f"), ("inband", "f"), ("ming", "f"), ("calv", "f"),
        ("afa", "f"), ("afb", "f"), ("afrho", "f"), ("afm", "f"), ("afsigma", "f"),
        ("afrmse", "f"), ("afinband", "f"), ("afming", "f"), ("afcal", "f"), ("arbgap", "f"),
        ("atmvol", "f"), ("rr25", "f"), ("bf25", "f"), ("cold", "b"),
    ],
    # ---- published by the risk process (risk/), Phase 5. sym = book name ----
    # Positions, one row per instrument held (republished at each start of day, so every HDB
    # date holds the book it was risk-managed with). Futures: sym = expiry label, strike null.
    "pos": [
        ("sym", "s"), ("asset", "s"), ("book", "s"), ("kind", "s"), ("expiry", "p"),
        ("strike", "f"), ("cp", "s"), ("qty", "f"), ("entry", "f"),
    ],
    # Risk by bucket: kind = total | expiry | delta; bucket = ALL, an expiry label, or 10P..10C.
    # mtm = mark-to-market value (USD); "value" is a q keyword, so it is not used as a name.
    # Units in risk/core.py: USD, BTC, per 1% spot move, per vol point, per day.
    "risk": [
        ("sym", "s"), ("asset", "s"), ("kind", "s"), ("bucket", "s"), ("R", "f"),
        ("mtm", "f"), ("delta", "f"), ("deltaR", "f"), ("deltapa", "f"), ("deltaspot", "f"),
        ("cashdelta", "f"), ("gamma", "f"), ("cashgamma", "f"), ("vega", "f"), ("theta", "f"),
        ("vanna", "f"), ("volga", "f"), ("vatm", "f"), ("vrr", "f"), ("vbf", "f"),
    ],
    # Scenario grid: full-revaluation P&L for spot moves (dspot, fraction) x vol shifts (dvol, points).
    "scen": [
        ("sym", "s"), ("asset", "s"), ("R", "f"), ("dspot", "f"), ("dvol", "f"), ("pnl", "f"),
    ],
    # P&L explain per interval [start, end]: Taylor terms, the unexplained rest, and the vega
    # term split into "smile" (predicted by the smile rule from the price move) and "surf"
    # (the surface itself re-marked).
    "pnl": [
        ("sym", "s"), ("asset", "s"), ("start", "p"), ("end", "p"), ("R", "f"),
        ("actual", "f"), ("delta", "f"), ("gamma", "f"), ("vega", "f"), ("theta", "f"),
        ("vanna", "f"), ("volga", "f"), ("unexpl", "f"), ("smile", "f"), ("surf", "f"),
    ],
    # VaR / ES (USD losses), per method (hs | fhs), with the hypothetical-P&L backtest of VaR 99%.
    # (Named vares, and columns wstart/wend, because var and last are q keywords.)
    "vares": [
        ("sym", "s"), ("asset", "s"), ("method", "s"), ("R", "f"), ("window", "j"), ("src", "s"),
        ("wstart", "p"), ("wend", "p"), ("var99", "f"), ("es975", "f"), ("es99", "f"),
        ("btdays", "j"), ("btexc", "j"), ("kupiec", "f"),
    ],
    # Portfolio VaR / ES across every coin's book (risk/portfolio.py), one number per row:
    # sym = scope: joint (the coins with a full window), stress (proxied coins' vol moves x1.5),
    # allcoins (every coin, on the days they all share), corr (correlation of daily moves).
    # asset = a coin, or ALL for portfolio totals; asset2 = the other coin of a correlation pair.
    # metric: var99 es975 es99 n sum_var99 sum_es975 div_var99 div_es975 (asset ALL);
    # alone_var99 alone_es975 contrib_es975 (per coin); btdays btexc kupiec (joint backtest); corr.
    # ("val", because value is a q keyword.)
    "port": [
        ("sym", "s"), ("asset", "s"), ("asset2", "s"), ("method", "s"), ("metric", "s"),
        ("R", "f"), ("val", "f"),
    ],
}

# Full q schemas, as stored in the tickerplant, real-time and historical DBs.
SCHEMAS = {t: TP_COLS + cols for t, cols in FEED_SCHEMAS.items()}


def columns(table: str) -> list[str]:
    """Columns the feed sends for a table, in order."""
    return [c for c, _ in FEED_SCHEMAS[table]]
