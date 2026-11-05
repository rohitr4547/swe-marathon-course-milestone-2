#!/usr/bin/env bash
# Check for E.2 (E2): prints its rule results, then a RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/e/checks/check_e_2.sh [E2]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/e_rules.py" 2 "$@"
