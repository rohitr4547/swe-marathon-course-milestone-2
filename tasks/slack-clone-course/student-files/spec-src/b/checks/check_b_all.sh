#!/usr/bin/env bash
# Runs every component B rule (B0 B1 B2 B3 B4 B5), then one RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/b/checks/check_b_all.sh [B0 B1 B2 B3 B4 B5]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/b_rules.py" all "$@"
