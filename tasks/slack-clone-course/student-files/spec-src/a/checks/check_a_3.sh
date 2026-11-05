#!/usr/bin/env bash
# Check for A.3 (A3): prints its rule results, then a RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/a/checks/check_a_3.sh [A3]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/a_rules.py" 3 "$@"
