/ Historical database: past days on disk, one folder per date.
/ Usage:  q hdb.q /path/to/hdb -p 5012

/ Vanilla kdb+tick starts the HDB as `q /path/to/hdb`. This wrapper does the
/ same, plus two things:
/   1. starts cleanly on day one, when the directory is still empty
/   2. schema evolution: when a new table is added (Phase 4 added iv, fwd and
/      surface), older date folders do not contain it, and queries across those
/      dates would fail. .Q.chk (KX's standard tool) adds an empty copy of every
/      missing table to every date folder, using the newest date as the template.
/      It runs at start-up and on every end-of-day reload.

/ At each end of day the RDB writes a new date folder and sends this process
/ the string "\l ." to reload. The handler below intercepts exactly that message
/ to run .Q.chk first; every other query is evaluated as normal (value).

.hdb.reload:{@[.Q.chk;`:.;{-1"HDB: .Q.chk skipped (",x,")"}]; @[system;"l .";{-1"HDB: nothing to load yet (",x,")"}]};
.z.pg:{$[x~"\\l .";.hdb.reload[];value x]};

system"cd ",first .z.x;
.hdb.reload[];
-1"HDB ready: ",$[`date in key`.;string[count date]," dates";"empty"];
