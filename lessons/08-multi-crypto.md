# Lesson 8: from one coin to nine — ETH and Deribit's USDC options

Up to Phase 6 the system priced one thing: BTC options on Deribit. This lesson extends it to
every option Deribit lists: ETH, and the seven USDC-settled coins (BTC_USDC, ETH_USDC, SOL, XRP,
AVAX, TRX, HYPE). Almost nothing in the kdb+ layer changes, because every table carried an
`asset` column from day one. The work is in three places: **premium conventions**, **routing**,
and **the bugs that only show up on new data**.

## 1. Two premium conventions, one regression

Deribit quotes BTC and ETH options in the coin itself (**inverse**), and the USDC options in USDC
(**linear**). For a call with payoff (S − K)⁺ in USD:

| | premium | value | put-call parity, as a line y = a + bK |
|---|---|---|---|
| inverse (BTC, ETH) | coins | V = D · Black76(F) / F | C − P = D(1 − K/F): a = D, b = −D/F |
| linear (*_USDC, NSE) | USDC / INR | V = D · Black76(F) | C − P = D(F − K): a = DF, b = −D |

In both, **F = −a/b**. So `engine/forward.py` runs the same weighted regression of C − P on K,
and only the reading of the discount factor changes (`D = a` inverse, `D = −b` linear). That
one difference lives in `engine/conventions.py`:

```python
DERIBIT      = Convention("deribit",      "inverse", tick=1e-4, ...)
DERIBIT_USDC = Convention("deribit_usdc", "linear",  tick=5e-5, ...)

def parity_FD(self, a, b):
    return -a / b, (a if self.premium == "inverse" else -b)
```

`to_black` turns a quoted premium into the undiscounted Black value the C++ IV solver wants
(`p·F/D` inverse, `p/D` linear). Nothing else in the engine knows which market it is in.

**Ticks differ per coin.** A TRX option ticks at 0.00005 USDC, a BTC_USDC option at 5 USDC. The
parity regression weights each strike by its bid–ask spread, floored at half a tick, so the engine
now reads each expiry's tick from the `ref` table instead of a global constant.

## 2. Routing: settlement groups

Deribit's websocket groups options by settlement currency: `trades.option.BTC`, `.ETH`, `.USDC`.
So the system runs **one feed and one engine process per group** (`FEED_GROUPS="BTC ETH USDC"`
in `config.env`), and the USDC engine holds one `SurfaceEngine` per coin:

```python
def in_group(asset, group):          # which rows an engine process keeps
    return asset.endswith("_USDC") if group == "USDC" else asset == group
```

The tickerplant sends every row to every subscriber; each engine keeps its own group's. The
index feed follows the same naming: `btc_usd` → asset `BTC`, `sol_usdc` → asset `SOL_USDC`, so the
spot table lines up with the options without a lookup table.

The risk process books one asset (BTC); its bootstrap now filters `where asset=`BTC`, because
`select last price from spot` on a multi-coin spot table returns whichever coin ticked last.

## 3. Bugs only new data could show

**ETH trade ids are strings.** BTC trade ids are numbers (`"407123456"`); ETH and USDC ids carry a
prefix (`"ETH-313151839"`). `int()` raised inside the websocket read loop, the loop treated it as
a dropped connection, and the feed reconnected — losing ~10 seconds of every ETH instrument —
on every single trade. Two fixes: `trade_id()` parses the number after the prefix, and a malformed
message is now counted and skipped, never allowed to cost the connection.

**Lee's moment bound is 2, not 4.** For SVI, total variance grows at most linearly in the
wings, with slope ≤ 2 (Lee 2004): b(1 + |ρ|) ≤ 2. The fit had 4 — the SSVI condition
θφ(1 + |ρ|) ≤ 4, a different parameterisation. BTC's smiles never came near the limit, so the
error was invisible; a sparse 7-day SOL smile hit b(1+|ρ|) ≈ 4 and its wings exploded.

**Delta → strike on a misbehaving smile.** The 25-delta strike solves N(d₁(k)) = 0.25 on the
fitted smile. On a sane smile N(d₁) falls steadily in k; on the exploded SOL fit it turned
around, and interpolating over the whole grid returned the grid edge: a butterfly of +1,691 vol
points. `delta_strike` now walks out from the money and takes the first crossing, or NaN.

## 4. The cross-convention test

Deribit lists BTC and ETH twice: inverse (`ETH-30OCT26-...`) and linear (`ETH_USDC-30OCT26-...`).
Different order books, market makers, conventions, contract sizes and ticks. If the conventions
are right, both must give the same smile. `scripts/cross_convention.py` replays both books on one
clock and compares, minute by minute:

```
expiry            days  pts | ATM inv    lin   diff  <1vp | RR inv    lin   diff  <1vp | BF inv    lin   diff |   F gap
BTC-12OCT26        2.8   26 |   25.60  25.16  +0.49  100% |  -3.87  -3.73  -0.09  100% |  +1.12  +1.03  +0.08 |   +0.3bp
BTC-13OCT26        3.8   26 |   28.50  28.38  +0.15  100% |  -3.17  -2.86  -0.30  100% |  +1.15  +1.08  +0.07 |   -0.2bp
BTC-16OCT26        6.8   26 |   32.18  32.04  +0.13  100% |  -1.80  -1.78  -0.00  100% |  +1.07  +1.05  +0.02 |   +0.3bp
BTC-23OCT26       13.8   26 |   32.76  32.64  +0.11  100% |  -0.64  -0.59  -0.07  100% |  +1.27  +1.20  +0.08 |   +0.4bp
BTC-30OCT26       20.8   26 |   33.36  33.33  +0.03  100% |  -0.76  -0.56  -0.21  100% |  +1.16  +1.09  +0.07 |   +1.7bp
BTC-27NOV26       48.8   26 |   36.77  36.74  +0.03  100% |  -0.80  -0.80  -0.00  100% |  +1.06  +1.06  +0.01 |   +1.7bp
BTC-25DEC26       76.8   26 |   37.32  37.37  -0.04  100% |  -0.88  -0.85  -0.04  100% |  +1.14  +1.11  +0.03 |   +3.3bp
ETH-12OCT26        2.8   26 |   32.69  32.98  -0.33  100% |  -8.11  -8.16  +0.02  100% |  +1.63  +1.54  +0.08 |   +1.0bp
ETH-13OCT26        3.8   26 |   35.85  36.38  -0.57  100% |  -6.39  -6.46  +0.06  100% |  +1.55  +1.42  +0.14 |   +0.9bp
ETH-16OCT26        6.8   26 |   40.79  41.00  -0.20  100% |  -4.86  -4.82  -0.01  100% |  +1.58  +1.54  +0.04 |   +0.3bp
ETH-23OCT26       13.8   26 |   42.60  42.69  -0.10  100% |  -3.12  -3.06  -0.03  100% |  +1.55  +1.51  +0.04 |   +0.2bp
ETH-30OCT26       20.8   26 |   44.30  44.39  -0.13  100% |  -2.32  -2.17  -0.13  100% |  +1.43  +1.40  +0.03 |   -0.4bp
ETH-27NOV26       48.8   26 |   48.59  48.68  -0.09   96% |  -1.85  -1.63  -0.23  100% |  +1.44  +1.51  -0.07 |   +1.5bp
ETH-25DEC26       76.8   26 |   49.59  49.59  -0.00   92% |  -1.18  -0.99  -0.16   92% |  +1.52  +1.53  -0.02 |   +2.3bp

ATM vol, inverse minus linear: median -0.02 vol pts, point-weighted mean |diff| 0.17 vol pts over 14 expiries
```

(25-minute recording of all three Deribit feeds, 9 Oct 2026 12:23–12:48 UTC; one comparison point per minute; expiries under 2 days are not fitted.)

A convention error is not subtle here: reading linear quotes as inverse divides every price by F
(~2,500 for ETH), and the implied vols are nonsense. Agreement to a tenth of a vol point is the
end-to-end check that the forward, the discount factor and the IVs are right for both.

## Try it

```bash
python -m feed --currency USDC --record --minutes 10 --out ~/rec_usdc
python -m feed --currency ETH  --record --minutes 10 --out ~/rec_eth
python scripts/cross_convention.py --rec ~/rec_eth ~/rec_usdc --pairs ETH
pytest tests/test_multi_asset.py tests/test_conventions.py -q
```
