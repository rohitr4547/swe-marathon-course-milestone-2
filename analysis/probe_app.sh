#!/usr/bin/env bash
# Boots one app in a fresh check container and runs analysis/probe_components.py against it.
# Usage: analysis/probe_app.sh <app dir>
set -u
APP="$(cd "$1" && pwd)"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="m2probe$$"
docker run -d --name "$NAME" -v "$REPO/spec-copies/5.3":/spec:ro m2-checktest >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
docker cp "$APP/." "$NAME":/app/
docker cp "$REPO/analysis/probe_components.py" "$NAME":/tmp/probe_components.py
docker exec "$NAME" bash -c 'rm -rf /app/data; pip install -q -r /app/requirements.txt >/dev/null 2>&1; bash /spec/common/restart.sh | tail -1'
docker exec "$NAME" bash -c 'PLAYWRIGHT_BROWSERS_PATH=/ms-playwright python3 /tmp/probe_components.py'
