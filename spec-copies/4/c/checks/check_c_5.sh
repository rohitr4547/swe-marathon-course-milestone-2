#!/usr/bin/env bash
# Check for C.5 (C5): prints its rule results, then a RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/c/checks/check_c_5.sh [C5]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/c_rules.py" 5 "$@"
