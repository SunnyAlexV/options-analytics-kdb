"""Rows for the risk tables, exactly as published (schemas: feed/schema.py). One function per
table, shared by the live process and the schema test, so what is tested is what is sent."""
from __future__ import annotations

import pandas as pd

from feed.schema import columns


def _ordered(table: str, rows: list[dict]) -> list[dict]:
    """Exactly the table's columns, in schema order (extra keys dropped, a missing one raises)."""
    cols = columns(table)
    return [{c: r[c] for c in cols} for r in rows]


def pos_rows(book: list[dict], asset: str) -> list[dict]:
    return _ordered("pos", [{"sym": r["sym"], "asset": asset, "book": r["book"], "kind": r["kind"],
                             "expiry": int(r["expiry"]), "strike": float(r["strike"]), "cp": r["cp"],
                             "qty": float(r["qty"]), "entry": float(r["entry"])} for r in book])


def risk_rows(agg: pd.DataFrame, asset: str) -> list[dict]:
    """sym = book name."""
    return _ordered("risk", [dict(r, sym=r.pop("book"), asset=asset) for r in agg.to_dict("records")])


def scen_rows(grid: pd.DataFrame, book: str, asset: str) -> list[dict]:
    return _ordered("scen", [dict(r, sym=book, asset=asset) for r in grid.to_dict("records")])


def pnl_q_rows(pnl: pd.DataFrame, asset: str) -> list[dict]:
    return _ordered("pnl", [dict(r, sym=r.pop("book"), asset=asset, start=int(r["start"]), end=int(r["end"]))
                            for r in pnl.to_dict("records")])


def vares_rows(res: pd.DataFrame, bt: dict, book: str, asset: str, R: float) -> list[dict]:
    out = []
    for r in res.to_dict("records"):
        b = bt[r["method"]]
        out.append({"sym": book, "asset": asset, "method": r["method"], "R": float(R),
                    "window": int(r["window"]), "src": r["src"].replace(" ", "_"),
                    "wstart": int(pd.Timestamp(r["first"]).value), "wend": int(pd.Timestamp(r["last"]).value),
                    "var99": float(r["var99"]), "es975": float(r["es975"]), "es99": float(r["es99"]),
                    "btdays": int(b["days"]), "btexc": int(b["exceptions"]), "kupiec": float(b["kupiec_p"])})
    return _ordered("vares", out)
