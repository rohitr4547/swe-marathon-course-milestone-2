#!/usr/bin/env bash
# Runs the A and B regression suite (the Run 5.1 copy, verbose) against one app in a fresh container.
# Usage: analysis/regression_app.sh <app dir>      Prints each REGRESSION FAIL line and the final count.
set -u
APP="$(cd "$1" && pwd)"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="m2regress$$"
docker run -d --name "$NAME" -v "$REPO/spec-copies/5.1":/spec:ro m2-checktest >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
docker cp "$APP/." "$NAME":/app/
docker exec "$NAME" bash -c 'rm -rf /app/data; pip install -q -r /app/requirements.txt >/dev/null 2>&1 || { echo "PIP INSTALL FAILED: results invalid"; exit 1; }; bash /spec/common/restart.sh | tail -1' || exit 1
docker exec "$NAME" bash -c 'bash /spec/checks/regression.sh' | grep -E '^REGRESSION'
