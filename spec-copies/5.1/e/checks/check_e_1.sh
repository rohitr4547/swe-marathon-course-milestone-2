#!/usr/bin/env bash
# Check for E.1 (E0 E1): prints its rule results, then a RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/e/checks/check_e_1.sh [E0 E1]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/e_rules.py" 1 "$@"
