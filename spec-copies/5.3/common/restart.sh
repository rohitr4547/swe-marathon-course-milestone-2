#!/usr/bin/env bash
# Stops whatever cluster is running, starts /app/start.sh in the background, and waits for all three nodes.
# Usage: bash /spec/common/restart.sh [--fresh]     --fresh also deletes /app/data/huddle.db* (after a schema change).
# Exit 0 only if 8000, 8001 and 8002 all answer GET /api/health within 20 s. Never kills by process name.
set -u
PGID_FILE=/tmp/spec_cluster.pgid

# The cluster this script started last runs in its own process group: killing the group also ends the respawn loops.
if [ -s "$PGID_FILE" ]; then
  kill -9 -- "-$(cat "$PGID_FILE")" 2>/dev/null
fi

# Anything still holding a cluster port was started some other way; kill it and its parent respawn loop, by PID.
for port in 8000 8001 8002 6667 6379; do
  for pid in $(ss -ltnpH "sport = :$port" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
    ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    if [ -n "$ppid" ] && [ "$ppid" -gt 1 ] && [ "$ppid" != "$$" ] && [ "$ppid" != "$PPID" ]; then
      kill -9 "$ppid" 2>/dev/null
    fi
    kill -9 "$pid" 2>/dev/null
  done
done
sleep 1

if [ "${1:-}" = "--fresh" ]; then
  rm -f /app/data/huddle.db /app/data/huddle.db-wal /app/data/huddle.db-shm
  echo "deleted /app/data/huddle.db*"
fi

if [ ! -x /app/start.sh ]; then
  echo "FAIL /app/start.sh is missing or not executable (chmod +x /app/start.sh)"
  exit 1
fi

# setsid gives the cluster its own process group (PGID = this bash's PID) and detaches it from the agent's command.
setsid bash -c 'echo $$ > /tmp/spec_cluster.pgid; exec /app/start.sh' >/tmp/start.log 2>&1 </dev/null &

for _ in $(seq 1 40); do
  up=1
  for p in 8000 8001 8002; do
    curl -fs --max-time 1 "http://127.0.0.1:$p/api/health" >/dev/null 2>&1 || up=0
  done
  if [ "$up" = 1 ]; then
    echo "cluster up: 8000 8001 8002 answer /api/health"
    exit 0
  fi
  sleep 0.5
done
echo "cluster NOT up after 20s. Last lines of /tmp/start.log:"
tail -n 20 /tmp/start.log
exit 1
