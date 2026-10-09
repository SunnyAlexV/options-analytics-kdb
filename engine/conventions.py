"""Market conventions: how an option's quoted premium relates to the Black-76 model.

Two premium conventions cover every market in this project so far:

  inverse  (Deribit BTC/ETH)  premium quoted in the underlying coin. Paying (S-K)+/S coins,
           a call is worth  V_coin = D * Black76(F) / F.   Put-call parity:
               C - P = D (1 - K/F)        a line y = a + b K with  a = D,  b = -D/F
  linear   (NSE, BSE, MCX; USD-margined options elsewhere)  premium paid upfront in the
           quote currency (INR, USD, ...):  V = D * Black76(F).   Put-call parity:
               C - P = D (F - K)          a line y = a + b K with  a = D F,  b = -D
In both, the forward is F = -a/b, so one weighted regression (engine/forward.py) serves both;
only which coefficient is the discount factor D differs.

Everything else that differs by market (time zone, the clock time options stop trading,
tick size) lives here too, so the engine, risk and dashboard code stay market-agnostic.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Convention:
    name: str
    premium: str                 # "inverse" or "linear"
    tick: float                  # option price tick, in premium units (BTC for inverse, INR for NSE)
    quote_ccy: str               # currency of the premium
    tz: str = "UTC"              # exchange time zone (IANA)
    expiry_time: str | None = None   # local clock time trading stops on expiry day, e.g. "15:30"

    # ---- premium <-> undiscounted Black-76 value in the forward's currency
    def to_black(self, premium, F, D):
        p, F, D = (np.asarray(x, dtype=float) for x in (premium, F, D))
        return p * F / D if self.premium == "inverse" else p / D

    def from_black(self, value, F, D):
        v, F, D = (np.asarray(x, dtype=float) for x in (value, F, D))
        return v * D / F if self.premium == "inverse" else v * D

    # ---- put-call parity line y = a + b K  ->  (F, D) and the gradients for standard errors
    def parity_FD(self, a: float, b: float) -> tuple[float, float]:
        F = -a / b
        D = a if self.premium == "inverse" else -b
        return F, D

    def parity_grad_D(self) -> np.ndarray:
        """dD / d(a, b)."""
        return np.array([1.0, 0.0]) if self.premium == "inverse" else np.array([0.0, -1.0])

    # ---- expiry time
    def expiry_ns(self, date: dt.date) -> int:
        """Epoch ns at which an option expiring on `date` stops trading (exchange local time)."""
        from zoneinfo import ZoneInfo
        hh, mm = (int(x) for x in (self.expiry_time or "00:00").split(":"))
        t = dt.datetime(date.year, date.month, date.day, hh, mm, tzinfo=ZoneInfo(self.tz))
        return int(t.timestamp()) * 1_000_000_000


DERIBIT = Convention("deribit", "inverse", tick=1e-4, quote_ccy="coin", tz="UTC", expiry_time="08:00")
# Deribit USDC-settled options (SOL_USDC, BTC_USDC, ...): linear, premium per coin in USDC. Ticks
# differ by coin (0.00005 for TRX ... 5 for BTC), so the engine uses each instrument's own tick.
DERIBIT_USDC = Convention("deribit_usdc", "linear", tick=5e-5, quote_ccy="USDC", tz="UTC", expiry_time="08:00")
# NSE / BSE equity and index options: premium in INR, tick 0.05, trading stops 15:30 IST
NSE = Convention("nse", "linear", tick=0.05, quote_ccy="INR", tz="Asia/Kolkata", expiry_time="15:30")
# NSE / BSE currency options (USDINR, GBPINR, ...): premium in INR, tick 0.0025, stop 12:30 IST
NSE_CCY = Convention("nse_ccy", "linear", tick=0.0025, quote_ccy="INR", tz="Asia/Kolkata", expiry_time="12:30")
# MCX commodity options on futures: premium in INR, trading hours vary by commodity; the
# exchange's own expiry timestamp is used (see feed/upstox.py), tick set per contract
MCX = Convention("mcx", "linear", tick=0.05, quote_ccy="INR", tz="Asia/Kolkata", expiry_time=None)

BY_NAME = {c.name: c for c in (DERIBIT, DERIBIT_USDC, NSE, NSE_CCY, MCX)}


def for_asset(asset: str, segment: str | None = None) -> Convention:
    """The convention of an asset: Deribit BTC/ETH are inverse, Deribit *_USDC are linear,
    Indian segments are linear."""
    if asset in ("BTC", "ETH") and segment in (None, "deribit"):
        return DERIBIT
    if asset.endswith("_USDC") and segment in (None, "deribit"):
        return DERIBIT_USDC
    if segment in ("NCD_FO", "BCD_FO"):
        return NSE_CCY
    if segment in ("MCX_FO", "NSE_COM"):
        return MCX
    return NSE
