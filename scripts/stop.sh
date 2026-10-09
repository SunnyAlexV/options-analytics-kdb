#!/usr/bin/env bash
# Stop the system, or chosen parts of it (reverse start order by default).
#   bash scripts/stop.sh            # everything
#   bash scripts/stop.sh rdb        # just the RDB (it recovers from the tickerplant log on restart)
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/config.env"

parts=("$@"); [ ${#parts[@]} -eq 0 ] && parts=(dash risk engine feed gw rdb hdb tp)
# feed and engine run one process per settlement group: feed_BTC, feed_ETH, ... (plus the
# single-process names of older versions, if one is still running)
expanded=()
for p in "${parts[@]}"; do
  if [ "$p" = feed ] || [ "$p" = engine ]; then
    [ -f "$RUN/$p.pid" ] && expanded+=("$p")
    for g in $FEED_GROUPS; do expanded+=("${p}_$g"); done
  else
    expanded+=("$p")
  fi
done
for p in "${expanded[@]}"; do
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
