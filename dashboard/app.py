"""The Dash app: three pages (Market, Risk, System) over one data source.

Layout is static: every chart has a fixed id and callbacks only replace its figure, so zoom and
the 3D camera survive the 2-second refresh (``uirevision``). Each page's callback runs only while
that page is showing, and asks the source for just the views it needs.
"""
from __future__ import annotations

import math

import dash
import numpy as np
import pandas as pd
from dash import Input, Output, dash_table, dcc, html
from dash.dash_table.Format import Format, Scheme, Sign

from . import analytics as A
from . import figures as FG
from . import theme as th

MARKET_VIEWS = ["latest_surface", "latest_iv", "latest_ref", "latest_snap", "latest_quote", "latest_fwd",
                "spot1m", "surf1m", "trades"]
RISK_VIEWS = ["risk_last", "risk1m", "scen_last", "pnl", "vares", "pos", "latest_iv"]
SYSTEM_VIEWS = ["dq", "gap", "lat1m", "counts", "latest_surface"]


def _fmt(v, f="{:,.0f}", none="–"):
    return none if v is None or (isinstance(v, float) and not math.isfinite(v)) else f.format(v)


def tile(label, value, sub=""):
    return html.Div([html.Div(label, className="lbl"), html.Div(value, className="val"),
                     html.Div(sub, className="sub")], className="tile")


def card(*children, title=None, note=None):
    head = ([html.H3(title)] if title else []) + ([html.Div(note, className="note")] if note else [])
    return html.Div(head + list(children), className="card")


def graph(id_):
    return dcc.Graph(id=id_, config={"displaylogo": False, "responsive": True})


def table(id_, page_size=None):
    kw = {"page_size": page_size} if page_size else {"page_action": "none"}
    return dash_table.DataTable(id=id_, **th.TABLE_STYLE, **kw, sort_action="native")


def create_app(source, history=None) -> dash.Dash:
    """source: dashboard.sources.GatewaySource or ReplaySource; history: daily index/DVOL frame."""
    app = dash.Dash(__name__, title="BTC options desk", update_title=None)
    vrp = A.vrp_history(history) if history is not None else pd.DataFrame()

    market = html.Div([
        html.Div(id="m-tiles", className="tiles"),
        html.Div([html.Span("Expiry"), dcc.Dropdown(id="m-expiry", clearable=False, style={"width": "220px"})],
                 className="ctrl"),
        html.Div([card(graph("m-smile")), card(graph("m-density"))], className="grid g2"),
        html.Div([card(graph("m-surface")),
                  html.Div([card(graph("m-term")), card(graph("m-skew"))], className="grid")],
                 className="grid g11"),
        html.Div([card(graph("m-rrbf")), card(graph("m-rv")), card(graph("m-carry"))], className="grid g3"),
        card(table("m-chain"), title="Option chain",
             note="IVs in %. rich = (mid IV − fitted IV) / half-spread: above +1 the mid is rich to the "
                  "arbitrage-free smile by more than half the spread, below −1 cheap. mark = Deribit's own mark IV."),
        html.Div([card(table("m-fwd"), title="Forwards and fits by expiry",
                       note="basis and carry vs the spot index; parity F − Deribit F in USD; D = BTC discount "
                            "factor from parity; r_btc = the BTC rate it implies, r_se its standard error (on short "
                            "expiries a tiny error in D is a large rate error); fit quality in vol points."),
                  card(graph("m-vrp"))], className="grid g11"),
        html.Div([card(graph("m-oi")), card(graph("m-volume"))], className="grid g11"),
        card(table("m-tape"), title="Trade tape (latest 60)", note="price in BTC per contract; usd = price × index"),
    ])
    risk = html.Div([
        html.Div(id="r-tiles", className="tiles"),
        html.Div([card(graph("r-scen")), card(graph("r-waterfall"))], className="grid g11"),
        html.Div([card(graph("r-vega-exp")), card(graph("r-vega-delta")), card(graph("r-factors"))],
                 className="grid g3"),
        html.Div([card(graph("r-delta-t")), card(graph("r-vega-t")), card(graph("r-gamma-t")), card(graph("r-pnl-t"))],
                 className="grid g4"),
        html.Div([card(table("r-pos"), title="Positions, live Greeks",
                       note="delta in BTC, gamma as BTC of delta per 1% move, vega USD per vol point, theta USD per day"),
                  card(table("r-var"), title="VaR and Expected Shortfall (USD, 1 day)",
                       note="HS: last 365 days equally weighted. FHS: each day rescaled to today's volatility "
                            "(EWMA 0.94). Backtest: exceptions of VaR 99% over the past year with Kupiec's p-value "
                            "(below 0.05 = the method is rejected).")], className="grid g11"),
    ])
    system = html.Div([
        html.Div(id="s-tiles", className="tiles"),
        html.Div([card(graph("s-quotes")), card(graph("s-latency"))], className="grid g11"),
        html.Div([card(table("s-fits"), title="Smile fits by expiry",
                       note="n quotes used; RMSE in vol points; inband = share of quotes whose fitted vol lies "
                            "inside their bid–ask; arbgap = extra RMSE paid to remove arbitrage"),
                  card(table("s-counts"), title="Rows per table today"),
                  card(table("s-gaps"), title="Feed outages")], className="grid g211"),
    ])

    app.layout = html.Div([
        dcc.Interval(id="tick", interval=2000),
        html.Div([html.H1("BTC options desk"), html.Span(source.name, className="pill"),
                  html.Span(id="clock", className="meta"), html.Span(id="errors", className="warn")],
                 className="top"),
        dcc.Tabs(id="tabs", value="market", children=[
            dcc.Tab(market, label="Market", value="market", className="tab", selected_className="tab--selected"),
            dcc.Tab(risk, label="Risk", value="risk", className="tab", selected_className="tab--selected"),
            dcc.Tab(system, label="System", value="system", className="tab", selected_className="tab--selected"),
        ]),
    ], className="wrap")
    app.index_string = app.index_string.replace("</head>", f"<style>{th.CSS}</style></head>")

    def keep(fig):
        fig.update_layout(uirevision="keep")
        return fig

    # ------------------------------------------------------------ market
    @app.callback(
        [Output("m-tiles", "children"), Output("m-expiry", "options"), Output("m-expiry", "value"),
         Output("m-smile", "figure"), Output("m-density", "figure"), Output("m-surface", "figure"),
         Output("m-term", "figure"), Output("m-skew", "figure"), Output("m-rrbf", "figure"),
         Output("m-rv", "figure"), Output("m-carry", "figure"),
         Output("m-chain", "data"), Output("m-chain", "columns"), Output("m-chain", "style_data_conditional"),
         Output("m-fwd", "data"), Output("m-fwd", "columns"), Output("m-vrp", "figure"),
         Output("m-oi", "figure"), Output("m-volume", "figure"), Output("m-tape", "data"), Output("m-tape", "columns"),
         Output("clock", "children"), Output("errors", "children")],
        [Input("tick", "n_intervals"), Input("tabs", "value"), Input("m-expiry", "value")])
    def market_page(_, tab, expiry):
        if tab != "market":
            return [dash.no_update] * 21 + [_clock(source), dash.no_update]
        now = source.now()
        v = source.views(MARKET_VIEWS)
        ex = A.expiries(v["latest_surface"], now)
        opts = [{"label": f"{s}  ({d:.0f}d)", "value": s} for s, d in zip(ex["sym"], ex["days"])]
        if expiry not in set(ex["sym"]):                      # default: the expiry nearest 30 days
            expiry = ex.iloc[(ex["days"] - 30).abs().argmin()]["sym"] if len(ex) else None
        h = A.headline(v, now)
        flow = A.trade_flow(v)
        tiles = [tile("BTC index", _fmt(h["spot"]), f"{h['spot_chg']:+.2%} in view" if math.isfinite(h["spot_chg"]) else ""),
                 tile("ATM vol 30d", _fmt(h["atm30"] * 100, "{:.2f}%"), "constant maturity, our fits"),
                 tile("Front RR 25Δ", _fmt(h["rr25"] * 100, "{:+.2f}"), h["front"]),
                 tile("Front BF 25Δ", _fmt(h["bf25"] * 100, "{:+.2f}"), h["front"]),
                 tile("Put/call volume", _fmt(flow["pc_ratio"], "{:.2f}"), "traded BTC, puts ÷ calls"),
                 tile("Buyer-initiated", _fmt(flow["buy_share"], "{:.0%}"), "share of traded BTC"),
                 tile("Expiries fitted", str(h["n_exp"]), "arbitrage-free SVI")]
        sm = A.smile(v, expiry, now) if expiry else {}
        ts = A.term_structure(v, now)
        ch = A.chain(v, expiry, now) if expiry else pd.DataFrame()
        ch_cols, ch_data, ch_style = _chain_table(ch)
        fw_cols, fw_data = _fwd_table(ts)
        tape_cols, tape_data = _tape_table(flow["tape"])
        oi = A.open_interest(v, expiry)
        return [tiles, opts, expiry,
                keep(FG.smile(sm)), keep(FG.density(sm)), keep(FG.surface3d(A.surface_grid(v, now))),
                keep(FG.term_structure(ts, h["atm30"])), keep(FG.skew_terms(ts)),
                keep(FG.rr_bf_history(A.smile_history(v, expiry), expiry or "")),
                keep(FG.realised_vs_implied(A.realised_intraday(v), A.atm30_history(v))),
                keep(FG.carry(ts)),
                ch_data, ch_cols, ch_style, fw_data, fw_cols, keep(FG.vrp(vrp)),
                keep(FG.open_interest(oi, expiry or "")), keep(FG.traded_by_strike(flow["by_strike"])),
                tape_data, tape_cols,
                _clock(source, now), "; ".join(v.get("_errors", []))]

    # ------------------------------------------------------------ risk
    @app.callback(
        [Output("r-tiles", "children"), Output("r-scen", "figure"), Output("r-waterfall", "figure"),
         Output("r-vega-exp", "figure"), Output("r-vega-delta", "figure"), Output("r-factors", "figure"),
         Output("r-delta-t", "figure"), Output("r-vega-t", "figure"), Output("r-gamma-t", "figure"),
         Output("r-pnl-t", "figure"), Output("r-pos", "data"), Output("r-pos", "columns"),
         Output("r-var", "data"), Output("r-var", "columns")],
        [Input("tick", "n_intervals"), Input("tabs", "value")])
    def risk_page(_, tab):
        if tab != "risk":
            return [dash.no_update] * 14
        v = source.views(RISK_VIEWS)
        t = A.risk_tiles(v)
        tiles = [tile("Book value", _fmt(t["mtm"]), "USD, mark to model"),
                 tile("Spot delta", _fmt(t["deltaspot"], "{:+.3f}"), "BTC, smile rule"),
                 tile("Cash delta", _fmt(t["cashdelta"], "{:+,.0f}"), "USD per +1% BTC"),
                 tile("Gamma", _fmt(t["gamma"], "{:+.3f}"), "BTC delta per +1%"),
                 tile("Vega", _fmt(t["vega"], "{:+,.0f}"), "USD per vol point"),
                 tile("Theta", _fmt(t["theta"], "{:+,.0f}"), "USD per day"),
                 tile("VaR 99%", _fmt(t.get("var99_fhs")), "USD, 1 day, FHS"),
                 tile("ES 97.5%", _fmt(t.get("es975_fhs")), "USD, 1 day, FHS"),
                 tile("Smile rule R", _fmt(t["R"], "{:g}"), "0 moneyness · 1 strike")]
        rl = v["risk_last"]
        by_exp = rl[rl["kind"] == "expiry"]
        order = ["10P", "25P", "ATM", "25C", "10C"]
        by_d = rl[rl["kind"] == "delta"].set_index("bucket").reindex(order).dropna(how="all").reset_index()
        fac = pd.DataFrame({"factor": ["ATM vol", "25Δ RR", "25Δ BF"],
                            "usd": [t["vatm"], t["vrr"], t["vbf"]]}) if math.isfinite(t["vatm"]) else pd.DataFrame()
        r1 = v["risk1m"]
        pos = A.positions(v)
        return [tiles,
                keep(FG.scenario_heatmap(A.scenario_matrix(v), t["R"] if math.isfinite(t["R"]) else 0)),
                keep(FG.waterfall(A.pnl_waterfall(v))),
                keep(FG.bars(by_exp.assign(bucket=by_exp["bucket"].map(A.short)), "bucket", "vega",
                             "Vega by expiry", "USD per vol point")),
                keep(FG.bars(by_d, "bucket", "vega", "Vega by delta bucket", "USD per vol point", th.AQUA)),
                keep(FG.bars(fac, "factor", "usd", "Smile-shape exposure", "USD per +1 vol point", th.VIOLET)),
                keep(FG.series(r1, "deltaspot", "Spot delta", "BTC")),
                keep(FG.series(r1, "vega", "Vega", "USD / vol pt", color=th.AQUA)),
                keep(FG.series(r1, "gamma", "Gamma", "BTC per 1%", color=th.ORANGE)),
                keep(FG.pnl_path(A.pnl_path(v))),
                *_simple_table(pos, {"qty": "{:+.2f}", "entry": "{:,.2f}", "iv": "{:.2%}", "delta": "{:+.3f}",
                                     "gamma_1pct": "{:+.3f}", "vega": "{:+,.0f}", "theta": "{:+,.0f}"})[::-1],
                *_var_table(v["vares"])[::-1]]

    # ------------------------------------------------------------ system
    @app.callback(
        [Output("s-tiles", "children"), Output("s-quotes", "figure"), Output("s-latency", "figure"),
         Output("s-fits", "data"), Output("s-fits", "columns"), Output("s-counts", "data"),
         Output("s-counts", "columns"), Output("s-gaps", "data"), Output("s-gaps", "columns")],
        [Input("tick", "n_intervals"), Input("tabs", "value")])
    def system_page(_, tab):
        if tab != "system":
            return [dash.no_update] * 9
        now = source.now()
        v = source.views(SYSTEM_VIEWS)
        fh = A.feed_health(v)
        lat = v["lat1m"]
        tiles = [tile("Quote updates", _fmt(fh.get("quotes_per_s", np.nan), "{:,.0f}/s"), "last 5 minutes"),
                 tile("Exchange → feed", _fmt(fh.get("lat_med_ms", np.nan), "{:,.0f} ms"), "median"),
                 tile("Feed → tickerplant", _fmt(float(lat["ms"].iloc[-1]) if len(lat) else np.nan, "{:.1f} ms"),
                      "median, last minute" if len(lat) else "live system only"),
                 tile("Crossed books", _fmt(fh.get("crossed", np.nan)), "today"),
                 tile("One-sided books", _fmt(fh.get("onesided", np.nan)), "today"),
                 tile("Feed outages", str(len(v["gap"])), "today")]
        ex = A.expiries(v["latest_surface"], now)
        fits = ex[["sym", "days", "n", "fsrc", "rmse", "afrmse", "inband", "afinband", "arbgap", "cold"]] if len(ex) else ex
        gaps = (v["gap"][["start", "end", "reason"]] if len(v["gap"])
                else pd.DataFrame({"status": ["no outages recorded"]}))
        return [tiles, keep(FG.dq_quotes(fh["dq"])), keep(FG.latency(fh["dq"], lat)),
                *_simple_table(fits, {"days": "{:.1f}", "rmse": "{:.2%}", "afrmse": "{:.2%}", "inband": "{:.0%}",
                                      "afinband": "{:.0%}", "arbgap": "{:.3%}"})[::-1],
                *_simple_table(v["counts"], {"rows": "{:,.0f}"})[::-1],
                *_simple_table(gaps, {})[::-1]]

    return app


# ---------------------------------------------------------------- tables
def _clock(source, now=None):
    now = source.now() if now is None else now
    return f"data time {now:%Y-%m-%d %H:%M:%S} UTC"


def _simple_table(df: pd.DataFrame, fmts: dict):
    """(columns, data) with values pre-formatted as text (tables show exactly what is formatted)."""
    if df is None or df.empty:
        return [], []
    out = df.copy()
    for c in out.columns:
        if c in fmts:
            out[c] = [_fmt(x, fmts[c]) for x in out[c]]
        elif pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%H:%M:%S")
        elif pd.api.types.is_float_dtype(out[c]):
            out[c] = [_fmt(x, "{:,.4g}") for x in out[c]]
        else:
            out[c] = out[c].astype(str)
    return [{"name": c, "id": c} for c in out.columns], out.to_dict("records")


def _chain_table(ch: pd.DataFrame):
    fm = {"strike": "{:,.0f}", "bsize": "{:,.1f}", "bid": "{:.4f}", "ask": "{:.4f}", "asize": "{:,.1f}",
          "bidiv": "{:.2%}", "midiv": "{:.2%}", "askiv": "{:.2%}", "model": "{:.2%}",
          "markiv": "{:.2%}", "delta": "{:+.3f}", "gamma": "{:.2e}", "vega": "{:,.1f}", "theta": "{:,.1f}",
          "oi": "{:,.1f}", "vol": "{:,.1f}"}
    cols, data = _simple_table(ch.drop(columns=["rich"]) if len(ch) else ch, fm)
    if not cols:
        return [], [], []
    # "rich" stays a number (formatted by the table), so the colour rule compares numbers:
    # beyond one half-spread rich (red tint) or cheap (blue tint) vs the arbitrage-free smile
    for row, val in zip(data, ch["rich"]):
        row["rich"] = round(float(val), 2) if np.isfinite(val) else None
    i = [c["id"] for c in cols].index("model") + 1
    cols.insert(i, {"name": "rich", "id": "rich", "type": "numeric",
                    "format": Format(precision=2, scheme=Scheme.fixed, sign=Sign.positive)})
    style = [{"if": {"filter_query": "{rich} >= 1", "column_id": "rich"}, "backgroundColor": "rgba(230,103,103,0.28)"},
             {"if": {"filter_query": "{rich} <= -1", "column_id": "rich"}, "backgroundColor": "rgba(57,135,229,0.28)"}]
    return cols, data, style


def _fwd_table(ts: pd.DataFrame):
    if ts.empty:
        return [], []
    keep = [c for c in ["sym", "days", "F", "basis", "carry", "diff", "D", "r_btc", "r_se", "fsrc", "pairs", "atmvol",
                        "rr25", "bf25"] if c in ts]
    return _simple_table(ts[keep], {"days": "{:.1f}", "F": "{:,.0f}", "basis": "{:+.3%}", "carry": "{:+.2%}",
                                    "diff": "{:+,.1f}", "D": "{:.5f}", "r_btc": "{:+.2%}", "r_se": "±{:.2%}", "pairs": "{:,.0f}",
                                    "atmvol": "{:.2%}", "rr25": "{:+.2%}", "bf25": "{:+.2%}"})


def _tape_table(t: pd.DataFrame):
    if t is None or t.empty:
        return [], []
    keep = t[["time", "sym", "side", "size", "price", "usd", "iv"]].copy()
    keep["iv"] = keep["iv"] / 100
    return _simple_table(keep, {"size": "{:,.1f}", "price": "{:.4f}", "usd": "{:,.0f}", "iv": "{:.2%}"})


def _var_table(v: pd.DataFrame):
    if v is None or v.empty:
        return [], []
    last = v[v["time"] == v["time"].max()]
    out = pd.DataFrame({
        "method": last["method"].str.upper(), "VaR 99%": last["var99"], "ES 97.5%": last["es975"],
        "ES 99%": last["es99"], "window (days)": last["window"],
        "backtest": [f"{e} / {d} days (expected {d * 0.01:.1f})" for e, d in zip(last["btexc"], last["btdays"])],
        "Kupiec p": last["kupiec"], "source": last["src"].str.replace("_", " ")})
    return _simple_table(out, {"VaR 99%": "{:,.0f}", "ES 97.5%": "{:,.0f}", "ES 99%": "{:,.0f}",
                               "window (days)": "{:,.0f}", "Kupiec p": "{:.2f}"})
