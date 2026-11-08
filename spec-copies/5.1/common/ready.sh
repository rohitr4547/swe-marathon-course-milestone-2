#!/usr/bin/env bash
# Submit gate: lists every check whose last complete run failed or came before your latest change to /app,
# and whether all three nodes answer. Prints READY TO SUBMIT only when nothing is left. Takes about a second.
# Usage: bash /spec/common/ready.sh
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -c "import sys; sys.path.insert(0, '$D'); import checklib; sys.exit(0 if checklib.ready() else 1)"
