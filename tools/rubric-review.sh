#!/usr/bin/env bash
# Local replica of the CI's blocking "Task Implementation Rubric Review"
# (.github/workflows/review.yml): the 35-criterion repo rubric via harbor exec,
# reviewed on sonnet, exactly as the gate runs it.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK="${1:-tasks/desk-position-reconcile}"
cd "$REPO"
[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || { echo "export the token first (same shell as the trials)"; exit 1; }
STAGE=$(mktemp -d); WORK=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir "$STAGE/task-under-review"
cp -R "$REPO/$TASK" "$STAGE/task-under-review/"
cp "$REPO/rubrics/task-implementation.toml" "$STAGE/rubric.toml"
cd "$WORK"
uvx --python 3.12 --from harbor==0.18.0 harbor exec \
    -p "$STAGE/task-under-review" \
    -p "$STAGE/rubric.toml" \
    --instruction-path "$REPO/tools/rubric-regression/templates/instruction.md" \
    -f /app/verdicts.json \
    --image ubuntu:24.04 \
    -a claude-code -m sonnet \
    --ae CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
    --job-name rubric-review || true
V=$(find jobs/rubric-review -path '*/artifacts/app/verdicts.json' 2>/dev/null | head -1 || true)
if [ -z "$V" ]; then echo "NO verdicts.json produced — review did not complete (CI would fail this run and it would be retried)"; exit 2; fi
python3 - "$V" "$REPO/rubrics/task-implementation.toml" <<'PY'
import json, sys
doc = json.JSONDecoder().raw_decode(open(sys.argv[1]).read().lstrip())[0]
checks = doc.get("checks", doc)
outcomes = {k: str(v.get("outcome", "?")).lower() for k, v in checks.items()}
fails = {k: v for k, v in checks.items() if outcomes[k] == "fail"}
na = [k for k, o in outcomes.items() if o == "not_applicable"]
passed = [k for k, o in outcomes.items() if o == "pass"]
for k in sorted(checks):
    mark = {"pass": "ok  ", "fail": "FAIL", "not_applicable": "n/a "}.get(outcomes[k], outcomes[k])
    print(f"  {mark}  {k}")
print(f"\n{len(passed)} pass, {len(na)} not applicable, {len(fails)} fail, of {len(checks)} criteria")
if na: print("  not applicable: " + ", ".join(sorted(na)))
# CI asserts the reviewer covered every criterion; do the same so a short
# review cannot read as a clean sweep.
import tomllib
rubric = tomllib.load(open(sys.argv[2], "rb")) if len(sys.argv) > 2 else None
if rubric:
    missing = [c["name"] for c in rubric["criteria"] if c["name"] not in checks]
    if missing:
        print(f"\n  MISSING VERDICTS for {len(missing)} criteria: {missing}")
        fails = fails or {"_missing": 1}
for k, v in fails.items():
    print(f"\nFAIL {k}:\n  {v.get('explanation','')[:600]}")
sys.exit(1 if fails else 0)
PY
rc=$?
mkdir -p "$REPO/results/rubric"
cp "$V" "$REPO/results/rubric/verdicts.json" 2>/dev/null || true
echo; echo "(verdicts written to results/rubric/verdicts.json)"
exit $rc
