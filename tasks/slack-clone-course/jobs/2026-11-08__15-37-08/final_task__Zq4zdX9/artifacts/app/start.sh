#!/bin/sh
set -eu
mkdir -p /app
redis-server --bind 127.0.0.1 --port 6379 --save "" --appendonly no >/tmp/huddle-redis.log 2>&1 &
REDIS_PID=$!
python3 /app/irc.py >/tmp/huddle-irc.log 2>&1 &
IRC_PID=$!
cleanup(){ kill "$REDIS_PID" "$IRC_PID" "$P0" "$P1" "$P2" 2>/dev/null || true; }
trap cleanup TERM INT EXIT
start_node(){
  i="$1"; port=$((8000+i))
  PORT="$port" NODE_ID="$i" python3 /app/server.py >/tmp/huddle-node-$i.log 2>&1 &
  echo $!
}
P0=$(start_node 0); P1=$(start_node 1); P2=$(start_node 2)
while :; do
  kill -0 "$REDIS_PID" 2>/dev/null || REDIS_PID=$(redis-server --bind 127.0.0.1 --port 6379 --save "" --appendonly no >/tmp/huddle-redis.log 2>&1 & echo $!)
  kill -0 "$P0" 2>/dev/null || P0=$(start_node 0)
  kill -0 "$P1" 2>/dev/null || P1=$(start_node 1)
  kill -0 "$P2" 2>/dev/null || P2=$(start_node 2)
  sleep 1
done
