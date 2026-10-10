"""Write results/phase8b_portfolio.md: the joint VaR / ES across every coin's book, from a bundle.

    python scripts/portfolio_report.py --bundle streamlit_app/bundle_all
    python scripts/portfolio_report.py --gw 5013 --date 2026.10.10          # from the live system

Every number in the report is read from the `port` and `vares` tables the risk process published
(risk/portfolio.py, risk/var.py), using the same functions as the dashboard's Portfolio page.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard import analytics as A  # noqa: E402
from dashboard import tables as TB  # noqa: E402
from dashboard.sources import P  # noqa: E402


def md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle")
    ap.add_argument("--gw", type=int)
    ap.add_argument("--date")
    ap.add_argument("--out", default=str(ROOT / "results" / "phase8b_portfolio.md"))
    a = ap.parse_args(argv)
    if a.bundle:
        from dashboard.bundle import load
        T = load(a.bundle)
        src = f"bundle `{Path(a.bundle).name}`"
    else:
        from dashboard.bundle import from_gateway
        T = from_gateway(a.gw, a.date)
        src = f"the live system's tables for {a.date}"
    port, vares = T["port"], T["vares"]
    when = port["time"].max()
    pf = {m: A.portfolio({"port_last": P["port_last"](T)}, m) for m in ("hs", "fhs")}
    f = pf["fhs"]
    v = vares[vares["time"] == vares.groupby("asset")["time"].transform("max")]
    own = v.pivot_table(index="asset", columns="method", values=["var99", "es975", "btexc", "btdays", "kupiec"])

    L = [f"# Phase 8b: portfolio VaR across every coin — {when:%d %b %Y %H:%M} UTC", "",
         f"Source: {src}. Reproduce: `python scripts/portfolio_report.py "
         + (f"--bundle {a.bundle}`" if a.bundle else f"--gw {a.gw} --date {a.date}`") + ".", "",
         "Each coin holds the same seven-leg sample book (risk/book.py), sized to the BTC book's USD notional "
         "per leg and delta-hedged. Each of the last 365 days moves every coin by its own move that day "
         "(price index; vol: own DVOL for BTC and ETH and their USDC markets, BTC's DVOL as a proxy for the rest). "
         "Full revaluation in C++; 1-day horizon; USD.", "",
         "## Headline (FHS)", "",
         "| measure | value |", "|---|---|"]
    for label, value, note in TB.portfolio_tiles(f):
        L.append(f"| {label} | {value} ({note}) |")
    L += ["", "## Variants", ""]
    for m in ("fhs", "hs"):
        L += [f"**{m.upper()}**", "", md(TB.text_frame(pf[m]["variants"], TB.PORT_VARIANT_FMT)), ""]
    L += ["## By coin (FHS)", "",
          "Contribution = the coin's average loss on the portfolio's worst 2.5% of days; the contributions add up "
          "to the portfolio's ES 97.5%.", "", md(TB.port_coin_frame(f["contrib"])), "",
          f"Sum of contributions: {f['contrib']['contrib_es975'].sum():,.2f}; portfolio ES 97.5%: {f['es975']:,.2f}.", ""]
    if not own.empty:
        tab = pd.DataFrame({
            "coin": own.index,
            "VaR 99% (FHS)": [f"{x:,.0f}" for x in own[("var99", "fhs")]],
            "ES 97.5% (FHS)": [f"{x:,.0f}" for x in own[("es975", "fhs")]],
            "backtest (FHS)": [f"{e:.0f} / {d:.0f}" if d > 0 else "too little history"
                               for e, d in zip(own[("btexc", "fhs")], own[("btdays", "fhs")])],
            "Kupiec p": [f"{k:.2f}" if np.isfinite(k) else "–" for k in own[("kupiec", "fhs")]],
        })
        L += ["## Each coin's own VaR (vares table)", "", md(tab), ""]
    c = f["corr"]
    if not c.empty:
        off = c.where(~np.eye(len(c), dtype=bool)).stack()
        L += ["## Correlation of daily moves", "",
              f"Off-diagonal correlations over the window range from {off.min():.2f} to {off.max():.2f}; "
              f"median {off.median():.2f}.", "",
              md(c.round(2).reset_index().rename(columns={"asset": ""})), ""]
    Path(a.out).write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
