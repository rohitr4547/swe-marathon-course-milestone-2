#!/bin/sh
set -eu
mkdir -p /app/data
start_redis() {
  if ! redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; then
    redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no >/app/redis.log 2>&1 &
    echo $! >/app/redis.pid
  fi
}
start_node() {
  p="$1"; file="/app/pid-$p"
  if [ -f "$file" ] && kill -0 "$(cat "$file")" 2>/dev/null; then return; fi
  PORT="$p" /opt/venv/bin/python /app/server.py >>"/app/node-$p.log" 2>&1 &
  echo $! >"$file"
}
start_irc() {
  if [ -f /app/pid-irc ] && kill -0 "$(cat /app/pid-irc)" 2>/dev/null; then return; fi
  PORT=6667 /opt/venv/bin/python /app/server.py >>/app/irc.log 2>&1 &
  echo $! >/app/pid-irc
}
trap 'for f in /app/pid-8000 /app/pid-8001 /app/pid-8002 /app/pid-irc /app/redis.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true; done' EXIT INT TERM
while :; do
  start_redis
  start_node 8000
  start_node 8001
  start_node 8002
  start_irc
  sleep 1
done
