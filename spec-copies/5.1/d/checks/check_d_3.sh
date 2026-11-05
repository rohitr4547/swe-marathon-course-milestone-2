#!/usr/bin/env bash
# Check for D.3 (D3): prints its rule results, then a RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/d/checks/check_d_3.sh [D3]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/d_rules.py" 3 "$@"
