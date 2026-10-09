"""Stand-in kdb+ processes for tests, speaking kdb+'s real IPC wire protocol.

A real q process isn't available in CI (KDB-X licence) or in every dev environment,
so these servers let the engine's networking be tested for real: handshake, message
framing, sync/async message types, and kdb+tick's .u.sub / upd conventions.

Wire protocol (what these implement):
  - handshake: client sends "user:pass\\3\\0"; server replies with one capability byte
  - each message: 8-byte header [endian=1, type (0 async, 1 sync, 2 response), 0, 0,
    total length int32 little-endian] followed by a serialised q object
"""
from __future__ import annotations

import os
import socket
import threading
import time

os.environ.setdefault("PYKX_UNLICENSED", "true")
import pandas as pd  # noqa: E402
import pykx as kx  # noqa: E402


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def _send(sock, obj, msg_type):
    sock.sendall(bytes(kx.serialize(kx.toq(obj) if not isinstance(obj, kx.K) else obj, wait=msg_type).copy()))


def _text(x) -> str:
    v = x.py() if isinstance(x, kx.K) else x
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


class FakeServer:
    """Base: accepts connections, handshakes, dispatches each message to on_message."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.clients = []
        self.lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        hs = b""
        while not hs.endswith(b"\x00"):
            hs += c.recv(1)
        c.sendall(b"\x03")                                   # capability byte: protocol version 3
        with self.lock:
            self.clients.append(c)
        try:
            while True:
                head = _recv_exact(c, 8)
                body = _recv_exact(c, int.from_bytes(head[4:8], "little") - 8)
                msg = kx.deserialize(head + body)
                reply = self.on_message(c, msg, head[1])
                if head[1] == 1:                             # sync: the client waits for a response
                    _send(c, reply if reply is not None else kx.toq(None), 2)
        except (ConnectionError, OSError):
            pass

    def on_message(self, conn, msg, msg_type):
        raise NotImplementedError

    def close(self):
        self.sock.close()


class FakeTickerplant(FakeServer):
    """Answers .u.sub like kdb+tick's u.q, records .u.upd calls, and can publish upd messages."""

    def __init__(self, schemas: dict[str, pd.DataFrame]):
        self.schemas = schemas
        self.subscribers: dict[str, list] = {}
        self.published: dict[str, list] = {}
        super().__init__()

    def on_message(self, conn, msg, msg_type):
        parts = list(msg) if isinstance(msg, kx.List) else [msg]   # unlicensed PyKX: iterate, don't index
        fn = _text(parts[0])
        if fn.startswith(".u.sub[;`] each "):                # one call subscribing to several tables
            tabs = [t for t in fn.split("each ", 1)[1].split("`") if t]
            replies = []
            for t in tabs:
                self.subscribers.setdefault(t, []).append(conn)
                replies.append([kx.SymbolAtom(t), kx.toq(self.schemas[t].iloc[:0])])
            return replies
        if fn == ".u.sub":                                   # (".u.sub"; `table; `syms)
            t = _text(parts[1])
            self.subscribers.setdefault(t, []).append(conn)
            return [kx.SymbolAtom(t), kx.toq(self.schemas[t].iloc[:0])]   # (name; empty schema)
        if fn == ".u.upd":                                   # (".u.upd"; `table; columns)
            t = _text(parts[1])
            self.published.setdefault(t, []).append([list(c.py()) for c in parts[2]])
            return None
        return None

    def publish(self, table: str, df: pd.DataFrame):
        """Send (`upd; `table; rows) asynchronously to every subscriber, like u.q's pub."""
        for c in self.subscribers.get(table, []):
            _send(c, [kx.SymbolAtom("upd"), kx.SymbolAtom(table), kx.toq(df)], 0)

    def wait_for(self, table: str, timeout: float = 20.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.published.get(table):
                return True
            time.sleep(0.05)
        return False


class FakeRDB(FakeServer):
    """Answers the handful of queries the engine sends an RDB."""

    def __init__(self, tables: dict[str, pd.DataFrame]):
        self.tables = tables
        super().__init__()

    def on_message(self, conn, msg, msg_type):
        if isinstance(msg, kx.List):                         # ("{select from t where i within (x;y-1)}"; a; b)
            parts = list(msg)
            q = _text(parts[0])
            t = q.split("select from ")[1].split()[0]
            a, b = int(parts[1].py()), int(parts[2].py())
            return kx.toq(self.tables[t].iloc[a:b].reset_index(drop=True))
        q = _text(msg)
        if " where i within " in q:                          # "select from t where i within a b"
            t = q.split("select from ")[1].split()[0]
            a, b = map(int, q.split(" within ")[1].split())
            return kx.toq(self.tables[t].iloc[a:b + 1].reset_index(drop=True))
        if q.startswith("count "):
            return kx.LongAtom(len(self.tables[q.split()[1]]))
        if "select by sym from" in q:                        # latest row per sym
            t = q.split("from ")[1].split()[0]
            return kx.toq(self.tables[t].groupby("sym", sort=False).tail(1).reset_index(drop=True))
        raise ValueError(f"FakeRDB: unexpected query {q!r}")


# ---------------------------------------------------------------- run in a separate process
# PyKX's connect is a C call that holds Python's GIL while it waits for the server's
# handshake reply. A fake server running as a thread in the same process could then
# never reply (deadlock), so each fake server runs in its own process -- exactly as a
# real q process would. The test talks to it through queues.
import multiprocessing as mp  # noqa: E402


def _child(kind, tables, cmd_q, out_q):
    srv = FakeTickerplant(tables) if kind == "tp" else FakeRDB(tables)
    out_q.put(("port", srv.port))
    while True:
        cmd = cmd_q.get()
        if cmd[0] == "publish":
            srv.publish(cmd[1], cmd[2])
        elif cmd[0] == "set_table":
            srv.tables[cmd[1]] = cmd[2]
        elif cmd[0] == "state":
            subs = sorted(getattr(srv, "subscribers", {}))
            pub = {t: [len(cols[0]) for cols in msgs] for t, msgs in getattr(srv, "published", {}).items()}
            out_q.put(("state", subs, pub))
        elif cmd[0] == "stop":
            srv.close()
            return


class ServerProcess:
    """A FakeTickerplant ("tp") or FakeRDB ("rdb") running in a child process."""

    def __init__(self, kind: str, tables: dict[str, pd.DataFrame]):
        ctx = mp.get_context("spawn")
        self.cmd_q, self.out_q = ctx.Queue(), ctx.Queue()
        self.proc = ctx.Process(target=_child, args=(kind, tables, self.cmd_q, self.out_q), daemon=True)
        self.proc.start()
        tag, self.port = self.out_q.get(timeout=60)

    def publish(self, table, df):
        self.cmd_q.put(("publish", table, df))

    def set_table(self, table, df):
        self.cmd_q.put(("set_table", table, df))

    def state(self):
        self.cmd_q.put(("state",))
        _, subs, pub = self.out_q.get(timeout=10)
        return subs, pub

    def wait_for(self, check, timeout: float = 30.0):
        end = time.time() + timeout
        while time.time() < end:
            subs, pub = self.state()
            if check(subs, pub):
                return subs, pub
            time.sleep(0.2)
        return self.state()

    def stop(self):
        self.cmd_q.put(("stop",))
        self.proc.join(timeout=5)
        if self.proc.is_alive():
            self.proc.terminate()
