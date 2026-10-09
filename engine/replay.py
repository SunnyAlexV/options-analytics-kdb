"""Replay recorded market data through the SurfaceEngine on a simulated clock.

Sources:
  - a feed recording folder (python -m feed --record): <dir>/<YYYY.MM.DD>/{ref,snap,quote}.jsonl.gz
  - the kdb+ gateway (any date range in the HDB/RDB): see load_from_gateway

Events are replayed in the order our feed received them (``recv``), in 100 ms
batches like the live feed, and refit() is called after each batch, so the
engine sees exactly the sequence it would have seen live.
"""
from __future__ import annotations

import json
import zlib
import os
from pathlib import Path

import pandas as pd

from .core import EngineConfig, SurfaceEngine

BATCH_NS = 100_000_000


def _read_jsonl(path: Path) -> list[dict]:
    """Read a gzipped JSON-lines recording, including one still being written.

    A file the feed is still writing has no gzip end marker yet, and its last line may
    be cut off. Decompress what is there and keep every complete line.
    """
    if not path.exists() or path.stat().st_size == 0:
        return []
    raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(path.read_bytes())
    lines = raw.split(b"\n")
    out = []
    for line in lines[:-1] if not raw.endswith(b"\n") else lines:
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass                                   # a partial line at the cut-off point
    return out


def load_recording(folder) -> dict[str, list[dict]]:
    """All days in a recording folder, merged."""
    out = {"ref": [], "snap": [], "quote": []}
    for day in sorted(Path(folder).expanduser().glob("[0-9]*")):
        for t in out:
            out[t] += _read_jsonl(day / f"{t}.jsonl.gz")
    return out


def load_from_gateway(port: int, start_date: str, end_date: str) -> dict[str, list[dict]]:
    """Read ref/snap/quote for a date range through our gateway. Dates as 'YYYY.MM.DD'."""
    os.environ.setdefault("PYKX_UNLICENSED", "true")
    import pykx as kx
    out = {}
    with kx.SyncQConnection(port=port, no_ctx=True) as c:
        for t in ("ref", "snap", "quote"):
            df = c(f"{{.gw.get[`{t};{start_date};{end_date};`]}}", None).pd()
            for col in df.columns:
                if pd.api.types.is_datetime64_any_dtype(df[col]):
                    df[col] = df[col].astype("int64")
                elif pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object:
                    df[col] = df[col].astype(object).where(df[col].notna(), "").map(str)
            out[t] = df.to_dict("records")
    return out


def events(data: dict[str, list[dict]]):
    """(recv_ns, table, row) in receive order. ref/snap without recv are placed first."""
    ev = []
    for t, rows in data.items():
        for r in rows:
            ev.append((int(r.get("recv", 0) or 0), t, r))
    ev.sort(key=lambda e: e[0])
    return ev


def replay(data, cfg: EngineConfig | None = None, on_output=None):
    """Drive a fresh engine through the data. on_output(now_ns, iv_rows, fwd_rows, surf_rows, engine)."""
    eng = SurfaceEngine(cfg)
    ev = events(data)
    i = 0
    while i < len(ev):
        t0 = ev[i][0]
        batch = {"ref": [], "snap": [], "quote": []}
        while i < len(ev) and ev[i][0] < t0 + BATCH_NS:
            batch[ev[i][1]].append(ev[i][2])
            i += 1
        now = max(t0, ev[i - 1][0])
        if batch["ref"]:
            eng.on_ref(batch["ref"])
        if batch["snap"]:
            eng.on_snap(batch["snap"])
        ivs = eng.on_quotes(batch["quote"]) if batch["quote"] else []
        fw, sf = eng.refit(now)
        if on_output:
            on_output(now, ivs, fw, sf, eng)
    return eng
