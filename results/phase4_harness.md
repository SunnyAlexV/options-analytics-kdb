# Phase 4 evaluation harness — 9 Oct 2026, ~29 min of live Deribit BTC options data

Reproduce: python -m feed --record --minutes 30, then:
  python scripts/eval_surface.py --recording ~/kdbdata/raw --minutes 14
  python scripts/eval_surface.py --recording ~/kdbdata/raw --start 14 --minutes 15

## First half
```
  refit every 0.1 s: replay 101s

configuration                                    n  med|z|  in band  p90|z|  ATM jit  RR jit  fit ms
Deribit mark (reference)                   145,108   0.154    99.0%    0.48     nanbp    nanbp     nan
baseline (current defaults) [raw]        1,444,460   0.175    96.9%    0.65    0.00bp   0.00bp    34.6
baseline (current defaults) [af]         1,444,460   0.177    96.8%    0.66    0.00bp   0.00bp    34.6
+ quote-size weights [raw]               1,530,380   0.176    97.0%    0.63    0.07bp   0.11bp    35.4
+ quote-size weights [af]                1,530,380   0.178    97.0%    0.64    0.07bp   0.11bp    35.4
Deribit forward only [raw]               1,436,022   0.178    96.8%    0.65    0.00bp   0.00bp    34.9
Deribit forward only [af]                1,436,022   0.180    96.8%    0.66    0.00bp   0.00bp    34.9
parity fwd, gate 0.1% (looser) [raw]     1,497,693   0.155    97.1%    0.62    0.01bp   0.02bp    35.7
parity fwd, gate 0.1% (looser) [af]      1,497,693   0.156    97.1%    0.63    0.01bp   0.02bp    35.7
parity fwd always (no gate) [raw]        1,498,036   0.155    97.1%    0.62    0.01bp   0.02bp    35.1
parity fwd always (no gate) [af]         1,498,036   0.156    97.0%    0.63    0.01bp   0.02bp    35.1
parity always + size weights [raw]       1,544,382   0.156    97.4%    0.59    0.11bp   0.18bp    36.4
parity always + size weights [af]        1,544,382   0.157    97.4%    0.61    0.11bp   0.18bp    36.4
OTM options only (not best side) [raw]   1,431,275   0.174    96.6%    0.65    0.00bp   0.00bp    33.1
OTM options only (not best side) [af]    1,431,275   0.176    96.5%    0.65    0.00bp   0.00bp    33.1
refit every 2 s [raw]                      580,328   0.177    96.8%    0.66    0.22bp   0.73bp    38.9
refit every 2 s [af]                       580,328   0.179    96.7%    0.66    0.22bp   0.73bp    38.9
refit every 0.1 s [raw]                  2,319,293   0.176    96.9%    0.65    0.00bp   0.00bp    31.5
refit every 0.1 s [af]                   2,319,293   0.178    96.8%    0.66    0.00bp   0.00bp    31.5
z = (model price - next quote mid) / that quote's half-spread, over the next 10 s; jitter = median change between successive new fits of the same expiry
```

## Second half
```
  refit every 0.1 s: replay 87s

configuration                                    n  med|z|  in band  p90|z|  ATM jit  RR jit  fit ms
Deribit mark (reference)                    89,597   0.135    99.1%    0.45     nanbp    nanbp     nan
baseline (current defaults) [raw]          783,556   0.165    98.0%    0.57    0.00bp   0.00bp    30.1
baseline (current defaults) [af]           783,556   0.171    97.4%    0.60    0.00bp   0.00bp    30.1
+ quote-size weights [raw]                 824,711   0.165    97.8%    0.56    0.02bp   0.06bp    31.2
+ quote-size weights [af]                  824,711   0.172    97.0%    0.61    0.02bp   0.06bp    31.2
Deribit forward only [raw]                 776,979   0.166    97.9%    0.57    0.00bp   0.00bp    29.6
Deribit forward only [af]                  776,979   0.173    97.4%    0.61    0.00bp   0.00bp    29.6
parity fwd, gate 0.1% (looser) [raw]       795,205   0.147    98.2%    0.54    0.01bp   0.00bp    32.2
parity fwd, gate 0.1% (looser) [af]        795,205   0.152    97.6%    0.58    0.01bp   0.00bp    32.2
parity fwd always (no gate) [raw]          796,575   0.147    98.2%    0.54    0.01bp   0.00bp    32.7
parity fwd always (no gate) [af]           796,575   0.152    97.6%    0.58    0.01bp   0.00bp    32.7
parity always + size weights [raw]         829,315   0.146    97.9%    0.53    0.06bp   0.10bp    34.7
parity always + size weights [af]          829,315   0.153    97.2%    0.57    0.06bp   0.10bp    34.7
OTM options only (not best side) [raw]     771,941   0.163    97.8%    0.57    0.00bp   0.00bp    28.9
OTM options only (not best side) [af]      771,941   0.169    97.1%    0.60    0.00bp   0.00bp    28.9
refit every 2 s [raw]                      337,399   0.167    98.0%    0.58    0.05bp   0.19bp    31.1
refit every 2 s [af]                       337,399   0.173    97.3%    0.61    0.05bp   0.19bp    31.1
refit every 0.1 s [raw]                  1,192,123   0.165    97.9%    0.57    0.00bp   0.00bp    26.2
refit every 0.1 s [af]                   1,192,123   0.171    97.5%    0.60    0.00bp   0.00bp    26.2
z = (model price - next quote mid) / that quote's half-spread, over the next 10 s; jitter = median change between successive new fits of the same expiry
```
