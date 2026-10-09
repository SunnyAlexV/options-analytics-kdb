"""Plotly figures for the dashboard. Every function takes analytics output and returns a figure;
with no data it returns an empty figure that says so, never an error.

Conventions: vols are shown in vol points (%), money in USD; one y-axis per chart (two measures
of different scale go in separate charts); a legend whenever there are two or more series.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from . import theme as th


def empty(title: str, msg: str = "waiting for data") -> go.Figure:
    f = go.Figure()
    f.update_layout(title=title, xaxis=dict(visible=False), yaxis=dict(visible=False), height=300,
                    annotations=[dict(text=msg, showarrow=False, font=dict(color=th.MUTED, size=13),
                                      xref="paper", yref="paper", x=0.5, y=0.5)])
    return f


def _h(f, height=340, **kw):
    f.update_layout(height=height, **kw)
    return f


# ------------------------------------------------------------------ market
def smile(sm: dict) -> go.Figure:
    title = f"Smile · {sm.get('expiry', '')}"
    if not sm or sm["points"].empty:
        return empty(title)
    p = sm["points"]
    f = go.Figure()
    mid = p["midiv"] * 100
    f.add_trace(go.Scatter(
        x=p["k"], y=mid, mode="markers", name="market mid (bid–ask bars)",
        marker=dict(color=th.BLUE, size=7, line=dict(color=th.SURFACE, width=2)),
        error_y=dict(type="data", symmetric=False, array=(p["askiv"] - p["midiv"]) * 100,
                     arrayminus=(p["midiv"] - p["bidiv"]) * 100, color=th.BLUE, thickness=1, width=0),
        customdata=np.c_[p["strike"], p["cp"], p["bidiv"] * 100, p["askiv"] * 100],
        hovertemplate="K %{customdata[0]:,.0f} %{customdata[1]}<br>bid %{customdata[2]:.2f} · "
                      "mid %{y:.2f} · ask %{customdata[3]:.2f}<extra></extra>"))
    m = sm["marks"]
    if len(m):
        f.add_trace(go.Scatter(x=m["k"], y=m["markiv"], mode="markers", name="Deribit mark",
                               marker=dict(color=th.YELLOW, size=6, symbol="diamond-open"),
                               hovertemplate="mark IV %{y:.2f}<extra></extra>"))
    f.add_trace(go.Scatter(x=sm["k"], y=sm["raw"] * 100, mode="lines", name="SVI raw",
                           line=dict(color=th.ORANGE, width=2, dash="dot")))
    f.add_trace(go.Scatter(x=sm["k"], y=sm["af"] * 100, mode="lines", name="SVI arbitrage-free",
                           line=dict(color=th.AQUA, width=2)))
    f.add_vline(x=0, line=dict(color=th.AXIS, width=1))
    return _h(f, 380, title=f"{title} · F {sm['F']:,.0f} · {sm['T'] * 365:.1f} days",
              xaxis_title="log-moneyness ln(K/F)", yaxis_title="implied vol (%)")


def density(sm: dict) -> go.Figure:
    if not sm:
        return empty("Risk-neutral density")
    K = sm["F"] * np.exp(sm["k"])
    # density of the price at expiry, per 1% of the forward: comparable across coins of any price
    per_pct = sm["density"] / K * sm["F"] * 0.01
    under = sm["expiry"].rsplit("-", 1)[0].replace("_USDC", "")
    f = go.Figure(go.Scatter(x=K, y=per_pct * 100, mode="lines", fill="tozeroy", name="density",
                             line=dict(color=th.AQUA, width=2), fillcolor="rgba(25,158,112,0.15)",
                             hovertemplate=under + " %{x:,.4g}<br>%{y:.2f}% per 1% band<extra></extra>"))
    f.add_vline(x=sm["F"], line=dict(color=th.AXIS, width=1))
    return _h(f, 380, title=f"Implied distribution of {under} at {sm['expiry']}",
              xaxis_title=f"{under} price at expiry (USD)", yaxis_title="probability per 1% of the forward (%)",
              showlegend=False)


def surface3d(g: dict) -> go.Figure:
    if len(g["days"]) < 2:
        return empty("Vol surface (arbitrage-free SVI)")
    f = go.Figure(go.Surface(x=g["z"], y=g["days"], z=g["vol"] * 100, colorscale=th.SEQUENTIAL,
                             colorbar=dict(title=dict(text="vol %"), thickness=10, tickfont=dict(color=th.MUTED)),
                             hovertemplate="%{x:+.1f} ATM std devs<br>%{y:.0f} days<br>%{z:.1f}%<extra></extra>"))
    ax = dict(gridcolor=th.GRID, backgroundcolor=th.SURFACE, color=th.MUTED, showbackground=True)
    f.update_layout(scene=dict(xaxis=dict(title="ln(K/F) / (σ_ATM √T)", **ax), yaxis=dict(title="days", **ax),
                               zaxis=dict(title="vol %", **ax), camera=dict(eye=dict(x=1.6, y=-1.6, z=0.8))),
                    margin=dict(l=0, r=0, t=48, b=0))
    return _h(f, 440, title="Vol surface (arbitrage-free SVI), in ATM standard deviations")


DAY_TICKS = dict(tickvals=[2, 7, 14, 30, 60, 90, 180, 365], ticktext=["2", "7", "14", "30", "60", "90", "180", "365"])


def term_structure(ts: pd.DataFrame, atm30: float) -> go.Figure:
    if ts.empty:
        return empty("ATM term structure")
    f = go.Figure(go.Scatter(x=ts["days"], y=ts["atmvol"] * 100, mode="lines+markers", name="ATM vol",
                             line=dict(color=th.BLUE, width=2), marker=dict(size=8),
                             customdata=ts["sym"], hovertemplate="%{customdata}<br>%{y:.2f}%<extra></extra>"))
    if np.isfinite(atm30):
        f.add_trace(go.Scatter(x=[30], y=[atm30 * 100], mode="markers", name="30-day constant maturity",
                               marker=dict(color=th.YELLOW, size=11, symbol="diamond")))
    return _h(f, 300, title="ATM vol by expiry", xaxis_title="days to expiry (log scale)", yaxis_title="vol (%)",
              xaxis=dict(type="log", **DAY_TICKS))


def skew_terms(ts: pd.DataFrame) -> go.Figure:
    if ts.empty:
        return empty("Skew and wings by expiry")
    f = go.Figure()
    f.add_trace(go.Scatter(x=ts["days"], y=ts["rr25"] * 100, mode="lines+markers", name="25Δ risk reversal",
                           line=dict(color=th.ORANGE, width=2)))
    f.add_trace(go.Scatter(x=ts["days"], y=ts["bf25"] * 100, mode="lines+markers", name="25Δ butterfly",
                           line=dict(color=th.AQUA, width=2)))
    f.add_hline(y=0, line=dict(color=th.AXIS, width=1))
    return _h(f, 300, title="Skew (RR) and wings (BF) by expiry", xaxis_title="days to expiry (log scale)",
              yaxis_title="vol points", xaxis=dict(type="log", **DAY_TICKS))


def carry(ts: pd.DataFrame) -> go.Figure:
    if ts.empty:
        return empty("Forward carry")
    f = go.Figure(go.Bar(x=[s.split("-", 1)[-1] for s in ts["sym"]], y=ts["carry"] * 100, name="carry",
                         marker=dict(color=th.BLUE),
                         hovertemplate="%{x}<br>%{y:.2f}% a year<extra></extra>"))
    return _h(f, 300, title="Futures carry implied by forwards (annualised)", yaxis_title="% per year",
              showlegend=False)


def series(df: pd.DataFrame, col: str, title: str, ytitle: str, scale=1.0, color=th.BLUE, height=240) -> go.Figure:
    if df is None or df.empty or col not in df:
        return empty(title)
    f = go.Figure(go.Scatter(x=df["time"], y=df[col] * scale, mode="lines", line=dict(color=color, width=2),
                             name=col))
    return _h(f, height, title=title, yaxis_title=ytitle, showlegend=False)


def rr_bf_history(h: pd.DataFrame, expiry: str) -> go.Figure:
    if h.empty:
        return empty(f"Skew and wings over time · {expiry}")
    f = go.Figure()
    f.add_trace(go.Scatter(x=h["time"], y=h["rr25"] * 100, mode="lines", name="25Δ RR", line=dict(color=th.ORANGE, width=2)))
    f.add_trace(go.Scatter(x=h["time"], y=h["bf25"] * 100, mode="lines", name="25Δ BF", line=dict(color=th.AQUA, width=2)))
    return _h(f, 260, title=f"Skew and wings over time · {expiry}", yaxis_title="vol points")


def realised_vs_implied(rv: pd.DataFrame, atm: pd.DataFrame) -> go.Figure:
    if (rv is None or rv["rv"].notna().sum() == 0) and (atm is None or atm.empty):
        return empty("Realised vs implied, today")
    f = go.Figure()
    if atm is not None and len(atm):
        f.add_trace(go.Scatter(x=atm["time"], y=atm["atm30"] * 100, mode="lines", name="implied: ATM 30-day",
                               line=dict(color=th.BLUE, width=2)))
    if rv is not None and rv["rv"].notna().any():
        f.add_trace(go.Scatter(x=rv["time"], y=rv["rv"] * 100, mode="lines", name="realised: 30-min, annualised",
                               line=dict(color=th.ORANGE, width=2)))
    return _h(f, 260, title="Realised vs implied vol, today", yaxis_title="vol (%)")


def vrp(v: pd.DataFrame) -> go.Figure:
    if v is None or v.empty:
        return empty("Variance risk premium, 5 years", "history unavailable (needs internet once)")
    f = go.Figure()
    f.add_trace(go.Scatter(x=v["date"], y=v["dvol"] * 100, mode="lines", name="DVOL (implied, next 30 days)",
                           line=dict(color=th.BLUE, width=1.5)))
    f.add_trace(go.Scatter(x=v["date"], y=v["rv_next"] * 100, mode="lines", name="realised over the next 30 days",
                           line=dict(color=th.ORANGE, width=1.5)))
    prem = v["vrp"].dropna()
    sub = (f"implied minus the realised that followed: mean {prem.mean() * 100:+.1f} pts, "
           f"positive {np.mean(prem > 0):.0%} of days") if len(prem) else ""
    return _h(f, 320, title=f"Variance risk premium, BTC, 5 years<br><sup>{sub}</sup>", yaxis_title="vol (%)",
              margin=dict(t=96))


def open_interest(oi: pd.DataFrame, expiry: str) -> go.Figure:
    title = f"Open interest by strike · {expiry}"
    if oi is None or oi.empty:
        return empty(title)
    f = go.Figure()
    for cp, c, n in (("C", th.BLUE, "calls"), ("P", th.ORANGE, "puts")):
        if cp in oi:
            f.add_trace(go.Bar(x=oi["strike"], y=oi[cp], name=n, marker=dict(color=c)))
    return _h(f, 320, title=title, barmode="group", bargap=0.2, xaxis_title="strike", yaxis_title="contracts")


def traded_by_strike(by: pd.DataFrame) -> go.Figure:
    title = "Traded today by strike, all expiries"
    if by is None or by.empty:
        return empty(title, "no trades yet")
    f = go.Figure()
    for cp, c, n in (("C", th.BLUE, "calls"), ("P", th.ORANGE, "puts")):
        if cp in by:
            f.add_trace(go.Bar(x=by["strike"], y=by[cp], name=n, marker=dict(color=c)))
    return _h(f, 320, title=title, barmode="group", bargap=0.2, xaxis_title="strike", yaxis_title="contracts")


# ------------------------------------------------------------------ risk
def scenario_heatmap(m: pd.DataFrame, R: float) -> go.Figure:
    if m is None or m.empty:
        return empty("Scenario P&L")
    z = m.to_numpy()
    lim = float(np.nanmax(np.abs(z))) or 1.0
    f = go.Figure(go.Heatmap(
        z=z, x=[f"{c:+.0%}" for c in m.columns], y=[f"{r:+.0f} pts" for r in m.index],
        colorscale=th.DIVERGING, zmin=-lim, zmax=lim, zmid=0, xgap=2, ygap=2,
        text=[[f"{v / 1e3:+,.0f}k" for v in row] for row in z], texttemplate="%{text}",
        textfont=dict(size=10, color=th.INK),
        colorbar=dict(title=dict(text="USD"), thickness=10, tickfont=dict(color=th.MUTED)),
        hovertemplate="BTC %{x}, vol %{y} pts<br>P&L %{z:,.0f} USD<extra></extra>"))
    return _h(f, 380, title=f"Scenario P&L, full revaluation (smile rule R = {R:g})",
              xaxis=dict(title="BTC move", type="category"), yaxis=dict(title="vol shift", type="category"),
              margin=dict(l=96))


def waterfall(w: pd.DataFrame) -> go.Figure:
    if w is None or w.empty:
        return empty("P&L explain")
    f = go.Figure(go.Waterfall(
        x=list(w["term"]) + ["Actual"], y=list(w["usd"]) + [w["actual"].iloc[0]],
        measure=["relative"] * len(w) + ["total"],
        increasing=dict(marker=dict(color=th.BLUE)), decreasing=dict(marker=dict(color="#e66767")),
        totals=dict(marker=dict(color=th.VIOLET)), connector=dict(line=dict(color=th.AXIS, width=1)),
        hovertemplate="%{x}: %{y:,.2f} USD<extra></extra>"))
    return _h(f, 380, title="P&L explain, cumulative", yaxis_title="USD", showlegend=False)


def bars(df: pd.DataFrame, x: str, y: str, title: str, ytitle: str, color=th.BLUE) -> go.Figure:
    if df is None or df.empty:
        return empty(title)
    f = go.Figure(go.Bar(x=df[x], y=df[y], marker=dict(color=color),
                         hovertemplate="%{x}: %{y:,.0f}<extra></extra>"))
    f.add_hline(y=0, line=dict(color=th.AXIS, width=1))
    return _h(f, 280, title=title, yaxis_title=ytitle, showlegend=False, bargap=0.3)


def pnl_path(p: pd.DataFrame) -> go.Figure:
    if p is None or p.empty:
        return empty("P&L: actual vs explained")
    f = go.Figure()
    f.add_trace(go.Scatter(x=p["time"], y=p["actual"], mode="lines", name="actual", line=dict(color=th.BLUE, width=2)))
    f.add_trace(go.Scatter(x=p["time"], y=p["explained"], mode="lines", name="explained by Greeks",
                           line=dict(color=th.ORANGE, width=2, dash="dot")))
    return _h(f, 280, title="Cumulative P&L: actual vs explained", yaxis_title="USD")


# ------------------------------------------------------------------ system
def dq_quotes(dq: pd.DataFrame) -> go.Figure:
    if dq is None or dq.empty:
        return empty("Quote updates per minute")
    t = pd.to_datetime(dq["minute"])
    return _h(go.Figure(go.Bar(x=t, y=dq["quotes"], marker=dict(color=th.BLUE), name="quotes")), 260,
              title="Quote updates per minute", yaxis_title="updates", showlegend=False)


def latency(dq: pd.DataFrame, lat: pd.DataFrame) -> go.Figure:
    if (dq is None or dq.empty) and (lat is None or lat.empty):
        return empty("Latency")
    f = go.Figure()
    if dq is not None and len(dq):
        t = pd.to_datetime(dq["minute"])
        f.add_trace(go.Scatter(x=t, y=dq["latmed"], mode="lines", name="exchange → feed, median", line=dict(color=th.BLUE, width=2)))
        f.add_trace(go.Scatter(x=t, y=dq["latmax"], mode="lines", name="exchange → feed, max", line=dict(color=th.ORANGE, width=1.5)))
    if lat is not None and len(lat):
        f.add_trace(go.Scatter(x=lat["time"], y=lat["ms"], mode="lines", name="feed → tickerplant, median",
                               line=dict(color=th.AQUA, width=2)))
    return _h(f, 260, title="Latency per minute", yaxis_title="ms", yaxis_rangemode="tozero")
