"""Turn raw Deribit messages into rows matching feed/schema.py.

Every function here is *pure*: message in, rows out, no network and no clocks
except the ``recv`` time passed in. That makes them easy to unit-test against
real recorded messages (tests/fixtures/deribit_samples.json).

Rows are plain dicts keyed by column name. Missing values are ``None``, which
becomes a q null later.
"""
from __future__ import annotations

MS = 1_000_000  # nanoseconds per millisecond


def asset_of(sym: str) -> str:
    """'BTC-25DEC26-80000-C' -> 'BTC'."""
    return sym.split("-", 1)[0]


def _top(levels: list) -> tuple[float | None, float | None]:
    """First [price, size] level of a book side, or (None, None) if the side is empty."""
    if levels:
        price, size = levels[0][0], levels[0][1]
        return float(price), float(size)
    return None, None


def book_to_quote(data: dict, recv: int) -> dict:
    """``book.{instrument}.none.1.100ms`` notification -> one ``quote`` row."""
    bid, bsize = _top(data.get("bids") or [])
    ask, asize = _top(data.get("asks") or [])
    sym = data["instrument_name"]
    return {
        "time": data["timestamp"] * MS, "recv": recv, "sym": sym, "asset": asset_of(sym),
        "bid": bid, "bsize": bsize, "ask": ask, "asize": asize,
    }


def trades_to_rows(data: list, recv: int) -> list[dict]:
    """``trades.option.{currency}.100ms`` notification -> ``trade`` rows.

    Deribit batches trades, so one message can hold several.
    ``iv`` arrives in vol points (39.2 = 39.2%) and is kept that way here.
    """
    rows = []
    for t in data:
        sym = t["instrument_name"]
        rows.append({
            "time": t["timestamp"] * MS, "recv": recv, "sym": sym, "asset": asset_of(sym),
            "price": float(t["price"]), "size": float(t["amount"]),
            "side": t["direction"],            # "buy"/"sell" = aggressor side
            "iv": t.get("iv"), "idx": t.get("index_price"),
            "tradeid": str(t["trade_id"]),
        })
    return rows


def index_to_row(data: dict, recv: int) -> dict:
    """``deribit_price_index.{index}`` notification -> one ``index`` row."""
    name = data["index_name"]                   # e.g. "btc_usd"
    return {
        "time": data["timestamp"] * MS, "recv": recv, "sym": name,
        "asset": name.split("_")[0].upper(), "price": float(data["price"]),
    }


def summary_to_rows(result: list, recv: int) -> list[dict]:
    """REST ``public/get_book_summary_by_currency`` result -> ``snap`` rows."""
    rows = []
    for s in result:
        sym = s["instrument_name"]
        rows.append({
            "time": s["creation_timestamp"] * MS, "recv": recv, "sym": sym, "asset": asset_of(sym),
            "mark": s.get("mark_price"), "markiv": s.get("mark_iv"),
            "und": s.get("underlying_price"), "idx": s.get("estimated_delivery_price"),
            "oi": s.get("open_interest"), "vol": s.get("volume"),
        })
    return rows


def instruments_to_ref(result: list) -> list[dict]:
    """REST ``public/get_instruments`` result -> ``ref`` rows."""
    rows = []
    for i in result:
        sym = i["instrument_name"]
        opt = i.get("option_type")
        rows.append({
            "sym": sym, "asset": asset_of(sym), "kind": i["kind"],
            "expiry": i["expiration_timestamp"] * MS,   # 08:00 UTC on expiry day
            "strike": i.get("strike"),
            "cp": {"call": "C", "put": "P"}.get(opt),
            "csize": i.get("contract_size"), "tick": i.get("tick_size"),
            "mintrade": i.get("min_trade_amount"),
            "listed": i["creation_timestamp"] * MS,
        })
    return rows
