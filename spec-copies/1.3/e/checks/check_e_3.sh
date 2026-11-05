#!/usr/bin/env bash
# Check for E.3 (E3): prints its rule results, then a RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/e/checks/check_e_3.sh [E3]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/e_rules.py" 3 "$@"
