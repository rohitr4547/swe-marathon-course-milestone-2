#!/usr/bin/env bash
# Check for A.2 (A2): prints its rule results, then a RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/a/checks/check_a_2.sh [A2]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/a_rules.py" 2 "$@"
