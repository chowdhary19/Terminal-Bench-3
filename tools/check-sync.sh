#!/usr/bin/env bash
# The verifier image is built from tests/ alone, so its copies of the reference,
# the generator and the policy cannot be COPYed from the task root. They are
# duplicated instead, and this guard is what stops the duplicates drifting.
set -euo pipefail
t="$(dirname "$0")/../tasks/desk-position-reconcile"
fail=0
check() { if ! diff -q "$1" "$2" >/dev/null; then echo "OUT OF SYNC: $1 vs $2"; fail=1; fi; }
check "$t/tests/verifier_env/reconcile_ref.py"     "$t/solution/files/reconcile.py"
check "$t/tests/verifier_env/generate_inputs.py"   "$t/environment/data/generate_inputs.py"
check "$t/tests/verifier_env/reporting_policy.yaml" "$t/environment/data/reporting_policy.yaml"
[ $fail -eq 0 ] && echo "verifier_env in sync with task sources"
exit $fail
