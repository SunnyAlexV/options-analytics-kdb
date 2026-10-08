#!/usr/bin/env bash
# Show which processes are up, and where their logs are.
#   bash scripts/status.sh
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/config.env"

printf '%-6s %-6s %-8s %-6s %s\n' PART STATE PID PORT LOG
for p in tp hdb rdb gw feed; do
  case $p in tp) port=$TP_PORT;; hdb) port=$HDB_PORT;; rdb) port=$RDB_PORT;; gw) port=$GW_PORT;; feed) port=-;; esac
  f="$RUN/$p.pid"
  if [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null; then state=UP; pid=$(cat "$f"); else state=down; pid=-; fi
  printf '%-6s %-6s %-8s %-6s %s\n' "$p" "$state" "$pid" "$port" "$LOGS/$p.log"
done
echo "Tickerplant logs: $(ls "$TPLOG" 2>/dev/null | tr '\n' ' ')"
echo "HDB dates:        $(ls -d "$HDB"/[0-9]* 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')"
