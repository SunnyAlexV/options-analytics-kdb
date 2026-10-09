#!/usr/bin/env bash
# Start the system, or chosen parts of it.
#   bash scripts/start.sh               # everything: tp hdb rdb gw feed engine risk
#   bash scripts/start.sh rdb           # just the RDB (e.g. after a crash)
#   bash scripts/start.sh tp hdb rdb gw # the kdb+ core without the live feed
# Start order matters: the RDB needs the tickerplant and HDB; the gateway needs both DBs.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/config.env"
Q=${Q:-$(command -v q || echo "$HOME/.kx/bin/q")}
PY=${PY:-python}
mkdir -p "$TPLOG" "$HDB" "$LOGS" "$RUN"

if [ ! -f "$ROOT/q/tick.q" ]; then
  echo "KX's kdb+tick scripts are missing. Run:  bash scripts/get_kdb_tick.sh"; exit 1
fi
"$PY" "$ROOT/scripts/gen_schema.py" >/dev/null     # keep q/tick/sym.q in step with feed/schema.py

running() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

wait_port() {   # wait until something is listening on a local port
  for _ in $(seq 1 40); do
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0
    sleep 0.25
  done
  return 1
}

launch() {      # launch <name> <dir> <port or -> <command...>
  local name=$1 dir=$2 port=$3; shift 3
  if running "$name"; then echo "  $name already running (pid $(cat "$RUN/$name.pid"))"; return; fi
  if [ "$port" != "-" ] && (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "  cannot start $name: port $port is already in use (check with: ss -ltnp | grep $port)"; exit 1
  fi
  echo "=== $(date -u '+%F %T') start $name: $*" >>"$LOGS/$name.log"
  # cd first, as its own statement: "cd dir && cmd &" would background the whole
  # chain, and $! would then be a wrapper shell's PID, not the process's.
  ( cd "$dir" || exit 1
    nohup "$@" </dev/null >>"$LOGS/$name.log" 2>&1 &
    echo $! >"$RUN/$name.pid" )
  if [ "$port" != "-" ]; then wait_port "$port" || true; fi
  sleep 1                                   # a process that fails at start-up exits within a second
  if running "$name"; then
    echo "  started $name (pid $(cat "$RUN/$name.pid"))"
  else
    echo "  FAILED to start $name. Last lines of $LOGS/$name.log:"; tail -n 15 "$LOGS/$name.log"; exit 1
  fi
}

parts=("$@"); [ ${#parts[@]} -eq 0 ] && parts=(tp hdb rdb gw feed engine risk)
for p in "${parts[@]}"; do
  case $p in
    tp)   launch tp   "$ROOT/q" "$TP_PORT"  "$Q" tick.q sym "$TPLOG" -p "$TP_PORT" ;;
    hdb)  launch hdb  "$ROOT/q" "$HDB_PORT" "$Q" hdb.q "$HDB" -p "$HDB_PORT" ;;
    rdb)  launch rdb  "$ROOT/q" "$RDB_PORT" "$Q" rdb.q ":$TP_PORT" ":$HDB_PORT" "$HDB" -p "$RDB_PORT" ;;
    gw)   launch gw   "$ROOT/q" "$GW_PORT"  "$Q" gw.q ":$RDB_PORT" ":$HDB_PORT" -p "$GW_PORT" ;;
    feed) launch feed "$ROOT"   -           "$PY" -u -m feed --tp --tp-port "$TP_PORT" --currency "$CURRENCY" ;;
    engine) launch engine "$ROOT" -         "$PY" -u -m engine --mode "$ENGINE_MODE" --tp-port "$TP_PORT" --rdb-port "$RDB_PORT" --currency "$CURRENCY" ;;
    risk) launch risk "$ROOT" -             "$PY" -u -m risk --tp-port "$TP_PORT" --rdb-port "$RDB_PORT" --currency "$CURRENCY" \
                --R "$RISK_R" --pnl-every "$RISK_PNL_EVERY" --data "$DATA" ${RISK_BOOK:+--book "$RISK_BOOK"} ;;
    *) echo "unknown part: $p (use tp hdb rdb gw feed engine risk)"; exit 1 ;;
  esac
done
