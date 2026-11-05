#!/usr/bin/env bash
# Check for D.2 (D2): prints its rule results, then a RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/d/checks/check_d_2.sh [D2]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/d_rules.py" 2 "$@"
