#!/usr/bin/env bash
# Runs every component C rule (C0 C1 C2 C3 C4 C5), then one RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/c/checks/check_c_all.sh [C0 C1 C2 C3 C4 C5]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/c_rules.py" all "$@"
