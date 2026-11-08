#!/bin/sh
set -eu
mkdir -p /app/data
if command -v redis-server >/dev/null 2>&1; then
  redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/app/data/redis.log 2>&1 &
  redis_pid=$!
fi
python3 /app/server.py --port 8000 --node 0 & p0=$!
python3 /app/server.py --port 8001 --node 1 & p1=$!
python3 /app/server.py --port 8002 --node 2 & p2=$!
python3 /app/irc.py & pi=$!
trap 'kill "$p0" "$p1" "$p2" "$pi" ${redis_pid:-} 2>/dev/null || true; exit 0' INT TERM
while :; do
  if ! kill -0 "$p0" 2>/dev/null; then python3 /app/server.py --port 8000 --node 0 & p0=$!; fi
  if ! kill -0 "$p1" 2>/dev/null; then python3 /app/server.py --port 8001 --node 1 & p1=$!; fi
  if ! kill -0 "$p2" 2>/dev/null; then python3 /app/server.py --port 8002 --node 2 & p2=$!; fi
  if ! kill -0 "$pi" 2>/dev/null; then python3 /app/irc.py & pi=$!; fi
  if ! kill -0 "${redis_pid:-0}" 2>/dev/null && command -v redis-server >/dev/null 2>&1; then redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/app/data/redis.log 2>&1 & redis_pid=$!; fi
  sleep 1
done
