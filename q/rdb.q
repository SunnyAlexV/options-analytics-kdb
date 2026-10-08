/ Real-time database: KX's vanilla tick/r.q, plus one line.
/ Usage:  q rdb.q :5010 :5012 /path/to/hdb -p 5011
/   arg 0  tickerplant address      arg 1  historical DB address
/   arg 2  historical DB directory (where end-of-day data is saved)

/ What r.q does on start-up:
/   1. connects to the tickerplant and subscribes to every table (.u.sub)
/   2. replays today's tickerplant log, so a restarted RDB recovers every row
/   3. thereafter inserts each update it is sent (upd:insert)
/   4. at end of day (.u.end) saves its tables to the HDB directory, partitioned
/      by date and sorted by sym, clears memory, and tells the HDB to reload

/ r.q then changes directory to the tickerplant log's folder, because vanilla
/ kdb+tick assumes the HDB lives there. Ours lives in a separate folder, so we
/ change directory again -- exactly what r.q's own comment says to do
/ ("HARDCODE \cd if other than logdir/db").

\l tick/r.q
system"cd ",.z.x 2;
-1"RDB ready: ",(", " sv {string[x],"=",string count value x}each tables`.),"  (hdb dir ",.z.x[2],")";
