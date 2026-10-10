"""Table formatting shared by the Dash app and the Streamlit demo (no UI library imported here).

Every table is shown exactly as formatted: numbers become text with the units the panel's note
describes. The one exception is the option chain's "rich" column, which stays a number so each
front end can colour it (rich beyond +1 half-spread, cheap beyond -1).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def fmt(v, f="{:,.0f}", none="–"):
    return none if v is None or (isinstance(v, float) and not math.isfinite(v)) else f.format(v)


def text_frame(df: pd.DataFrame, fmts: dict) -> pd.DataFrame:
    """Every column as text: listed columns with their format, timestamps as HH:MM:SS,
    other floats to 4 significant figures."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    for c in out.columns:
        if c in fmts:
            out[c] = [fmt(x, fmts[c]) for x in out[c]]
        elif pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%H:%M:%S")
        elif pd.api.types.is_float_dtype(out[c]):
            out[c] = [fmt(x, "{:,.4g}") for x in out[c]]
        else:
            out[c] = out[c].astype(str)
    return out.reset_index(drop=True)


CHAIN_FMT = {"strike": "{:,.0f}", "bsize": "{:,.1f}", "bid": "{:.4f}", "ask": "{:.4f}", "asize": "{:,.1f}",
             "bidiv": "{:.2%}", "midiv": "{:.2%}", "askiv": "{:.2%}", "model": "{:.2%}",
             "markiv": "{:.2%}", "delta": "{:+.3f}", "gamma": "{:.2e}", "vega": "{:,.4g}", "theta": "{:,.4g}",
             "oi": "{:,.1f}", "vol": "{:,.1f}"}


def chain_frame(ch: pd.DataFrame) -> pd.DataFrame:
    """The option chain as text, with "rich" kept numeric (rounded) right after "model"."""
    if ch is None or ch.empty:
        return pd.DataFrame()
    out = text_frame(ch.drop(columns=["rich"]), CHAIN_FMT)
    rich = [round(float(x), 2) if np.isfinite(x) else None for x in ch["rich"]]
    out.insert(list(out.columns).index("model") + 1, "rich", rich)
    return out


def fwd_frame(ts: pd.DataFrame) -> pd.DataFrame:
    if ts is None or ts.empty:
        return pd.DataFrame()
    keep = [c for c in ["sym", "days", "F", "basis", "carry", "diff", "D", "r_impl", "r_se", "fsrc", "pairs", "atmvol",
                        "rr25", "bf25"] if c in ts]
    return text_frame(ts[keep], {"days": "{:.1f}", "F": "{:,.0f}", "basis": "{:+.3%}", "carry": "{:+.2%}",
                                 "diff": "{:+,.1f}", "D": "{:.5f}", "r_impl": "{:+.2%}", "r_se": "±{:.2%}",
                                 "pairs": "{:,.0f}", "atmvol": "{:.2%}", "rr25": "{:+.2%}", "bf25": "{:+.2%}"})


def tape_frame(t: pd.DataFrame) -> pd.DataFrame:
    if t is None or t.empty:
        return pd.DataFrame()
    keep = t[["time", "sym", "side", "size", "price", "usd", "iv"]].copy()
    keep["iv"] = keep["iv"] / 100
    return text_frame(keep, {"size": "{:,.1f}", "price": "{:.4f}", "usd": "{:,.2f}", "iv": "{:.2%}"})


def var_frame(v: pd.DataFrame) -> pd.DataFrame:
    if v is None or v.empty:
        return pd.DataFrame()
    last = v[v["time"] == v["time"].max()]
    out = pd.DataFrame({
        "method": last["method"].str.upper(), "VaR 99%": last["var99"], "ES 97.5%": last["es975"],
        "ES 99%": last["es99"], "window (days)": last["window"],
        "backtest": [f"{e} / {d} days (expected {d * 0.01:.1f})" for e, d in zip(last["btexc"], last["btdays"])],
        "Kupiec p": last["kupiec"], "source": last["src"].str.replace("_", " ")})
    return text_frame(out, {"VaR 99%": "{:,.0f}", "ES 97.5%": "{:,.0f}", "ES 99%": "{:,.0f}",
                            "window (days)": "{:,.0f}", "Kupiec p": "{:.2f}"})


POS_FMT = {"qty": "{:+.2f}", "entry": "{:,.2f}", "iv": "{:.2%}", "delta": "{:+.3f}",
           "gamma_1pct": "{:+.3f}", "vega": "{:+,.0f}", "theta": "{:+,.0f}"}
FITS_COLS = ["sym", "days", "n", "fsrc", "rmse", "afrmse", "inband", "afinband", "arbgap", "cold"]
FITS_FMT = {"days": "{:.1f}", "rmse": "{:.2%}", "afrmse": "{:.2%}", "inband": "{:.0%}",
            "afinband": "{:.0%}", "arbgap": "{:.3%}"}

PORT_VARIANT_FMT = {"days": "{:,.0f}", "VaR 99%": "{:,.0f}", "ES 97.5%": "{:,.0f}", "ES 99%": "{:,.0f}",
                    "sum alone ES 97.5%": "{:,.0f}", "diversification (ES)": "{:,.0f}"}
PORT_COIN_FMT = {"ES 97.5% alone": "{:,.0f}", "contribution to ES": "{:,.0f}", "VaR 99% alone": "{:,.0f}",
                 "share of ES": "{:.0%}"}


def port_coin_frame(c: pd.DataFrame) -> pd.DataFrame:
    """The by-coin table, with readable column names, as text."""
    if c is None or c.empty:
        return pd.DataFrame()
    out = c.rename(columns={"asset": "coin", "alone_es975": "ES 97.5% alone", "contrib_es975": "contribution to ES",
                            "alone_var99": "VaR 99% alone", "share": "share of ES"})
    return text_frame(out[["coin", "ES 97.5% alone", "contribution to ES", "share of ES", "VaR 99% alone"]],
                      PORT_COIN_FMT)


def portfolio_tiles(pf: dict) -> list[tuple[str, str, str]]:
    """(label, value, note) for the Portfolio page's headline tiles (FHS, 1 day, USD)."""
    n = len(pf.get("coins", []))
    out = [("Portfolio ES 97.5%", fmt(pf["es975"]), f"USD, 1 day, FHS, {n} coins"),
           ("Portfolio VaR 99%", fmt(pf["var99"]), "USD, 1 day, FHS"),
           ("Sum of coins alone", fmt(pf["sum_es975"]), "ES 97.5%, each book on its own"),
           ("Diversification", fmt(pf["div_es975"]),
            f"{pf['div_es975'] / pf['sum_es975']:.0%} of the sum" if pf.get("sum_es975") else ""),
           ("Stress ES 97.5%", fmt(pf["stress_es975"]), "proxied coins' vol moves ×1.5"),
           ("Backtest", f"{fmt(pf['btexc'])} / {fmt(pf['btdays'])}",
            f"VaR 99% exceptions, Kupiec p = {fmt(pf['kupiec'], '{:.2f}')}")]
    if pf.get("short"):
        out.append(("With " + ", ".join(pf["short"]), fmt(pf["all_es975"]),
                    f"ES 97.5% on the {fmt(pf['all_n'])} days all coins share"))
    return out
