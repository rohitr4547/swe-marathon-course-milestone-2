#!/usr/bin/env bash
# Runs every component D rule (D0 D1 D2 D3), then one RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/d/checks/check_d_all.sh [D0 D1 D2 D3]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/d_rules.py" all "$@"
