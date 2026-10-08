"""Table schemas: the single source of truth for every table the feed produces.

Each table is an ordered list of (column, type) pairs. The type letters are q's
own type characters, so Phase 2 can generate the q table definitions directly
from this file and the Python and q sides can never drift apart:

    p = timestamp   s = symbol   f = float   j = long   d = date   b = boolean

Time conventions
----------------
- ``time``  exchange timestamp (when Deribit says the event happened), UTC
- ``recv``  when our feed handler received the message, UTC
Both are stored in Python as integer nanoseconds since 1970-01-01 UTC.
``recv - time`` is the network + exchange latency, which the data-quality
table reports.

Every market-data table carries ``asset`` so later phases can add ETH and
other asset classes without changing any schema (PLAN.md, phase 8).
"""

SCHEMAS: dict[str, list[tuple[str, str]]] = {
    # Top-of-book quote: one row each time the best bid or ask changes.
    "quote": [
        ("time", "p"), ("recv", "p"), ("sym", "s"), ("asset", "s"),
        ("bid", "f"), ("bsize", "f"), ("ask", "f"), ("asize", "f"),
    ],
    # Every option trade.
    "trade": [
        ("time", "p"), ("recv", "p"), ("sym", "s"), ("asset", "s"),
        ("price", "f"), ("size", "f"), ("side", "s"), ("iv", "f"),
        ("idx", "f"), ("tradeid", "s"),
    ],
    # Spot index, about once a second.
    "index": [
        ("time", "p"), ("recv", "p"), ("sym", "s"), ("asset", "s"), ("price", "f"),
    ],
    # Snapshot of Deribit's own marks, forwards and open interest, every few seconds.
    # Deribit's mark IV is used only to validate our own IVs, never as an input.
    "snap": [
        ("time", "p"), ("recv", "p"), ("sym", "s"), ("asset", "s"),
        ("mark", "f"), ("markiv", "f"), ("und", "f"), ("idx", "f"),
        ("oi", "f"), ("vol", "f"),
    ],
    # Instrument reference data, refreshed at start-up and hourly.
    "ref": [
        ("sym", "s"), ("asset", "s"), ("kind", "s"), ("expiry", "p"),
        ("strike", "f"), ("cp", "s"), ("csize", "f"), ("tick", "f"),
        ("mintrade", "f"), ("listed", "p"),
    ],
    # Per-minute data-quality summary.
    "dq": [
        ("time", "p"), ("asset", "s"), ("quotes", "j"), ("trades", "j"),
        ("crossed", "j"), ("onesided", "j"), ("symsupdated", "j"),
        ("latmed", "f"), ("latmax", "f"),
    ],
    # One row per feed outage (disconnect, silent connection, ...).
    "gap": [
        ("start", "p"), ("end", "p"), ("asset", "s"), ("reason", "s"),
    ],
}


def columns(table: str) -> list[str]:
    """Column names of a table, in order."""
    return [c for c, _ in SCHEMAS[table]]
