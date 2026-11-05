#!/usr/bin/env bash
# Check for D.1 (D0 D1): prints its rule results, then a RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/d/checks/check_d_1.sh [D0 D1]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/d_rules.py" 1 "$@"
