#!/usr/bin/env bash
# Run from any directory. Does not contact or modify a database.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PYTHON=${PYTHON:-python3}
"$PYTHON" scripts/verify.py
"$PYTHON" figures/plot_ldbc_sf10.py --from-csv
"$PYTHON" figures/plot_aeong_workloads.py --from-csv
echo "Appendix plots regenerated under extended-experiments/figures/"
