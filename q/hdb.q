/ Historical database: past days on disk, one folder per date.
/ Usage:  q hdb.q /path/to/hdb -p 5012

/ Vanilla kdb+tick starts the HDB as `q /path/to/hdb`. This wrapper does the
/ same, but also starts cleanly on day one when the directory is still empty.
/ At each end of day the RDB writes a new date folder and sends this process
/ "\l ." to reload, which picks up the new date.

system"cd ",first .z.x;
@[system;"l .";{-1"HDB: nothing to load yet (",x,")"}];
-1"HDB ready: ",$[`date in key`.;string[count date]," dates";"empty"];
