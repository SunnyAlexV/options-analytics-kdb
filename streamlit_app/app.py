"""Public demo on Streamlit Community Cloud: a recorded session of the live system, replayed.

    streamlit run streamlit_app/app.py

The live system is the Dash app over kdb+ (dashboard/app.py). This page is a window onto it
for people without kdb+: the same bundle replay (dashboard/sources.py ReplaySource), the same
calculations (dashboard/analytics.py) and the same charts (dashboard/figures.py), so every
number matches what the live dashboard showed. Only the page layout is Streamlit's.

The replay clock runs at 10x real time and loops; everyone watching sees the same moment, like
a live market. "Pause at a moment" freezes the page at any minute of the session instead.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard import analytics as A  # noqa: E402
from dashboard import figures as FG  # noqa: E402
from dashboard import tables as TB  # noqa: E402
from dashboard import theme as th  # noqa: E402
from dashboard.sources import ReplaySource, assets_in, for_asset  # noqa: E402

BUNDLE = Path(__file__).resolve().parent / "bundle"
SPEED = 10.0
REFRESH_S = 3
MARKET_VIEWS = ["latest_surface", "latest_iv", "latest_ref", "latest_snap", "latest_quote", "latest_fwd",
                "spot1m", "surf1m", "trades"]
RISK_VIEWS = ["risk_last", "risk1m", "scen_last", "pnl", "vares", "pos", "latest_iv"]
SYSTEM_VIEWS = ["dq", "gap", "lat1m", "counts", "latest_surface"]
REPO = "https://github.com/SunnyAlexV/options-analytics-kdb"

st.set_page_config(page_title="Crypto options desk", page_icon="📈", layout="wide")


# --------------------------------------------------------------------------- data (once per server)
@st.cache_resource(show_spinner="Loading the recorded session…")
def source() -> ReplaySource:
    return ReplaySource(BUNDLE, speed=SPEED)


@st.cache_resource
def vrp_frame() -> pd.DataFrame:
    f = BUNDLE / "history.csv"
    return A.vrp_history(pd.read_csv(f, parse_dates=["date"])) if f.exists() else pd.DataFrame()


@st.cache_resource
def minutes() -> list[pd.Timestamp]:
    """Every minute of the session after the warm-up (the replay starts there too)."""
    s = source()
    return list(pd.date_range((s.t_start + s.warm).ceil("1min"), s.t_end.floor("1min"), freq="1min"))


# --------------------------------------------------------------------------- page pieces
CSS = f"""
<style>
.stApp {{ background:{th.PAGE}; }}
.block-container {{ padding-top: 2.2rem; max-width: 1800px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:8px; margin:4px 0 10px; }}
.tile {{ background:{th.SURFACE}; border:1px solid {th.BORDER}; border-radius:8px; padding:10px 12px; }}
.tile .lbl {{ color:{th.MUTED}; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }}
.tile .val {{ color:{th.INK}; font-size:20px; font-weight:600; margin-top:2px; }}
.tile .sub {{ color:{th.INK2}; font-size:11px; margin-top:2px; }}
.meta {{ color:{th.MUTED}; font-size:12px; }}
.note {{ color:{th.MUTED}; font-size:11px; margin:-6px 0 6px; }}
h3 {{ font-size:14px !important; }}
</style>
"""


def tiles(items):
    html = "".join(f'<div class="tile"><div class="lbl">{a}</div><div class="val">{b}</div>'
                   f'<div class="sub">{c}</div></div>' for a, b, c in items)
    st.markdown(f'<div class="tiles">{html}</div>', unsafe_allow_html=True)


def chart(fig, key):
    st.plotly_chart(fig, theme=None, key=key, config={"displaylogo": False})


def table(df: pd.DataFrame, title: str | None = None, note: str | None = None, height="auto", key=None):
    if title:
        st.markdown(f"### {title}")
    if note:
        st.markdown(f'<div class="note">{note}</div>', unsafe_allow_html=True)
    if df is None or df.empty:
        st.caption("no data yet")
        return
    st.dataframe(df, hide_index=True, height=height, key=key)


def rich_styled(df: pd.DataFrame):
    """The chain with its "rich" column tinted: red = rich, blue = cheap (beyond 1 half-spread).
    The column is shown as text (blank where there is no two-sided quote: st.dataframe would
    print "None"); the tint is decided on the numbers."""
    val = pd.to_numeric(df["rich"], errors="coerce")
    out = df.assign(rich=[f"{x:+.2f}" if np.isfinite(x) else "" for x in val])
    tint = ["background-color: rgba(230,103,103,0.28)" if x >= 1 else
            "background-color: rgba(57,135,229,0.28)" if x <= -1 else "" for x in val.fillna(0)]
    return out.style.apply(lambda _: tint, subset=["rich"])


# --------------------------------------------------------------------------- pages
def market_page(now, asset, key):
    s = source()
    v = for_asset(s.views(MARKET_VIEWS, now), asset)
    ex = A.expiries(v["latest_surface"], now)
    if ex.empty:
        st.info("No fitted smiles yet at this moment of the replay.")
        return
    labels = list(ex["sym"])
    default = int((ex["days"] - 30).abs().argmin())                   # the expiry nearest 30 days
    keep = st.session_state.get("expiry")
    expiry = st.selectbox("Expiry", labels, index=labels.index(keep) if keep in labels else default,
                          format_func=lambda x: f"{x}  ({float(ex.loc[ex['sym'] == x, 'days'].iloc[0]):.0f}d)",
                          key=f"expiry_{key}", width=260)
    st.session_state["expiry"] = expiry
    h = A.headline(v, now)
    flow = A.trade_flow(v)
    f = TB.fmt
    tiles([(f"{asset} index", f(h["spot"]), f"{h['spot_chg']:+.2%} in view" if math.isfinite(h["spot_chg"]) else ""),
           ("ATM vol 30d", f(h["atm30"] * 100, "{:.2f}%"), "constant maturity, our fits"),
           ("Front RR 25Δ", f(h["rr25"] * 100, "{:+.2f}"), h["front"]),
           ("Front BF 25Δ", f(h["bf25"] * 100, "{:+.2f}"), h["front"]),
           ("Put/call volume", f(flow["pc_ratio"], "{:.2f}"), "traded size, puts ÷ calls"),
           ("Buyer-initiated", f(flow["buy_share"], "{:.0%}"), "share of traded size"),
           ("Expiries fitted", str(h["n_exp"]), "arbitrage-free SVI")])
    sm = A.smile(v, expiry, now)
    ts = A.term_structure(v, now)
    c1, c2 = st.columns([2, 1])
    with c1:
        chart(FG.smile(sm), "m-smile")
    with c2:
        chart(FG.density(sm), "m-density")
    c1, c2 = st.columns(2)
    with c1:
        chart(FG.surface3d(A.surface_grid(v, now)), "m-surface")
    with c2:
        chart(FG.term_structure(ts, h["atm30"]), "m-term")
        chart(FG.skew_terms(ts), "m-skew")
    c1, c2, c3 = st.columns(3)
    with c1:
        chart(FG.rr_bf_history(A.smile_history(v, expiry), expiry), "m-rrbf")
    with c2:
        chart(FG.realised_vs_implied(A.realised_intraday(v), A.atm30_history(v)), "m-rv")
    with c3:
        chart(FG.carry(ts), "m-carry")
    st.markdown("### Option chain")
    st.markdown('<div class="note">IVs in %. rich = (mid IV − fitted IV) / half-spread: above +1 the mid is rich to '
                'the arbitrage-free smile by more than half the spread (red), below −1 cheap (blue). '
                "mark = Deribit's own mark IV.</div>", unsafe_allow_html=True)
    ch = TB.chain_frame(A.chain(v, expiry, now))
    if ch.empty:
        st.caption("no data yet")
    else:
        st.dataframe(rich_styled(ch), hide_index=True, height=420, key=f"m-chain-{key}")
    c1, c2 = st.columns(2)
    with c1:
        table(TB.fwd_frame(ts), "Forwards and fits by expiry",
              "basis and carry vs the spot index; diff = parity F − Deribit F in USD; D = discount factor from "
              "parity; r_impl = the rate it implies in the premium currency (BTC/ETH for inverse, USDC for linear "
              "options), r_se its standard error; fit quality in vol points.", key=f"m-fwd-{key}")
    with c2:
        chart(FG.vrp(vrp_frame()), "m-vrp")
    c1, c2 = st.columns(2)
    with c1:
        chart(FG.open_interest(A.open_interest(v, expiry), expiry), "m-oi")
    with c2:
        chart(FG.traded_by_strike(flow["by_strike"]), "m-volume")
    table(TB.tape_frame(flow["tape"]), "Trade tape (latest 60)",
          "price per contract in the premium currency (the coin for BTC/ETH, USDC for *_USDC); usd in dollars",
          height=360, key=f"m-tape-{key}")


def risk_page(now, asset, key):
    v = for_asset(source().views(RISK_VIEWS, now), asset)
    if v["risk_last"].empty:
        st.info(f"No risk book for {asset} in this recording: the sample book is held in BTC.")
        return
    t = A.risk_tiles(v)
    f = TB.fmt
    tiles([("Book value", f(t["mtm"]), "USD, mark to model"),
           ("Spot delta", f(t["deltaspot"], "{:+.3f}"), f"{asset.split('_')[0]}, smile rule"),
           ("Cash delta", f(t["cashdelta"], "{:+,.0f}"), "USD per +1% move"),
           ("Gamma", f(t["gamma"], "{:+.3f}"), "delta change per +1%"),
           ("Vega", f(t["vega"], "{:+,.0f}"), "USD per vol point"),
           ("Theta", f(t["theta"], "{:+,.0f}"), "USD per day"),
           ("VaR 99%", f(t.get("var99_fhs")), "USD, 1 day, FHS"),
           ("ES 97.5%", f(t.get("es975_fhs")), "USD, 1 day, FHS"),
           ("Smile rule R", f(t["R"], "{:g}"), "0 moneyness · 1 strike")])
    rl = v["risk_last"]
    by_exp = rl[rl["kind"] == "expiry"]
    order = ["10P", "25P", "ATM", "25C", "10C"]
    by_d = rl[rl["kind"] == "delta"].set_index("bucket").reindex(order).dropna(how="all").reset_index()
    fac = (pd.DataFrame({"factor": ["ATM vol", "25Δ RR", "25Δ BF"], "usd": [t["vatm"], t["vrr"], t["vbf"]]})
           if math.isfinite(t["vatm"]) else pd.DataFrame())
    r1 = v["risk1m"]
    c1, c2 = st.columns(2)
    with c1:
        chart(FG.scenario_heatmap(A.scenario_matrix(v), t["R"] if math.isfinite(t["R"]) else 0), "r-scen")
    with c2:
        chart(FG.waterfall(A.pnl_waterfall(v)), "r-waterfall")
    c1, c2, c3 = st.columns(3)
    with c1:
        chart(FG.bars(by_exp.assign(bucket=by_exp["bucket"].map(A.short)), "bucket", "vega",
                      "Vega by expiry", "USD per vol point"), "r-vega-exp")
    with c2:
        chart(FG.bars(by_d, "bucket", "vega", "Vega by delta bucket", "USD per vol point", th.AQUA), "r-vega-delta")
    with c3:
        chart(FG.bars(fac, "factor", "usd", "Smile-shape exposure", "USD per +1 vol point", th.VIOLET), "r-factors")
    cols = st.columns(4)
    for c, fig, k in zip(cols, [FG.series(r1, "deltaspot", "Spot delta", "coins"),
                                FG.series(r1, "vega", "Vega", "USD / vol pt", color=th.AQUA),
                                FG.series(r1, "gamma", "Gamma", "coins per 1%", color=th.ORANGE),
                                FG.pnl_path(A.pnl_path(v))], ["r-delta-t", "r-vega-t", "r-gamma-t", "r-pnl-t"]):
        with c:
            chart(fig, k)
    c1, c2 = st.columns(2)
    with c1:
        table(TB.text_frame(A.positions(v), TB.POS_FMT), "Positions, live Greeks",
              "delta in coins, gamma as coins of delta per 1% move, vega USD per vol point, theta USD per day",
              key=f"r-pos-{key}")
    with c2:
        table(TB.var_frame(v["vares"]), "VaR and Expected Shortfall (USD, 1 day)",
              "HS: last 365 days equally weighted. FHS: each day rescaled to today's volatility (EWMA 0.94). "
              "Backtest: exceptions of VaR 99% over the past year with Kupiec's p-value "
              "(below 0.05 = the method is rejected).", key=f"r-var-{key}")


def system_page(now, key):
    v = source().views(SYSTEM_VIEWS, now)
    fh = A.feed_health(v)
    f = TB.fmt
    tiles([("Quote updates", f(fh.get("quotes_per_s", np.nan), "{:,.0f}/s"), "last 5 minutes"),
           ("Exchange → feed", f(fh.get("lat_med_ms", np.nan), "{:,.0f} ms"), "median"),
           ("Crossed books", f(fh.get("crossed", np.nan)), "today"),
           ("One-sided books", f(fh.get("onesided", np.nan)), "today"),
           ("Feed outages", str(len(v["gap"])), "today")])
    c1, c2 = st.columns(2)
    with c1:
        chart(FG.dq_quotes(fh["dq"]), "s-quotes")
    with c2:
        chart(FG.latency(fh["dq"], v["lat1m"]), "s-latency")
    ex = A.expiries(v["latest_surface"], now)
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        table(TB.text_frame(ex[TB.FITS_COLS], TB.FITS_FMT) if len(ex) else ex, "Smile fits by expiry",
              "n quotes used; RMSE in vol points; inband = share of quotes whose fitted vol lies inside their "
              "bid–ask; arbgap = extra RMSE paid to remove arbitrage", key=f"s-fits-{key}")
    with c2:
        table(TB.text_frame(v["counts"], {"rows": "{:,.0f}"}), "Rows per table in this demo",
              "thinned to one row per instrument per minute", key=f"s-counts-{key}")
    with c3:
        gaps = (v["gap"][["start", "end", "reason"]] if len(v["gap"])
                else pd.DataFrame({"status": ["no outages recorded"]}))
        table(TB.text_frame(gaps, {}), "Feed outages", key=f"s-gaps-{key}")


# --------------------------------------------------------------------------- layout
st.markdown(CSS, unsafe_allow_html=True)
s = source()
head = st.columns([3, 2, 3, 3], vertical_alignment="bottom")
with head[0]:
    st.markdown(f'<div style="font-size:24px;font-weight:600;color:{th.INK}">Crypto options desk</div>',
                unsafe_allow_html=True)
    st.markdown(f'<span class="meta">Replay of a live session recorded from Deribit by '
                f'<a href="{REPO}">this kdb+ / C++ / Python system</a></span>', unsafe_allow_html=True)
with head[2]:
    page = st.segmented_control("Page", ["Market", "Risk", "System"], default="Market", required=True,
                                key="page", label_visibility="collapsed")
with head[3]:
    live = st.toggle(f"Live replay ({SPEED:g}× speed)", value=True, key="live",
                     help="Off: pause the replay and pick any minute of the session.")

assets = assets_in(s.views(["latest_surface"], s.t_end)) or ["BTC"]
with head[1]:
    asset = st.selectbox("Asset", assets, key="asset", label_visibility="collapsed")

if live:
    @st.fragment(run_every=REFRESH_S)
    def live_body():
        now = s.now()
        st.markdown(f'<span class="meta">data time {now:%Y-%m-%d %H:%M:%S} UTC · refreshes every '
                    f'{REFRESH_S} s</span>', unsafe_allow_html=True)
        render(now, "live")
else:
    mins = minutes()
    moment = st.select_slider("Moment of the session (UTC)", options=mins, value=mins[len(mins) // 2],
                              format_func=lambda t: f"{t:%H:%M}", key="moment")


def render(now, key):
    if page == "Market":
        market_page(now, asset, key)
    elif page == "Risk":
        risk_page(now, asset, key)
    else:
        system_page(now, key)


if live:
    live_body()
else:
    render(moment, "paused")

st.markdown(f'<div class="meta" style="margin-top:24px">Live, this dashboard runs in Dash over the kdb+ '
            f'gateway; this demo replays a recorded session through the same analytics code. '
            f'Source and documentation: <a href="{REPO}">{REPO}</a></div>', unsafe_allow_html=True)
