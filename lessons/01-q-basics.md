# q session 1 — basics on real BTC options

**Time:** about 60–90 minutes.
**Goal:** by the end you will have turned a live Deribit snapshot into a q table and queried it: ATM vol by expiry, open interest, spreads, and USD premiums.

**How to work:**
- Type each line yourself rather than pasting. Your fingers learn q faster than your eyes do.
- Lines starting `q)` are typed at the q prompt (don't type the `q)` itself).
- Text after ` / ` is a comment explaining the line; you can type it or skip it.
- If anything errors, paste the line and the error into the chat.

Start q from the System folder:

```bash
cd /mnt/c/Users/Sunny/Desktop/System
q
```

---

## Part A — The one rule: q reads right to left

```q
q)2+3
q)2*3+4          / 14, not 10: 3+4 first, then 2*7
q)(2*3)+4        / 10: brackets override it
```

q has **no operator precedence**. Every expression is evaluated right to left. This is the single most common source of beginner bugs, so check this rule first whenever a result looks wrong.

## Part B — Atoms, lists and types

```q
q)x:1 2 3 4 5    / ':' assigns. A list is just values separated by spaces
q)x*2            / arithmetic works on whole lists at once, no loop needed
q)sum x
q)avg x
q)til 10         / 0 1 2 ... 9
q)x where x>2    / filtering: where returns the positions that are true
q)count x
```

Types:

```q
q)type 1         / -7h  = long (whole number). The minus sign means a single atom
q)type 1 2 3     / 7h   = list of longs (no minus sign)
q)type 1.5       / -9h  = float
q)type `BTC      / -11h = symbol: a short label, like a category
q)type "BTC"     / 10h  = string, i.e. a list of characters
q)0N             / null long
q)0n             / null float (a missing bid will look like this)
q)2026.10.08     / date
q).z.p           / current timestamp, UTC
```

> **Symbols vs strings:** use symbols (`` `BTC ``) for labels you group or filter on, and strings (`"BTC"`) for text you need to cut up. We'll use both shortly.

## Part C — Dictionaries and tables

```q
q)d:`strike`iv!(80000;0.45)   / a dictionary: keys ! values
q)d`iv
q)t:([] sym:`C1`C2`C3; strike:70000 80000 90000f; iv:0.52 0.45 0.48)
q)t
q)meta t                       / column names and types (f = float, s = symbol)
q)count t
```

A q table is a list of named columns, all the same length. This is why q is fast for time series: each column is stored as one contiguous array.

The query language looks like SQL, but it is q:

```q
q)select from t where strike>75000
q)select avg iv from t
q)select from t where iv=min iv
q)update m:strike%80000 from t    / % is division in q ( / is for comments)
q)t                               / t is unchanged: update returned a new table
```

## Part D — Functions

```q
q)sq:{x*x}                  / x, y, z are the default argument names
q)sq 4
q)sq 1 2 3                  / works on lists automatically
q)lm:{[k;f] log k%f}        / named arguments in [ ]; log is the natural log
q)lm[90000;80000]           / log-moneyness of a 90k strike against an 80k forward
```

`lm` is the **log-moneyness** k = ln(K/F), the x-axis of the volatility smile. We'll use it again for the SVI fit.

Leave q for now:

```q
q)\\
```

---

## Part E — Load a live snapshot

In the Ubuntu terminal:

```bash
python scripts/snapshot.py
```

This downloads every BTC option on Deribit (about 950) and saves them to `~/kdbdata/btc_snap.csv`. Open the file in VS Code to see what q is about to read: one row per option, blank cells where there is no bid.

Back into q:

```bash
q
```

```q
q)raw:("PSFFFFFFFF";enlist ",") 0: `:/home/sunny/kdbdata/btc_snap.csv
q)count raw
q)5#raw
q)meta raw
```

What the load line means:
- `0:` reads a text file.
- `"PSFFFFFFFF"` gives one type letter per column: P = timestamp, S = symbol, F = float.
- `enlist ","` says the separator is a comma and the first row is the column names.
- `` `:/home/... `` is a file handle: a symbol starting with a colon.

The columns are:

| Column | Meaning |
|---|---|
| `bid`, `ask`, `mark` | Option prices **in BTC** |
| `markiv` | Deribit's implied vol, in vol points |
| `und` | The forward price for that expiry, in USD |
| `idx` | The BTC index (spot) price |
| `oi` | Open interest, in contracts |
| `vol` | 24-hour volume, in contracts |

## Part F — Parse the instrument name

An instrument name like `` `BTC-27NOV26-88000-C `` packs four fields into one symbol. Cut it up:

```q
q)p:"-" vs/: string raw`sym     / string turns symbols into text; vs splits on "-"
q)3#p                           / first 3 results: ("BTC";"27NOV26";"88000";"C")
```

`/:` means **each-right**: apply `"-" vs` to every string in the list on its right. q has a small family of these adverbs (`'` each, `/:` each-right, `\:` each-left), and they replace almost all loops.

Turn `"27NOV26"` into a date. Deribit writes single-digit days without a zero, e.g. `"9OCT26"`, so read from the right:

```q
q)mon:`JAN`FEB`MAR`APR`MAY`JUN`JUL`AUG`SEP`OCT`NOV`DEC!1+til 12
q)pad:{-2#"0",string x}         / 9 -> "09", 11 -> "11"
q)toDate:{[s] "D"$"20",(-2#s),".",(pad mon[`$3#-5#s]),".",pad "I"$-5_s}
q)toDate "27NOV26"
q)toDate "9OCT26"
```

Reading `toDate` right to left:

| Piece | What it does |
|---|---|
| `-5_s` | Drop the last 5 characters → the day text, `"27"` |
| `"I"$` | Parse it as an integer |
| `3#-5#s` | Take the last 5 characters, then the first 3 of those → the month, `"NOV"` |
| `` `$ `` | Turn the text into a symbol, so it can look up `mon` |
| `-2#s` | Take the last 2 characters → the year, `"26"` |
| `"D"$` | Parse `"2026.11.27"` as a date |

Build the real table:

```q
q)quote:update expiry:toDate each p[;1], strike:"F"$p[;2], cp:`$p[;3] from raw
q)5#quote
q)meta quote
```

`p[;1]` means "element 1 of every item in p", i.e. all the expiry strings.

## Part G — Ask the market questions

How many options per expiry?

```q
q)select n:count i by expiry from quote
```

`i` is the row number, so `count i` counts rows. `by` groups, like SQL's GROUP BY.

Add mid-price, spread and USD values. The backtick before `quote` means "update the table in place":

```q
q)update mid:0.5*bid+ask, spread:ask-bid from `quote
q)update midusd:mid*und from `quote
q)select sym, mid, und, midusd from quote where expiry=min expiry, cp=`C
```

**Inverse options:** prices are in BTC, so the USD value is the BTC price times the underlying. Every pricing step later has to respect this.

Where is the liquidity? List the 10 options with the most open interest:

```q
q)10#`oi xdesc quote
```

Which options have no bid at all?

```q
q)select sym, ask, markiv from quote where null bid
q)select n:count i by expiry from quote where null bid
```

What do you notice about which expiries and strikes these are?

ATM implied vol by expiry, using the call whose strike is closest to the forward:

```q
q)select atmiv:markiv first iasc abs strike-und, fwd:first und by expiry from quote where cp=`C
```

Right to left: `strike-und` gives each strike's distance from the forward; `abs` removes the sign; `iasc` lists positions from nearest to furthest; `first` takes the nearest; and `markiv[...]` picks that option's vol. This result is the **ATM term structure**, the first surface metric in the plan.

Look at one smile:

```q
q)select strike, cp, markiv from quote where expiry=2026.12.25, cp=`C
```

Do vols rise or fall as strikes go up? That shape is what SVI will fit.

---

## Exercises — try these before looking anything up

1. What fraction of all options have no bid? (Hint: `avg null quote`bid` — why does `avg` give a fraction here?)
2. Which expiry has the highest total open interest? (`sum oi` … `by expiry`, then sort.)
3. Add a column `k` = log-moneyness ln(strike/und), using your `lm` function or `log`.
4. For the 25 Dec 2026 expiry, which strike has the widest spread **as a percentage of mid**? Is it deep in the money or far out of the money, and why would that be?
5. Compare the forward price `und` across expiries. It rises with expiry; use it to compute the implied annualised basis per expiry. You'll need the time to expiry in years, and Deribit options expire at 08:00 UTC.

Paste your answers (the q you wrote and the output) into the chat, and we'll go through them before the first commit.

## Cheat sheet

| q | Meaning |
|---|---|
| `x:5` | Assign |
| `#` | Take: `3#x` (first 3), `-3#x` (last 3) |
| `_` | Drop: `2_x` (drop first 2) |
| `til n` | 0 … n−1 |
| `where` | Positions where true |
| `%` | Divide |
| `/` | Comment (when it follows a space) |
| `'` | Each |
| `/:` | Each-right |
| `\:` | Each-left |
| `` `s `` | Symbol |
| `"s"` | String |
| `0N`, `0n` | Null long, null float |
| `select … by … from … where …` | Query |
| `` update … from `t `` | Update the table in place |
| `` `col xasc t `` | Sort ascending |
| `` `col xdesc t `` | Sort descending |
| `meta t` | Column types |
| `\\` | Quit q |
