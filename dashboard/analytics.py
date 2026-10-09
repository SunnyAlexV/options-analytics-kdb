"""Everything the dashboard derives from the views (dashboard/sources.py), in numpy/pandas.

No C++ and no kdb+ here, so the public demo runs anywhere. Each function says what it
computes and from which columns; tests/test_dashboard.py checks them on known inputs.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.special import ndtr

YEAR_S = 365 * 86400
SVI_RAW = ("a", "b", "rho", "m", "sigma")
SVI_AF = ("afa", "afb", "afrho", "afm", "afsigma")


# ------------------------------------------------------------------ SVI in numpy
def svi_w(p, k):
    """Raw SVI total variance w(k) = a + b (rho (k-m) + sqrt((k-m)^2 + sigma^2))."""
    a, b, rho, m, s = p
    x = np.asarray(k, dtype=float) - m
    return a + b * (rho * x + np.sqrt(x * x + s * s))


def svi_vol(p, k, T):
    return np.sqrt(np.maximum(svi_w(p, k), 0.0) / T)


def svi_density(p, k, T):
    """Risk-neutral density of the log-return x = ln(S_T / F) at expiry, evaluated at x = k,
    straight from the smile.

    Gatheral (2004): with w(k), d2(k) = -k/sqrt(w) - sqrt(w)/2 and
        g(k) = (1 - k w'/(2w))^2 - (w'^2 / 4)(1/w + 1/4) + w''/2,
    the density of ln(K/F) at expiry is  p(k) = g(k) / sqrt(2 pi w) * exp(-d2^2 / 2).
    g < 0 anywhere would mean a negative density: butterfly arbitrage.
    """
    a, b, rho, m, s = p
    k = np.asarray(k, dtype=float)
    x = k - m
    r = np.sqrt(x * x + s * s)
    w = np.maximum(a + b * (rho * x + r), 1e-12)
    w1 = b * (rho + x / r)
    w2 = b * s * s / r ** 3
    g = (1 - k * w1 / (2 * w)) ** 2 - (w1 * w1 / 4) * (1 / w + 0.25) + w2 / 2
    d2 = -k / np.sqrt(w) - np.sqrt(w) / 2
    return g / np.sqrt(2 * np.pi * w) * np.exp(-0.5 * d2 * d2)


# ------------------------------------------------------------------ helpers
def short(sym: str) -> str:
    """BTC-25DEC26 -> 25DEC26 (for axis labels)."""
    return str(sym).split("-", 1)[-1]


def label(sym: str) -> str:
    """BTC-25DEC26-90000-C -> BTC-25DEC26."""
    return "-".join(str(sym).split("-")[:2])


def years_to(expiry: pd.Series, now: pd.Timestamp) -> pd.Series:
    return (pd.to_datetime(expiry) - now).dt.total_seconds() / YEAR_S


def expiries(surface: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Fitted expiries, nearest first, with days to expiry."""
    if surface.empty:
        return pd.DataFrame(columns=["sym", "days"])
    s = surface.assign(days=years_to(surface["expiry"], now) * 365)
    return s[s["days"] > 0].sort_values("days").reset_index(drop=True)


# ------------------------------------------------------------------ market page
def headline(views: dict, now: pd.Timestamp) -> dict:
    """Top-row numbers: spot, its change today, 30-day constant-maturity ATM vol, front RR/BF."""
    spot = views["spot1m"]
    out = {"spot": np.nan, "spot_chg": np.nan, "atm30": np.nan, "rr25": np.nan, "bf25": np.nan,
           "front": "", "n_exp": 0}
    if len(spot):
        out["spot"] = float(spot["price"].iloc[-1])
        out["spot_chg"] = float(spot["price"].iloc[-1] / spot["price"].iloc[0] - 1)
    ex = expiries(views["latest_surface"], now)
    if len(ex):
        out["atm30"] = constant_maturity_atm(ex, 30.0)
        f = ex.iloc[0]
        out.update(rr25=float(f["rr25"]), bf25=float(f["bf25"]), front=f["sym"], n_exp=len(ex))
    return out


def constant_maturity_atm(ex: pd.DataFrame, days: float) -> float:
    """ATM vol at a fixed maturity, interpolating TOTAL variance sigma^2 T linearly in T between
    the two neighbouring expiries (the standard way: variance, not vol, is additive in time).
    Like Deribit's DVOL in spirit (30 days), but from our own fitted smiles."""
    T = ex["days"].to_numpy() / 365
    w = ex["atmvol"].to_numpy() ** 2 * T
    t = days / 365
    if len(T) == 0 or not np.all(np.isfinite(w)):
        return float("nan")
    if t <= T[0]:
        return float(ex["atmvol"].iloc[0])
    if t >= T[-1]:
        return float(ex["atmvol"].iloc[-1])
    return float(math.sqrt(np.interp(t, T, w) / t))


def smile(views: dict, expiry: str, now: pd.Timestamp, kgrid=None) -> dict:
    """One expiry's market points and fitted curves, in log-moneyness k = ln(K/F).

    Points: out-of-the-money options (puts below F, calls above) with bid/mid/ask implied vols,
    plus Deribit's own mark IV for the same strikes. Curves: raw and arbitrage-free SVI."""
    sf = views["latest_surface"]
    row = sf[sf["sym"] == expiry]
    if row.empty:
        return {}
    row = row.iloc[0]
    T = float(years_to(pd.Series([row["expiry"]]), now).iloc[0])
    F = float(row["F"])
    ref = views["latest_ref"][["sym", "strike", "cp"]]
    iv = views["latest_iv"].merge(ref, on="sym")
    iv = iv[iv["sym"].map(label) == expiry]
    iv = iv[np.where(iv["cp"] == "C", iv["strike"] >= F, iv["strike"] < F)]
    iv = iv.assign(k=np.log(iv["strike"] / F)).sort_values("k")
    snap = views["latest_snap"].merge(ref, on="sym")
    snap = snap[(snap["sym"].isin(iv["sym"]))]
    snap = snap.assign(k=np.log(snap["strike"] / F)).sort_values("k")
    if kgrid is None:
        lo, hi = (iv["k"].min(), iv["k"].max()) if len(iv) else (-0.5, 0.5)
        span = max(hi - lo, 0.1)
        kgrid = np.linspace(lo - 0.15 * span, hi + 0.15 * span, 241)
    raw = [row[c] for c in SVI_RAW]
    af = [row[c] for c in SVI_AF]
    return {"expiry": expiry, "T": T, "F": F, "points": iv, "marks": snap, "k": kgrid,
            "raw": svi_vol(raw, kgrid, row["T"]), "af": svi_vol(af, kgrid, row["T"]),
            "density": svi_density(af, kgrid, row["T"]), "row": row}


def surface_grid(views: dict, now: pd.Timestamp, n_z: int = 61, zmax: float = 2.5) -> dict:
    """Arbitrage-free vol on a (days to expiry) x (standardised moneyness) grid for the 3D surface.

    Standardised moneyness z = ln(K/F) / (sigma_ATM sqrt(T)): the number of ATM standard
    deviations a strike is from the forward. A fixed range of z covers the same range of deltas
    on every expiry, so a 3-day smile is not stretched into its far wings next to a 1-year one."""
    ex = expiries(views["latest_surface"], now)
    z = np.linspace(-zmax, zmax, n_z)
    rows = []
    for _, r in ex.iterrows():
        k = z * r["atmvol"] * math.sqrt(r["T"])
        rows.append(svi_vol([r[c] for c in SVI_AF], k, r["T"]))
    Z = np.array(rows) if rows else np.zeros((0, n_z))
    return {"z": z, "days": ex["days"].to_numpy(), "vol": Z, "labels": ex["sym"].tolist()}


def term_structure(views: dict, now: pd.Timestamp) -> pd.DataFrame:
    """Per expiry: ATM vol, RR25, BF25, forward, basis to spot and the annualised carry it implies,
    and our parity forward vs Deribit's, with the discount factor and the rate it implies, in the
    premium currency (BTC or ETH for inverse options, USDC for linear ones)."""
    ex = expiries(views["latest_surface"], now)
    if ex.empty:
        return ex
    spot = views["spot1m"]["price"].iloc[-1] if len(views["spot1m"]) else np.nan
    fw = views["latest_fwd"][["sym", "D", "seD", "seF", "und", "diff", "fsrc", "n"]] if len(views["latest_fwd"]) else None
    out = ex[["sym", "days", "atmvol", "rr25", "bf25", "F", "fsrc", "n", "rmse", "inband", "afinband", "arbgap"]].copy()
    T = out["days"] / 365
    out["basis"] = out["F"] / spot - 1
    out["carry"] = np.log(out["F"] / spot) / T                     # annualised, continuously compounded
    if fw is not None:
        out = out.merge(fw.rename(columns={"fsrc": "fwd_src", "n": "pairs"}), on="sym", how="left")
        out["r_impl"] = -np.log(out["D"]) / T                      # premium-currency rate implied by parity's D
        # its standard error, from the regression's error on D (delta method: d r = -dD / (D T)).
        # Short expiries have tiny T, so a small error in D is a large error in r: read r with r_se.
        out["r_se"] = out["seD"] / (out["D"] * T)
    return out


def chain(views: dict, expiry: str, now: pd.Timestamp) -> pd.DataFrame:
    """Option chain for one expiry: quotes, implied vols, the fitted (arbitrage-free) vol, how rich
    or cheap the mid is vs the fit in half-spreads, Deribit's mark IV, Greeks, open interest."""
    sm = smile(views, expiry, now)
    if not sm:
        return pd.DataFrame()
    ref = views["latest_ref"][["sym", "strike", "cp"]]
    df = (views["latest_iv"].merge(ref, on="sym")
          .merge(views["latest_quote"][["sym", "bid", "ask", "bsize", "asize"]], on="sym", how="left")
          .merge(views["latest_snap"][["sym", "markiv", "oi", "vol"]], on="sym", how="left"))
    df = df[df["sym"].map(label) == expiry].copy()
    # each IV was priced on the forward known when its quote arrived (iv.F); compare it with the
    # fitted smile at the same log-moneyness, so a forward that moved since does not show up as
    # calls rich and puts cheap at one strike
    k = np.log(df["strike"] / df["F"])
    df["model"] = svi_vol([sm["row"][c] for c in SVI_AF], k, sm["row"]["T"])
    hs = (df["askiv"] - df["bidiv"]) / 2
    df["rich"] = (df["midiv"] - df["model"]) / hs.where(hs > 0)
    df["markiv"] = df["markiv"] / 100
    cols = ["strike", "cp", "bsize", "bid", "ask", "asize", "bidiv", "midiv", "askiv", "model", "rich",
            "markiv", "delta", "gamma", "vega", "theta", "oi", "vol"]
    df = df[np.isfinite(df["midiv"])]                       # quoted options only (an IV exists)
    return df.sort_values(["strike", "cp"])[cols].reset_index(drop=True)


def smile_history(views: dict, expiry: str) -> pd.DataFrame:
    s = views["surf1m"]
    return s[s["sym"] == expiry].sort_values("time") if len(s) else s


def realised_intraday(views: dict, window: int = 30) -> pd.DataFrame:
    """Realised vol from 1-minute log returns of the index, rolling `window` minutes,
    annualised with sqrt(minutes per year). Crypto trades 24/7, so every minute counts."""
    s = views["spot1m"]
    if len(s) < 3:
        return pd.DataFrame(columns=["time", "rv"])
    r = np.log(s["price"]).diff()
    rv = r.rolling(window, min_periods=max(5, window // 3)).std() * math.sqrt(365 * 24 * 60)
    return pd.DataFrame({"time": s["time"], "rv": rv})


def vrp_history(hist: pd.DataFrame, window: int = 30) -> pd.DataFrame:
    """Variance risk premium over the years: DVOL (implied, 30 days ahead) against the realised
    vol of the NEXT 30 days, and against the PAST 30 days. Implied above the realised that
    followed is the premium option sellers collect on average."""
    if hist is None or hist.empty:
        return pd.DataFrame()
    h = hist.sort_values("date").reset_index(drop=True)
    r = np.log(h["spot"]).diff()
    past = r.rolling(window).std() * math.sqrt(365)
    nxt = r[::-1].rolling(window).std()[::-1].shift(-1) * math.sqrt(365)
    return pd.DataFrame({"date": h["date"], "dvol": h["dvol"] / 100, "rv_past": past, "rv_next": nxt,
                         "vrp": h["dvol"] / 100 - nxt})


def trade_flow(views: dict) -> dict:
    """Trades: the tape (USD prices too), volume by strike for calls and puts, buy/sell split."""
    t = views["trades"]
    if t.empty:
        return {"tape": t, "by_strike": pd.DataFrame(), "pc_ratio": np.nan, "buy_share": np.nan}
    ref = views["latest_ref"][["sym", "strike", "cp"]]
    t = t.merge(ref, on="sym", how="left")
    # inverse options (BTC, ETH) are priced in the coin: USD = price x index. Linear *_USDC
    # options are priced in USDC already.
    linear = t["sym"].map(label).str.contains("_USDC-")
    t = t.assign(usd=np.where(linear, t["price"], t["price"] * t["idx"]), expiry=t["sym"].map(label))
    by = t.groupby(["strike", "cp"])["size"].sum().unstack(fill_value=0).reset_index()
    calls = t.loc[t["cp"] == "C", "size"].sum()
    puts = t.loc[t["cp"] == "P", "size"].sum()
    return {"tape": t.sort_values("time", ascending=False).head(60), "by_strike": by,
            "pc_ratio": float(puts / calls) if calls else np.nan,
            "buy_share": float(t.loc[t["side"] == "buy", "size"].sum() / t["size"].sum())}


def open_interest(views: dict, expiry: str | None = None) -> pd.DataFrame:
    """Open interest by strike (calls, puts), from Deribit's snapshots."""
    s = views["latest_snap"].merge(views["latest_ref"][["sym", "strike", "cp"]], on="sym")
    if expiry:
        s = s[s["sym"].map(label) == expiry]
    return s.groupby(["strike", "cp"])["oi"].sum().unstack(fill_value=0).reset_index()


# ------------------------------------------------------------------ risk page
def risk_tiles(views: dict) -> dict:
    r = views["risk_last"]
    tot = r[r["kind"] == "total"]
    out = {k: np.nan for k in ("mtm", "deltaspot", "cashdelta", "gamma", "cashgamma", "vega", "theta",
                               "vanna", "volga", "vatm", "vrr", "vbf", "R")}
    if len(tot):
        out.update({k: float(tot[k].iloc[0]) for k in out})
    v = views["vares"]
    if len(v):
        last = v[v["time"] == v["time"].max()]
        for _, row in last.iterrows():
            out[f"var99_{row['method']}"] = float(row["var99"])
            out[f"es975_{row['method']}"] = float(row["es975"])
    return out


def pnl_waterfall(views: dict) -> pd.DataFrame:
    """Cumulative P&L explain since the start of the data: each Taylor term, with vega split
    into 'smile' (moved because spot moved, per the smile rule) and 'surface' (re-marked)."""
    p = views["pnl"]
    if p.empty:
        return pd.DataFrame(columns=["term", "usd"])
    terms = [("delta", "Delta"), ("gamma", "Gamma"), ("smile", "Vega: smile"), ("surf", "Vega: surface"),
             ("theta", "Theta"), ("vanna", "Vanna"), ("volga", "Volga"), ("unexpl", "Unexplained")]
    return pd.DataFrame({"term": [n for _, n in terms], "usd": [float(p[c].sum()) for c, _ in terms],
                         "actual": float(p["actual"].sum())})


def pnl_path(views: dict) -> pd.DataFrame:
    p = views["pnl"].sort_values("end")
    if p.empty:
        return p
    expl = p[["delta", "gamma", "vega", "theta", "vanna", "volga"]].sum(axis=1)
    return pd.DataFrame({"time": p["end"], "actual": p["actual"].cumsum(), "explained": expl.cumsum(),
                         "unexpl": p["unexpl"].cumsum()})


def positions(views: dict) -> pd.DataFrame:
    """Positions with live Greeks: each option's per-contract Greeks (iv table) times quantity;
    a future has delta 1 per contract."""
    pos = views["pos"]
    if pos.empty:
        return pos
    iv = views["latest_iv"][["sym", "midiv", "delta", "gamma", "vega", "theta", "F"]]
    df = pos.merge(iv, on="sym", how="left")
    fut = df["kind"] == "future"
    q = df["qty"]
    out = pd.DataFrame({
        "sym": df["sym"], "kind": df["kind"], "qty": q, "entry": df["entry"],
        "iv": df["midiv"].where(~fut),
        "delta": np.where(fut, q, q * df["delta"]),
        "gamma_1pct": np.where(fut, 0.0, q * df["gamma"] * df["F"] * 0.01),
        "vega": np.where(fut, 0.0, q * df["vega"]),
        "theta": np.where(fut, 0.0, q * df["theta"]),
    })
    return out


def scenario_matrix(views: dict) -> pd.DataFrame:
    s = views["scen_last"]
    if s.empty:
        return s
    # rows ascending: a heatmap draws the first row at the bottom, so +20 vol points ends on top
    return s.pivot_table(index="dvol", columns="dspot", values="pnl").sort_index()


# ------------------------------------------------------------------ system page
def feed_health(views: dict) -> dict:
    dq = views["dq"]
    if dq.empty:
        return {"dq": dq}
    d = dq.sort_values("minute")
    return {"dq": d, "quotes_per_s": float(d["quotes"].tail(5).mean() / 60),
            "lat_med_ms": float(d["latmed"].tail(5).median()),
            "crossed": int(d["crossed"].sum()), "onesided": int(d["onesided"].sum())}


def ndtr_call_delta(k, w):
    """Forward call delta N(d1) at log-moneyness k with total variance w (for delta axes)."""
    return ndtr((-np.asarray(k) + 0.5 * w) / np.sqrt(w))


def atm30_history(views: dict) -> pd.DataFrame:
    """The 30-day constant-maturity ATM vol for every minute of the day (from surf1m)."""
    s = views["surf1m"]
    if s.empty:
        return pd.DataFrame(columns=["time", "atm30"])
    out = []
    for t, g in s.groupby("time"):
        g = g.assign(days=g["T"] * 365).sort_values("days")
        g = g[g["days"] > 0]
        if len(g):
            out.append((t, constant_maturity_atm(g, 30.0)))
    return pd.DataFrame(out, columns=["time", "atm30"])
