#!/usr/bin/env bash
# Re-runs every step check of components A to E against one app in a fresh container, and prints the totals.
# Usage: analysis/verify_app.sh <app dir> [components, default abcde]
# Uses the Run 5.3 spec copy (every check, verbose output) and the m2-checktest image (Ubuntu 24.04, /opt/venv,
# redis-server, Playwright Chromium), mirroring the task container. The app's own requirements.txt is installed first.
set -u
APP="$(cd "$1" && pwd)"; COMPS="${2:-abcde}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC="$REPO/spec-copies/5.3"
NAME="m2verify$$"
docker run -d --name "$NAME" -v "$SPEC":/spec:ro m2-checktest >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
docker cp "$APP/." "$NAME":/app/
docker exec "$NAME" bash -c 'rm -rf /app/data; pip install -q -r /app/requirements.txt >/dev/null 2>&1; bash /spec/common/restart.sh | tail -1'
for x in $(echo "$COMPS" | grep -o .); do
  tp=0; tf=0
  for chk in $(docker exec "$NAME" bash -c "ls /spec/$x/checks/check_${x}_*.sh 2>/dev/null | sort -V"); do
    out=$(docker exec "$NAME" bash -c "bash $chk")
    res=$(echo "$out" | grep -o 'RESULT: [0-9]* passed, [0-9]* failed' | tail -1)
    p=$(echo "$res" | grep -o '[0-9]* passed' | grep -o '[0-9]*'); f=$(echo "$res" | grep -o '[0-9]* failed' | grep -o '[0-9]*')
    tp=$((tp + ${p:-0})); tf=$((tf + ${f:-0}))
    echo "  $(basename "$chk"): ${res:-no RESULT line}"
    echo "$out" | grep -E '^(FAIL|NOTE)' | grep -v 'not run' | cut -c1-190 | sed 's/^/      /'
  done
  echo "$(echo "$x" | tr a-e A-E): $tp passed, $tf failed"
done
