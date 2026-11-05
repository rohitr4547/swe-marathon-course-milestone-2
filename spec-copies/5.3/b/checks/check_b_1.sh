#!/usr/bin/env bash
# Check for B.1 (B0 B1): prints its rule results, then a RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/b/checks/check_b_1.sh [B0 B1]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/b_rules.py" 1 "$@"
