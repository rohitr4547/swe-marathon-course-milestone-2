#!/bin/bash
set -u
mkdir -p /app/data
redis-server --bind 127.0.0.1 --port 6379 --daemonize yes >/tmp/huddle-redis.log 2>&1 || true
pids=()
cleanup(){ for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM
for port in 8000 8001 8002; do
  PORT="$port" python3 /app/server.py >>"/tmp/huddle-$port.log" 2>&1 &
  pids+=("$!")
done
PORT=6667 python3 /app/irc.py >>/tmp/huddle-irc.log 2>&1 &
pids+=("$!")
while true; do
  sleep 1
  redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1 || redis-server --bind 127.0.0.1 --port 6379 --daemonize yes >/tmp/huddle-redis.log 2>&1 || true
  for i in "${!pids[@]}"; do
    kill -0 "${pids[$i]}" 2>/dev/null || {
      if [ "$i" -lt 3 ]; then
        port=$((8000+i)); PORT="$port" python3 /app/server.py >>"/tmp/huddle-$port.log" 2>&1 &
      else PORT=6667 python3 /app/irc.py >>/tmp/huddle-irc.log 2>&1 & fi
      pids[$i]=$!
    }
  done
done
