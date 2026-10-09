"""Phase 6 dashboard: analytics maths, the replay views, every chart, and the app itself.

The end-to-end test writes a small synthetic feed recording, builds a bundle from it with the
real pipeline (surface engine + risk on a simulated clock), replays it through every view and
renders every figure: the same path the public demo takes.
"""
import gzip
import json
import math

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("dash")
pytest.importorskip("pricing", reason="build the C++ module first: pip install -e .")

from dashboard import analytics as A  # noqa: E402
from dashboard import figures as FG  # noqa: E402
from dashboard.sources import P, Q  # noqa: E402

NS = 1_000_000_000


# ------------------------------------------------------------------ maths
def test_svi_density_integrates_to_one_and_matches_breeden_litzenberger():
    import pricing as px
    p, T = [0.0049, 0.0296, -0.3, 0.02, 0.2], 30 / 365
    k = np.linspace(-1.5, 1.5, 30001)
    dens = A.svi_density(p, k, T)
    assert np.trapezoid(dens, k) == pytest.approx(1.0, abs=1e-6)
    # Breeden-Litzenberger: density of K is d2C/dK2 (undiscounted); density of k = that times K
    F = 80000.0
    K = F * np.exp(k)
    C = px.price(F, K, T, A.svi_vol(p, k, T), True)
    bl = np.gradient(np.gradient(C, K), K) * K
    mid = slice(5000, 25000)
    assert np.max(np.abs(bl[mid] - dens[mid])) < 1e-4 * dens.max()


def test_svi_w_matches_cpp():
    from pricing import _core
    p, k = [0.01, 0.06, -0.35, 0.02, 0.15], np.linspace(-2, 2, 101)
    np.testing.assert_allclose(A.svi_w(p, k), _core.svi_w(p, k), rtol=0, atol=1e-15)


def test_constant_maturity_interpolates_total_variance():
    ex = pd.DataFrame({"days": [10.0, 50.0], "atmvol": [0.40, 0.50]})
    w10, w50 = 0.40 ** 2 * 10 / 365, 0.50 ** 2 * 50 / 365
    w30 = w10 + (w50 - w10) * (30 - 10) / (50 - 10)
    assert A.constant_maturity_atm(ex, 30) == pytest.approx(math.sqrt(w30 / (30 / 365)))
    assert A.constant_maturity_atm(ex, 5) == 0.40 and A.constant_maturity_atm(ex, 90) == 0.50


def test_vrp_uses_realised_vol_of_the_following_days():
    d = pd.date_range("2025-01-01", periods=120, freq="D")
    r = np.r_[np.full(60, 0.01), np.full(60, 0.03)] * np.where(np.arange(120) % 2, 1, -1)
    h = pd.DataFrame({"date": d, "spot": 80000 * np.exp(np.cumsum(r)), "dvol": 50.0})
    v = A.vrp_history(h, window=30)
    # 40 days in, the NEXT 30 days are still the calm regime; 70 days in, the volatile one
    assert v["rv_next"].iloc[20] == pytest.approx(0.01 * math.sqrt(365), rel=0.05)
    assert v["rv_next"].iloc[70] == pytest.approx(0.03 * math.sqrt(365), rel=0.05)


def test_every_view_has_a_q_and_a_pandas_definition():
    assert set(P) == set(Q)


# ------------------------------------------------------------------ end to end
def _write(folder, table, rows):
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(folder / f"{table}.jsonl.gz", "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def synthetic_recording(root, minutes=12):
    """A feed recording of a one-expiry market whose forward drifts, every 20 seconds."""
    from tests.test_engine import synthetic_market
    t0 = 1_791_500_000 * NS
    rng = np.random.default_rng(3)
    day = root / "2026.10.08"
    quotes, spot, snap, trades, dq = [], [], [], [], []
    F = 82_000.0
    ref = None
    for j in range(minutes * 3):
        t = t0 + j * 20 * NS
        F *= math.exp(rng.normal(0, 0.0002))   # ~0.15% a minute, a realistic BTC drift
        r_, s_, q_, _ = synthetic_market(t0, F=F)
        ref = ref or [dict(x, asset="BTC") for x in r_]
        quotes += [dict(q, asset="BTC", exch=t, recv=t + 5_000_000) for q in q_]
        snap += [dict(s, asset="BTC", exch=t, recv=t, markiv=50.0, mark=0.01, idx=F, oi=10.0, vol=1.0) for s in s_]
        spot.append({"sym": "btc_usd", "asset": "BTC", "exch": t, "recv": t, "price": F - 30})
        trades.append({"sym": q_[j % len(q_)]["sym"], "asset": "BTC", "exch": t, "recv": t, "price": 0.01,
                       "size": 1.0, "side": "buy" if j % 2 else "sell", "iv": 50.0, "idx": F, "tradeid": j})
    for m in range(minutes):
        dq.append({"sym": "BTC", "asset": "BTC", "minute": t0 + m * 60 * NS, "quotes": 100, "trades": 3,
                   "crossed": 0, "onesided": 1, "symsupdated": 60, "latmed": 200.0, "latmax": 900.0})
    for t, rows in (("ref", ref), ("quote", quotes), ("snap", snap), ("spot", spot), ("trade", trades),
                    ("dq", dq), ("gap", [])):
        _write(day, t, rows)
    return root


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    from dashboard import bundle
    from dashboard.sources import ReplaySource
    root = synthetic_recording(tmp_path_factory.mktemp("rec"))
    rng = np.random.default_rng(1)
    hist = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=500, freq="D"),
                         "spot": 60000 * np.exp(np.cumsum(rng.normal(0, 0.02, 500))),
                         "dvol": 50 * np.exp(np.cumsum(rng.normal(0, 0.03, 500)))})
    T = bundle.from_recording(root, risk_every_s=20, hist=hist, log=lambda *_: None)
    out = tmp_path_factory.mktemp("bundle")
    bundle.save(T, out)
    return ReplaySource(out, warmup_min=0), hist


def test_bundle_has_every_table(replay):
    from dashboard.bundle import TABLES
    src, _ = replay
    assert set(src.T) == set(TABLES)
    for t in ("quote", "iv", "surface", "fwd", "risk", "scen", "pnl", "pos", "vares"):
        assert len(src.T[t]) > 0, t
        assert src.T[t]["time"].dtype == "datetime64[ns]"


def test_all_views_analytics_and_figures_render(replay):
    src, hist = replay
    now = src.t_end
    v = src.views(list(P), now=now)
    ex = A.expiries(v["latest_surface"], now)
    assert len(ex) == 1
    e = ex["sym"].iloc[0]
    sm = A.smile(v, e, now)
    assert len(sm["points"]) > 10 and np.all(np.isfinite(sm["af"]))
    ch = A.chain(v, e, now)
    assert len(ch) > 10 and ch["model"].notna().all()
    assert np.nanmedian(np.abs(ch["rich"])) < 1.0               # quotes sit around the fitted smile
    figs = [
        FG.smile(sm), FG.density(sm), FG.surface3d(A.surface_grid(v, now)),
        FG.term_structure(A.term_structure(v, now), A.headline(v, now)["atm30"]),
        FG.skew_terms(A.term_structure(v, now)), FG.carry(A.term_structure(v, now)),
        FG.rr_bf_history(A.smile_history(v, e), e),
        FG.realised_vs_implied(A.realised_intraday(v), A.atm30_history(v)), FG.vrp(A.vrp_history(hist)),
        FG.open_interest(A.open_interest(v, e), e), FG.traded_by_strike(A.trade_flow(v)["by_strike"]),
        FG.scenario_heatmap(A.scenario_matrix(v), 0.0), FG.waterfall(A.pnl_waterfall(v)),
        FG.pnl_path(A.pnl_path(v)), FG.dq_quotes(v["dq"]), FG.latency(v["dq"], v["lat1m"]),
    ]
    for f in figs:
        f.to_dict()                                              # every figure serialises
    tiles = A.risk_tiles(v)
    assert np.isfinite(tiles["mtm"]) and np.isfinite(tiles["var99_fhs"])
    w = A.pnl_waterfall(v)
    assert w["usd"].sum() == pytest.approx(w["actual"].iloc[0], abs=1e-6)   # terms add up to actual
    pos = A.positions(v)
    assert len(pos) == len(v["pos"]) and pos["delta"].notna().all()


def test_scenario_matrix_puts_the_largest_vol_shift_on_top(replay):
    src, _ = replay
    m = A.scenario_matrix(src.views(["scen_last"], now=src.t_end))
    assert list(m.index) == sorted(m.index)                       # heatmap draws row 0 at the bottom


def test_app_builds(replay):
    from dashboard.app import create_app
    src, hist = replay
    app = create_app(src, hist)
    assert app.layout is not None


def test_live_source_over_the_wire_matches_replay(replay):
    """The live path end to end: the kdb+ worker process sends every view's q query over kdb+'s
    wire protocol (to a stand-in gateway) and converts the answers; they must equal the replay's."""
    from dashboard.sources import GatewaySource
    from tests.fake_kdb import ServerProcess
    src, _ = replay
    day = src.t_end.normalize()
    tables = {t: df[df["time"] >= day] for t, df in src.tables_at(src.t_end).items()}   # one day, like an RDB
    gw = ServerProcess("gw", tables)
    live = GatewaySource(gw.port)
    try:
        got = live.views(list(P))
        assert not got.get("_errors"), got.get("_errors")
        want = {n: f(tables) for n, f in P.items()}
        for n in P:
            g, w = got[n], want[n].reset_index(drop=True)
            assert list(g.columns) == list(w.columns), n
            assert len(g) == len(w), n
            if len(w) and "time" in w:
                # the RDB keeps a time of day; the live source dates it today (UTC), the replay's day
                # is the synthetic one, so compare the time of day
                tod = lambda x: x - x.dt.normalize()                     # noqa: E731
                assert (tod(g["time"]) == tod(w["time"])).all(), f"{n}: time of day not preserved"
                assert (g["time"].dt.normalize() == pd.Timestamp.now("UTC").tz_localize(None).normalize()).all()
        assert (got["latest_iv"]["sym"] == want["latest_iv"]["sym"].to_numpy()).all()
    finally:
        live.close()
        gw.stop()
