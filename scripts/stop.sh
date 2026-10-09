#!/usr/bin/env bash
# Stop the system, or chosen parts of it (reverse start order by default).
#   bash scripts/stop.sh            # everything
#   bash scripts/stop.sh rdb        # just the RDB (it recovers from the tickerplant log on restart)
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/config.env"

parts=("$@"); [ ${#parts[@]} -eq 0 ] && parts=(engine feed gw rdb hdb tp)
for p in "${parts[@]}"; do
  f="$RUN/$p.pid"
  if [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null; then
    pid=$(cat "$f")
    kill "$pid"
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"      # still alive after 5 s: force it
    echo "  stopped $p (pid $pid)"
  else
    echo "  $p not running"
  fi
  rm -f "$f"
done
