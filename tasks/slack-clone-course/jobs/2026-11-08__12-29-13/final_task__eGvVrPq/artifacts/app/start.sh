#!/bin/bash
cd /app
mkdir -p /app/data/files
python3 /app/core.py --init-db
(while true; do redis-server --port 6379 --bind 127.0.0.1 --save '' --appendonly no; sleep 1; done) &
for n in 0 1 2; do (while true; do python3 /app/server.py --node $n; sleep 1; done) & done
wait
