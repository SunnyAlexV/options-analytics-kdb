/ Gateway: the single entry point for queries. Clients (the dashboard, the calc
/ engine, you at a q prompt) never need to know whether data is in memory
/ (RDB, today) or on disk (HDB, earlier days).
/ Usage:  q gw.q :5011 :5012 -p 5013

/ Example from any q session:
/   h:hopen 5013
/   h(`.gw.get; `quote; .z.D-1; .z.D; `BTC-25DEC26-80000-C)   / yesterday + today, one option
/   h(`.gw.get; `spot;  .z.D;   .z.D; `)                      / today, all syms
/   h(`.gw.latest; `quote)                                     / latest quote per sym

args:.z.x,(count .z.x)_(":5011";":5012");
/ Connection addresses need TWO colons: `::5011 means "port 5011 on this
/ machine". With one colon, `:5011 is a FILE called 5011, and hopen would
/ happily open that file and append our queries to it.
.gw.addr:`rdb`hdb!`$":",/:2#args;
.gw.h:`rdb`hdb!0N 0Ni;                        / connection handles, null = not connected

/ Connect on first use and after a disconnect, so the gateway survives an
/ RDB or HDB restart without being restarted itself.
.gw.conn:{[p] if[null .gw.h p; .gw.h[p]:@[hopen;.gw.addr p;0Ni]];
  if[null .gw.h p; '"gateway: ",string[p]," unavailable"]; .gw.h p};
.z.pc:{[x] .gw.h[where .gw.h=x]:0Ni};         / a connection closed: forget it

/ Run on the HDB: rows for a date range. The date constraint comes first,
/ so q reads only the date folders it needs (partition pruning).
/ ?[table;where;by;columns] is q's "functional select": the same as
/ select ... from t where ..., but it accepts the table *name* as a symbol,
/ which is what we have here. Each where-clause is a parse tree (op;col;value).
.gw.hq:{[t;sd;ed;s] c:enlist(within;`date;sd,ed);
  if[not s~`; c,:enlist(in;`sym;enlist s)];
  ?[t;c;0b;()]};

/ Run on the RDB: today's rows, with a date column added to match the HDB.
.gw.rq:{[t;s] r:value t; r:$[s~`;r;select from r where sym in s];
  `date xcols update date:.z.D from r};

/ get[table; start date; end date; syms (` = all)]
.gw.get:{[t;sd;ed;s]
  d:.z.D; r:();
  if[sd<d; r,:enlist .gw.conn[`hdb](.gw.hq;t;sd;ed&d-1;s)];
  if[ed>=d; r,:enlist .gw.conn[`rdb](.gw.rq;t;s)];
  $[count r; raze r; ()]};

/ latest[table]: most recent row per sym, today
.gw.latest:{[t] .gw.conn[`rdb]({select by sym from value x};t)};

/ rdbq / hdbq: run a prepared query (a string) on the RDB or HDB. The dashboard
/ (dashboard/sources.py) sends its queries this way, so summaries such as a
/ median per minute are computed in q, next to the data, and only small results
/ travel back. This runs any q expression: fine on this machine, but a shared
/ gateway would restrict it to a list of named queries.
.gw.rdbq:{[q] .gw.conn[`rdb] q};
.gw.hdbq:{[q] .gw.conn[`hdb] q};

-1"Gateway ready: rdb ",string[.gw.addr`rdb],", hdb ",string .gw.addr`hdb;
