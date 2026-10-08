# Phase 2 walkthrough: the kdb+ core on KX's standard kdb+tick

Phase 2 builds the system that captures and stores every update:

- a **tickerplant** that receives and logs every update,
- a **real-time database (RDB)** holding today's data in memory,
- a **historical database (HDB)** holding past days on disk, and
- a **gateway** that answers queries across both.

The first three are **KX's own standard scripts**, used exactly as KX publishes them. Many firms' kdb+ systems are built on these three files, so understanding them line by line is what kdb+ interviews test.

## 1. What runs, and in what order

| Process | Port | Script | Whose code |
|---|---|---|---|
| Tickerplant | 5010 | `q/tick.q` + `q/tick/u.q` | KX, unmodified |
| HDB | 5012 | `q/hdb.q` | ours (4 lines) |
| RDB | 5011 | `q/rdb.q` → loads `q/tick/r.q` | KX's r.q, plus 1 line of ours |
| Gateway | 5013 | `q/gw.q` | ours |
| Feed | — | `python -m feed --tp` | ours (Phase 1, plus `TickerplantSink`) |

There are two supporting files:
- `q/tick/sym.q` holds the table schemas. It's **generated** from `feed/schema.py` by `scripts/gen_schema.py`.
- `config.env` holds the ports and folders. Every q process runs with `TZ=UTC`, so "today" and end of day are in UTC.

**Why are KX's files downloaded rather than committed?** Their repository has no licence file. Without a licence we have no clear right to republish their code, but KX's README explicitly invites you to download it into your own version control. `scripts/get_kdb_tick.sh` fetches the three files at one pinned commit and checks their SHA-256 hashes, so everyone runs byte-identical code.

## 2. The conventions KX's tickerplant enforces

`tick.q` refuses to start unless **every table's first two columns are `time` and `sym`**. A unit test now checks this too.

- **`time`**: if the incoming data doesn't start with a timespan, the tickerplant stamps every row with its own clock (`.z.P`, cast to time of day). The feed therefore never sends `time`.
- **`sym`**: subscribers can filter on it. At end of day, each table is **sorted by `sym`** and given the **parted attribute** (`p#`). That makes "all quotes for this option on this date" a single contiguous read from disk.

That gives every quote three timestamps: Deribit's (`exch`), our feed's (`recv`), and the tickerplant's (`time`). The latency of every hop is measurable.

One schema decision was forced by how kdb+ stores data. **Symbols are stored once in a global `sym` file**, and every symbol column holds small integers pointing into it. A column with millions of distinct values, such as trade ids, would make that file grow without limit. So `tradeid` is a long, not a symbol. This is a classic kdb+ interview question.

## 3. tick.q, function by function

Open `q/tick.q` next to this section. After running `get_kdb_tick.sh`, it's in your project folder.

**Start-up:** `q tick.q sym /home/sunny/kdbdata/tplog -p 5010`
- `sym` loads `tick/sym.q` (our schemas).
- The second argument is the folder where the log is written.

**`.u.tick[src;dst]`** runs once at start:
1. `init[]` (from u.q) builds `.u.w`, the subscriber registry: for each table, a list of (connection handle; syms wanted).
2. It checks every table starts with `time`, `sym`.
3. It applies the grouped attribute `g#` to `sym`, for fast lookups by sym intraday.
4. It sets `.u.d` to today's date.
5. It opens today's log file with `.u.ld`.

**`.u.ld[date]`** opens the log for a date, e.g. `tplog/sym2026.10.08`:
- It creates the file if it doesn't exist.
- `-11!(-2;L)` **counts the messages already in the log**. This is how a restarted tickerplant carries on today's log rather than overwriting it. If the log is corrupt, it says how many messages are valid and exits rather than guessing.

**`.u.upd[table;data]`** is the function our feed calls. We run in **zero-latency mode** (no `-t` flag), so each call:
1. Checks whether the date has changed: `ts"d"$a:.z.P`. That would trigger end of day.
2. Puts the time column in front if the first column isn't a timespan.
3. **Publishes** the rows to every subscriber of that table (`pub`).
4. **Appends** `(`upd;table;data)` to the log file and increments `.u.i`, the message count.

An honest note on ordering: the vanilla tickerplant **publishes, then logs** (its change log says "2006.07.24 pub then log"). The gap between the two is microseconds within one function call, but if the tickerplant crashed exactly there, a subscriber could hold a message the log doesn't. Some firms modify this to log first. We use KX's version unchanged and document the trade-off.

**`.u.ts` and `.u.endofday`**: a timer checks once a second whether the date has moved on. If it has:
1. `end d` sends `.u.end[date]` to every subscriber.
2. The date advances.
3. Yesterday's log is closed and a new one opened.

## 4. u.q, the publish/subscribe layer (about 15 lines)

| Function | What it does |
|---|---|
| `init` | Builds `.u.w`: table → list of (handle; syms) |
| `sub[table;syms]` | Called by a subscriber. `` ` `` means all tables or all syms. Registers the caller (`.z.w`, its connection handle) and returns the table's **empty schema**, so the subscriber knows the columns. |
| `pub[table;data]` | For each subscriber of that table: filter to the syms it wants, then send **asynchronously**: `(neg handle)(`upd;table;rows)`. A negative handle means "send without waiting for a reply", so a slow subscriber can't slow the tickerplant. |
| `.z.pc` | Runs when any connection closes, and removes that subscriber. |
| `end[date]` | Sends `.u.end[date]` to every subscriber. |

## 5. r.q, the real-time database (about 10 lines)

At start-up, r.q makes **one call** to the tickerplant:

```q
(.u.sub[`;`]; `.u `i`L)
```

That single call means: "subscribe me to everything, **and** tell me how many messages are in today's log (`.u.i`) and where the log is (`.u.L`)."

Because both happen in the same call, nothing can arrive in between. The RDB then:
1. sets up the empty tables,
2. **replays exactly `.u.i` messages from the log** with `-11!`, calling `upd` (which is just `insert`) for each one, and
3. receives live updates from that point on.

The result: no gaps and no duplicates. This is the whole crash-recovery mechanism, and the system test checks it by killing the RDB.

**`.u.end[date]`**, at midnight UTC, calls `.Q.hdpf`, which:
1. writes each table to `<hdb>/<date>/<table>/`, sorted by `sym` with `p#`,
2. empties the in-memory tables, and
3. tells the HDB process `\l .`, so it reloads and sees the new date.

**Our one added line:** r.q assumes the HDB lives in the same folder as the tickerplant log. Ours doesn't, so `rdb.q` changes directory to the HDB folder after loading r.q. r.q's own comment says to do exactly this: `/ HARDCODE \cd if other than logdir/db`.

## 6. The gateway (`q/gw.q`, ours)

```q
h:hopen 5013
h(`.gw.get; `quote; .z.D-3; .z.D; `BTC-25DEC26-80000-C)   / last 3 days + today, one option
h(`.gw.latest; `quote)                                     / latest quote per option, today
```

`.gw.get` splits the date range:
- dates **before today** go to the HDB, with the date constraint first, so q reads only those date folders (**partition pruning**);
- **today** goes to the RDB, with a `date` column added so the two halves line up.

The results are joined with `raze`.

The gateway reconnects by itself, so restarting the RDB or HDB doesn't require restarting the gateway.

One new piece of q here, the **functional select**: `?[t; c; 0b; ()]` is the same as `select from t where …`, but it accepts the table *name* as a symbol and the conditions as parse trees `(op; column; value)`. That's how you build queries programmatically.

## 7. Bugs caught while building it

- **Background-process PIDs.** In `( cd dir && nohup q … & )`, the `&` puts the whole `cd && nohup` chain in the background. So `$!` was the PID of a wrapper shell, and `stop.sh` killed the wrapper while q kept running, still holding its port. The restarted process then failed to bind, yet the script reported "started". It was caught by testing the scripts with a stand-in for q, and fixed by making `cd` a separate statement. `start.sh` also now refuses to start a process on a port that's already taken.
- **Trade ids as symbols** would have grown the `sym` file forever (section 2).
- **A lone `/` comments out the rest of a script.** In q, a line holding only `/` *opens a block comment*, which runs until a line holding only `\`. I used lone `/` lines as spacers in the comment headers of `rdb.q`, `hdb.q` and `gw.q`. q therefore ignored everything below them, including `\l tick/r.q`, and the RDB started with no tables. There was no error message, because nothing went wrong from q's point of view: it was all commentary. It was found on the first real run, by starting the RDB in the foreground and seeing a bare `q)` prompt with no "RDB ready" line. A unit test now scans our q files for lone `/` lines.
- **One colon or two.** `` hopen `::5011 `` connects to port 5011 on this machine, but `` hopen `:5011 `` opens a **file** called `5011`. The gateway built its addresses with one colon. So "query the RDB" quietly meant "append the query to a file and return the file handle". Every gateway answer had a count of 1, and a stray 157-byte file called `q/6011` appeared in the project. KX's `r.q` gets this right with `` `$":",.u.x 0 ``, which turns `":5010"` into `` `::5010 ``. The system test caught it.

## 8. Run it

In the Ubuntu terminal, from the project folder:

```bash
bash scripts/get_kdb_tick.sh
```
Run this once. It should show three `ok` lines.

```bash
pytest
```
You should see `11 passed`.

```bash
python scripts/system_test.py
```
This takes about 3 minutes, and every line should say PASS. It runs a separate copy of the system on ports 6010–6013, with data in `~/kdbdata-test`, so it never touches the real system. It checks:
- the schemas,
- live data flow,
- **crash recovery** (it kills the RDB),
- the gateway,
- a forced end of day.

Then start the real system:

```bash
bash scripts/start.sh
bash scripts/status.sh
```

Query it from a q session:

```q
q)h:hopen 5011                                   / connect to the RDB
q)h"count each value each tables[]"              / rows per table
q)h"select n:count i by sym from quote"          / quotes per option
q)h"select from quote where sym=`BTC-25DEC26-80000-C"
q)h"exec 1e-6*med `long$time-`timespan$recv from quote"   / median feed -> tickerplant delay, ms
q)g:hopen 5013                                   / the gateway
q)g(`.gw.latest;`quote)
```

Note the last RDB query: `time` is time of day (timespan) and `recv` is a full timestamp, so `` `timespan$recv `` takes its time of day before subtracting. Near midnight that gives a misleading result. Can you see why?

```bash
bash scripts/stop.sh
```
Data is kept. Tomorrow, `start.sh` carries on, and at midnight UTC today's data moves to the HDB.

## 9. Questions

1. The RDB replays `.u.i` messages from the log at start-up. Why must it subscribe **and** read `.u.i` in the same call? What could go wrong if it did them in two separate calls?
2. `pub` sends to subscribers with a **negative** handle. What would happen to the feed if the RDB froze and the tickerplant sent synchronously?
3. Our RDB holds all of today in memory: about 25 million quotes by midnight. At what point would you add an **intraday write-down**, and what would it change in `r.q`?
4. Why does `.gw.get` put the date condition first, not the sym condition?
