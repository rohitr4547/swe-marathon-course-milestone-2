#!/bin/bash
set -u
mkdir -p /app/data
pids=()
redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/tmp/huddle-redis.log 2>&1 &
redis_pid=$!
trap 'kill "$redis_pid" 2>/dev/null || true; for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; exit 0' INT TERM EXIT
for port in 8000 8001 8002; do
  PORT=$port python3 /app/server.py &
  pids+=($!)
done
python3 /app/irc.py &
pids+=($!)
while true; do
  if ! kill -0 "$redis_pid" 2>/dev/null; then redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/tmp/huddle-redis.log 2>&1 & redis_pid=$!; fi
  for i in "${!pids[@]}"; do
    if ! kill -0 "${pids[$i]}" 2>/dev/null; then
      if [ "$i" -lt 3 ]; then PORT=$((8000+i)) python3 /app/server.py & pids[$i]=$!; else python3 /app/irc.py & pids[$i]=$!; fi
    fi
  done
  sleep 1
done
