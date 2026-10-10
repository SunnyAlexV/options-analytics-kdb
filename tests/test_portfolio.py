"""Joint VaR / ES across coins (risk/portfolio.py): the identities it must satisfy, on synthetic
smiles and synthetic histories where the right answer is known."""
import math

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pricing", reason="build the C++ module first: pip install -e .")

from risk import portfolio as PF  # noqa: E402
from risk import var as varmod  # noqa: E402
from risk.book import sample_book  # noqa: E402
from risk.history import index_name, vol_source  # noqa: E402
from risk.market import Market  # noqa: E402
from risk.rows import port_rows  # noqa: E402
from feed.schema import columns  # noqa: E402
from tests.test_risk import NOW, surface_rows  # noqa: E402


def coin_market(coin: str, F: float) -> Market:
    """The test smiles, relabelled for another coin and its price level."""
    rows = surface_rows(F=F)
    for r in rows:
        r["sym"] = r["sym"].replace("BTC", coin)
    return Market.from_surface(rows, asof=NOW, spot=F)


def history(spot0, n, seed, shocks=None, vol_seed=None):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.03, n) if shocks is None else shocks
    v = np.random.default_rng(vol_seed if vol_seed is not None else seed + 99).normal(0, 0.04, n)
    return pd.DataFrame({"date": pd.date_range("2024-01-01", periods=n, freq="D"),
                         "spot": spot0 * np.exp(np.cumsum(r)), "dvol": 50 * np.exp(np.cumsum(v))})


@pytest.fixture(scope="module")
def two_coins():
    mk = {"BTC": coin_market("BTC", 80_000.0), "SOL_USDC": coin_market("SOL_USDC", 110.0)}
    books = {"BTC": sample_book(mk["BTC"], hedge_rule_R=0.0),
             "SOL_USDC": sample_book(mk["SOL_USDC"], hedge_rule_R=0.0, scale=80_000 / 110)}
    rng = np.random.default_rng(1)
    common = rng.normal(0, 0.025, 500)                       # a shared crypto factor ...
    hists = {"BTC": history(80_000, 500, 2, shocks=common + rng.normal(0, 0.01, 500)),
             "SOL_USDC": history(110, 500, 3, shocks=1.3 * common + rng.normal(0, 0.02, 500))}
    return books, mk, hists


def test_history_mapping():
    assert index_name("BTC") == "btc_usd" and index_name("SOL_USDC") == "sol_usdc"
    assert vol_source("ETH") == ("ETH", False) and vol_source("ETH_USDC") == ("ETH", False)
    assert vol_source("BTC_USDC") == ("BTC", False)
    assert vol_source("XRP_USDC") == ("BTC", True)              # no DVOL for XRP: BTC's stands in


def test_coin_book_has_the_btc_books_usd_size_and_coin_strikes():
    btc = sample_book(coin_market("BTC", 80_000.0), hedge_rule_R=0.0)
    trx = sample_book(coin_market("TRX_USDC", 0.33), hedge_rule_R=0.0, scale=80_000 / 0.33)
    for b, t in zip(btc[:-1], trx[:-1]):                      # options: same USD notional per leg
        assert t["qty"] * 0.33 == pytest.approx(b["qty"] * 80_000, rel=1e-9)
    assert all("d" in r["sym"].split("-")[2] for r in trx[:-1])   # decimal strikes written 0d33
    assert trx[-1]["kind"] == "future"                        # the (synthetic) forward hedge


def test_contributions_add_up_to_the_joint_es(two_coins):
    rep = PF.compute(*two_coins, R=0.0, window=365)
    for method in ("hs", "fhs"):
        s = rep["methods"][method]["joint"]
        assert sum(s["contrib_es975"].values()) == pytest.approx(s["joint"]["es975"], rel=1e-12)
        assert s["div_es975"] >= -1e-9                       # ES is subadditive on common scenarios
        assert s["n"] == 365


def test_single_coin_numbers_equal_the_standalone_var(two_coins):
    books, mk, hists = two_coins
    rep = PF.compute(books, mk, hists, R=0.0, window=365)
    for a in books:
        ref = varmod.compute(books[a], mk[a], hists[a], R=0.0, window=365)
        for method in ("hs", "fhs"):
            alone = rep["methods"][method]["joint"]["alone"][a]
            row = ref[ref["method"] == method].iloc[0]
            assert alone["var99"] == pytest.approx(row["var99"], rel=1e-12)
            assert alone["es975"] == pytest.approx(row["es975"], rel=1e-12)


def test_identical_coins_have_no_diversification():
    """Two copies of the same book on the same history: the joint numbers are exactly twice each."""
    m = coin_market("BTC", 80_000.0)
    m2 = coin_market("BTC_USDC", 80_000.0)
    b1 = sample_book(m, hedge_rule_R=0.0)
    b2 = [dict(r, sym=r["sym"].replace("BTC", "BTC_USDC")) for r in b1]
    h = history(80_000, 450, 7)
    rep = PF.compute({"BTC": b1, "BTC_USDC": b2}, {"BTC": m, "BTC_USDC": m2}, {"BTC": h, "BTC_USDC": h.copy()},
                     R=0.0, window=365)
    s = rep["methods"]["hs"]["joint"]
    assert s["joint"]["es975"] == pytest.approx(2 * s["alone"]["BTC"]["es975"], rel=1e-12)
    assert s["div_es975"] == pytest.approx(0.0, abs=1e-6 * s["joint"]["es975"])
    assert rep["corr"].loc["BTC", "BTC_USDC"] == pytest.approx(1.0)


def test_short_history_coin_joins_only_the_all_variant(two_coins):
    books, mk, hists = two_coins
    mk = dict(mk, HYPE_USDC=coin_market("HYPE_USDC", 40.0))
    books = dict(books, HYPE_USDC=sample_book(mk["HYPE_USDC"], hedge_rule_R=0.0, scale=2000))
    hists = dict(hists, HYPE_USDC=history(40, 131, 11).assign(
        date=pd.date_range(end=hists["BTC"]["date"].iloc[-1], periods=131, freq="D")))
    rep = PF.compute(books, mk, hists, R=0.0, window=365)
    assert rep["main"] == ["BTC", "SOL_USDC"] and rep["short"] == ["HYPE_USDC"]
    allv = rep["methods"]["hs"]["allcoins"]
    assert allv["n"] == 130 and set(allv["alone"]) == {"BTC", "SOL_USDC", "HYPE_USDC"}
    assert "HYPE_USDC" not in rep["methods"]["hs"]["joint"]["alone"]


def test_stress_scales_only_the_proxied_coins_vol_moves(two_coins):
    books, mk, hists = two_coins
    base = PF.compute(books, mk, hists, R=0.0, window=365)
    none = PF.compute(books, mk, hists, R=0.0, window=365, proxied=())
    sol = PF.compute(books, mk, hists, R=0.0, window=365, proxied=("SOL_USDC",))
    s0 = none["methods"]["hs"]
    assert s0["stress"]["joint"]["es975"] == pytest.approx(s0["joint"]["joint"]["es975"])   # nothing proxied
    s1 = sol["methods"]["hs"]["stress"]["alone"]
    assert s1["BTC"]["es975"] == pytest.approx(base["methods"]["hs"]["joint"]["alone"]["BTC"]["es975"])
    assert s1["SOL_USDC"]["es975"] != pytest.approx(base["methods"]["hs"]["joint"]["alone"]["SOL_USDC"]["es975"])


def test_joint_backtest_and_rows(two_coins):
    books, mk, hists = two_coins
    rep = PF.compute(books, mk, hists, R=0.0, window=365)
    bt = PF.backtest(books, mk, hists, 0.0, rep["main"], window=365, test_days=60)
    assert bt["hs"]["days"] == 60 and 0 <= bt["hs"]["kupiec_p"] <= 1
    rows = port_rows(rep, bt)
    assert all(list(r) == columns("port") for r in rows)
    m = {(r["sym"], r["method"], r["metric"], r["asset"]): r["val"] for r in rows}
    assert m[("joint", "fhs", "es975", "ALL")] == pytest.approx(
        sum(v for (s, me, k, a), v in m.items() if s == "joint" and me == "fhs" and k == "contrib_es975"))
    assert m[("joint", "hs", "btdays", "ALL")] == 60
    assert sum(1 for r in rows if r["sym"] == "corr") == 4      # 2 x 2 matrix


def test_risk_process_books_every_coin_and_publishes_the_portfolio(two_coins):
    """The live process, driven directly: two coins' smiles arrive, each gets its own sample book
    (the SOL one sized to the BTC book's USD notional), and the VaR step publishes per-coin
    vares rows plus the joint port rows."""
    import argparse
    import time as _time
    pytest.importorskip("pykx")
    from risk.__main__ import RiskProcess
    from tests.test_risk import surface_rows as srows

    class Sink:
        def __init__(self):
            self.rows = {}

        def publish(self, t, rows):
            self.rows.setdefault(t, []).extend(rows)

        def close(self):
            pass

    _, _, hists = two_coins
    args = argparse.Namespace(tp_port=1, rdb_port=1, assets=["ALL"], currency=None, R=0.0, book=None,
                              data="/nonexistent", risk_every=0.0, scen_every=0.0, pnl_every=0.0,
                              var_every=0.0, var_window=365)
    p = RiskProcess(args)
    p.out = Sink()
    now = _time.time_ns()
    for coin, F in (("BTC", 80_000.0), ("SOL_USDC", 110.0)):
        rows = []
        for r in srows(now=now, F=F):
            T = (r["expiry"] - now) / (365 * 86400e9)
            rows.append(dict(r, sym=r["sym"].replace("BTC", coin), asset=coin, expiry=now + int(T * 365 * 86400e9),
                             T=T, afa=0.18 * T, afb=0.6 * T))
        p.handle("surface", rows)
        p.handle("spot", [{"sym": coin.lower(), "asset": coin, "price": F}])
    p.hist, p.hist_day = hists, _time.strftime("%Y-%m-%d", _time.gmtime())
    p.hist_assets = set(hists)
    for c in p.coins.values():
        c.first_seen = _time.monotonic() - 120             # ready: no waiting for more expiries
    p.tick()
    books = {a: c.book for a, c in p.coins.items()}
    assert set(books) == {"BTC", "SOL_USDC"} and all(books.values())
    btc_opt, sol_opt = books["BTC"][0], books["SOL_USDC"][0]
    assert sol_opt["qty"] * 110 == pytest.approx(btc_opt["qty"] * 80_000)
    pub = p.out.rows
    assert {r["asset"] for r in pub["pos"]} == {"BTC", "SOL_USDC"}
    assert {r["asset"] for r in pub["risk"]} == {"BTC", "SOL_USDC"}
    assert {r["asset"] for r in pub["vares"]} == {"BTC", "SOL_USDC"}
    port = {(r["sym"], r["method"], r["metric"], r["asset"]): r["val"] for r in pub["port"]}
    assert port[("joint", "fhs", "es975", "ALL")] > 0
    assert port[("joint", "fhs", "div_es975", "ALL")] >= 0
