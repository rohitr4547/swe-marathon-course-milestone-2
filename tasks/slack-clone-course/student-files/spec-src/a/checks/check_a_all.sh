#!/usr/bin/env bash
# Runs every component A rule (A0 A1 A2 A3 A4), then one RESULT line. Exit 0 only if every rule passes.
# Usage: bash /spec/a/checks/check_a_all.sh [A0 A1 A2 A3 A4]
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/lib.sh"
exec python3 "$D/a_rules.py" all "$@"
