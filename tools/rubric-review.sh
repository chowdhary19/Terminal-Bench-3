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
python3 - "$V" <<'PY'
import json, sys
doc = json.JSONDecoder().raw_decode(open(sys.argv[1]).read().lstrip())[0]
checks = doc.get("checks", doc)
fails = {k: v for k, v in checks.items() if str(v.get("outcome", "")).lower() == "fail"}
for k, v in sorted(checks.items()):
    o = str(v.get("outcome", "?")).lower()
    mark = {"pass": "ok  ", "fail": "FAIL", "not_applicable": "n/a "}.get(o, o)
    print(f"  {mark}  {k}")
print(f"\n{len(checks) - len(fails)}/{len(checks)} passed")
for k, v in fails.items():
    print(f"\nFAIL {k}:\n  {v.get('explanation','')[:600]}")
sys.exit(1 if fails else 0)
PY
rc=$?
echo; echo "(verdicts kept at $V)"
exit $rc
