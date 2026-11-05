#!/usr/bin/env bash
# Regression suite for Specs A and B: reruns every A and B rule and prints only what broke, then a total.
# Usage: bash /spec/checks/regression.sh      Exit 0 only if nothing broke.
FEEDBACK=verbose
export FEEDBACK CHECK_MODE=regression
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$D/regression_rules.py" "$@"
