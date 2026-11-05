#!/usr/bin/env bash
# Runs every component E rule (E0 E1 E2 E3 E4 E5 E6), then one RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/e/checks/check_e_all.sh [E0 E1 E2 E3 E4 E5 E6]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/e_rules.py" all "$@"
