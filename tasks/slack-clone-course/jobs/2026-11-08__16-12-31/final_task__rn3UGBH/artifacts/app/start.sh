#!/bin/sh
mkdir -p /app/data
if command -v redis-server >/dev/null 2>&1; then
  (while :; do
    if ! ss -ltnH 'sport = :6379' 2>/dev/null | grep -q LISTEN; then
      redis-server --bind 127.0.0.1 --port 6379 >/tmp/redis.log 2>&1 &
    fi
    sleep 1
  done) &
fi
python /app/irc.py >/tmp/irc.log 2>&1 &
while :; do
  for n in 0 1 2; do
    port=$((8000+n))
    if ! ss -ltnH "sport = :$port" 2>/dev/null | grep -q LISTEN; then
      PORT=$port NODE_ID=$n python /app/server.py >/tmp/huddle$n.log 2>&1 &
    fi
  done
  sleep 1
done
