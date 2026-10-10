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


def port_rows(rep: dict, bt: dict) -> list[dict]:
    """The portfolio report (risk/portfolio.compute) and its backtest as long rows."""
    R = float(rep["R"])
    out = []

    def add(scope, method, metric, val, asset="ALL", asset2=""):
        out.append({"sym": scope, "asset": asset, "asset2": asset2, "method": method,
                    "metric": metric, "R": R, "val": float(val)})

    for method, res in rep["methods"].items():
        for scope, s in res.items():
            for k in ("var99", "es975", "es99"):
                add(scope, method, k, s["joint"][k])
            add(scope, method, "n", s["n"])
            for k in ("sum_var99", "sum_es975", "div_var99", "div_es975"):
                add(scope, method, k, s[k])
            for a, x in s["alone"].items():
                add(scope, method, "alone_var99", x["var99"], a)
                add(scope, method, "alone_es975", x["es975"], a)
                add(scope, method, "contrib_es975", s["contrib_es975"][a], a)
        if method in bt:
            b = bt[method]
            add("joint", method, "btdays", b["days"])
            add("joint", method, "btexc", b["exceptions"])
            add("joint", method, "kupiec", b["kupiec_p"])
    if "corr" in rep:
        c = rep["corr"]
        for a in c.index:
            for b in c.columns:
                add("corr", "", "corr", c.loc[a, b], a, b)
    return _ordered("port", out)
