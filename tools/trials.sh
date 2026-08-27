#!/usr/bin/env bash
# TB3 trial launcher for desk-position-reconcile.
#
# Reproduces the CI in .github/workflows/run-trials.yml and run-cheat-trials.yml
# against the current TB3 defaults in .github/harbor-run-defaults.yml:
#
#   opus   claude-code  anthropic/claude-opus-5  reasoning_effort=max
#                       CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000
#   codex  codex        openai/gpt-5.6-sol       reasoning_effort=xhigh
#
# Usage:  tools/trials.sh <opus|codex> <run|cheat> [repetitions]
#         tools/trials.sh --parallel N <opus|codex> <run|cheat>
#
# --parallel N runs N trials at once as ONE harbor job (-k N -n N): each trial
# gets its own environment container, its own verifier container and its own
# output directory; nothing is shared between them. On this machine Docker has
# 7.65 GiB, so N=2 is the safe setting; N=3 risks an out-of-memory kill, which
# would void a trial rather than fail it.
#
# In cheat mode the task is copied OUTSIDE the repository before the adversarial
# prompt is appended, exactly as CI does it, so the repository is never mutated.
#
# Secrets: the Claude token is read into this process only. It is never echoed and
# never written to disk. It IS passed to harbor as an --ae argument, which is the
# form the assignment documents, so it is visible in `ps` output for the duration
# of the run. It is unset on exit.
# Codex authentication is left entirely to the codex CLI; this script does not
# read, copy or print it.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK="desk-position-reconcile"
JOBS="${KLAVIS_JOBS:-/tmp/klavis-trials/jobs}"
HARBOR_VER="0.14.0"          # the version CI uses for /run and /cheat
ENV_BACKEND="docker"         # CI default is modal; docker is the local equivalent

cleanup() { unset CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CODE_MAX_OUTPUT_TOKENS 2>/dev/null || true
            [ -n "${CHEAT_DIR:-}" ] && rm -rf "$CHEAT_DIR" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# --summary reads what has already run; --dry-run sets a cheat trial up and
# checks it without spending a trial.
if [ "${1:-}" = "--summary" ]; then
    python3 "$REPO/tools/trial-summary.py" "$JOBS"
    exit $?
fi

DRY=""; PAR=""
while :; do
    case "${1:-}" in
        --dry-run)  DRY=1; shift ;;
        --parallel) [ $# -ge 2 ] || { echo "--parallel needs a number" >&2; exit 2; }; PAR="$2"; shift 2 ;;
        *) break ;;
    esac
done

CONFIG="${1:-}"; MODE="${2:-}"; REPS="${3:-1}"
if [ -n "$PAR" ]; then
    case "$PAR" in ''|*[!0-9]*) echo "--parallel needs a number" >&2; exit 2;; esac
    [ "$PAR" -ge 2 ] && [ "$PAR" -le 3 ] || { echo "--parallel must be 2 or 3" >&2; exit 2; }
    REPS=1
fi
case "$CONFIG" in opus|codex) ;; *) echo "usage: $0 <opus|codex> <run|cheat> [repetitions]" >&2; exit 2;; esac
case "$MODE"   in run|cheat)  ;; *) echo "usage: $0 <opus|codex> <run|cheat> [repetitions]" >&2; exit 2;; esac
case "$REPS"   in ''|*[!0-9]*) echo "repetitions must be a number" >&2; exit 2;; esac
[ "$REPS" -ge 1 ] && [ "$REPS" -le 3 ] || { echo "repetitions must be 1-3" >&2; exit 2; }

docker info >/dev/null 2>&1 || { echo "Docker daemon is not running." >&2; exit 1; }
cd "$REPO" || exit 1
[ -z "$(git status --porcelain)" ] || { echo "Repository has uncommitted changes; aborting so the trial matches the commit." >&2; exit 1; }
COMMIT=$(git rev-parse --short HEAD)

# ---- the task path this run uses ------------------------------------------
CHEAT_DIR=""
if [ "$MODE" = "cheat" ]; then
    CHEAT_DIR=$(mktemp -d /tmp/klavis-cheat-XXXXXX)
    cp -R "tasks/$TASK" "$CHEAT_DIR/$TASK"
    INSTRUCTION="$CHEAT_DIR/$TASK/instruction.md"
    # Same two steps as run-cheat-trials.yml: strip the honest-mode "do not
    # cheat" sentences, then append the red-team brief after the description.
    perl -0pi -e 's/\s*[Dd]o not cheat[^.]*\.//g' "$INSTRUCTION"
    printf '\n\n' >> "$INSTRUCTION"
    cat rubrics/hack-trial-prompt.md >> "$INSTRUCTION"
    TASK_PATH="$CHEAT_DIR/$TASK"
    grep -qi "red team" "$INSTRUCTION" || { echo "adversarial prompt did not append" >&2; exit 1; }
    grep -qi "do not cheat" "$INSTRUCTION" && { echo "honest-mode clause survived the strip" >&2; exit 1; }
else
    TASK_PATH="tasks/$TASK"
fi

# ---- agent configuration, from .github/harbor-run-defaults.yml -------------
if [ "$CONFIG" = "opus" ]; then
    AGENT="claude-code"; MODEL="anthropic/claude-opus-5"
    KWARGS=(--ak reasoning_effort=max --ae CLAUDE_FORCE_OAUTH=1)   # token appended after it is read
else
    AGENT="codex";       MODEL="openai/gpt-5.6-sol"
    KWARGS=(--ak reasoning_effort=xhigh)
fi

AGENT_TO=$(python3 -c "import tomllib;print(int(tomllib.load(open('tasks/$TASK/task.toml','rb'))['agent']['timeout_sec']))")
VER_TO=$(python3 -c "import tomllib;print(int(tomllib.load(open('tasks/$TASK/task.toml','rb'))['verifier']['timeout_sec']))")

echo "task     : $TASK @ $COMMIT"
echo "mode     : $MODE$([ "$MODE" = cheat ] && echo '  (adversarial prompt appended to a copy outside the repo)')"
echo "agent    : $AGENT / $MODEL / $(printf '%s ' "${KWARGS[@]}")"
echo "harbor   : $HARBOR_VER   env: $ENV_BACKEND   output: $JOBS"
if [ -n "$PAR" ]; then
    echo "budget   : agent $((AGENT_TO/60))m, verifier $((VER_TO/60))m, $PAR trials concurrently in one job"
else
    echo "budget   : agent $((AGENT_TO/60))m, verifier $((VER_TO/60))m, x$REPS"
fi
[ "$MODE" = cheat ] && echo "expected : reward 0.000 on every trial, or the verifier is exploitable"
echo

mkdir -p "$JOBS"

if [ -n "$DRY" ]; then
    echo "DRY RUN: setup only, no trial spent."
    echo "  task path harbor would be given : $TASK_PATH"
    if [ "$MODE" = "cheat" ]; then
        echo "  instruction lines               : $(wc -l < "$TASK_PATH/instruction.md" | tr -d ' ')"
        echo "  honest-mode 'do not cheat'      : $(grep -ci 'do not cheat' "$TASK_PATH/instruction.md" || true) occurrences (want 0)"
        echo "  red-team brief present          : $(grep -ci 'Red Team QA Agent' "$TASK_PATH/instruction.md" || true) (want 1)"
        echo "  task files copied               : $(find "$TASK_PATH" -type f | wc -l | tr -d ' ')"
        if diff -q "$TASK_PATH/task.toml" "$REPO/tasks/$TASK/task.toml" >/dev/null; then
            echo "  task.toml identical to repo     : yes"
        else
            echo "  task.toml DIFFERS from repo"
        fi
    fi
    exit 0
fi

# ---- credentials ----------------------------------------------------------
if [ "$CONFIG" = "opus" ]; then
    if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
        echo "Using CLAUDE_CODE_OAUTH_TOKEN already exported in this shell (${#CLAUDE_CODE_OAUTH_TOKEN} chars)."
    else
        read -rsp "Claude OAuth token (input hidden): " CLAUDE_CODE_OAUTH_TOKEN; echo
    fi
    [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] || { echo "No token supplied." >&2; exit 1; }
    case "$CLAUDE_CODE_OAUTH_TOKEN" in *[[:space:]]*)
        echo "Token contains whitespace: the paste picked up a newline. Re-copy it." >&2; exit 1;; esac
    [ "${#CLAUDE_CODE_OAUTH_TOKEN}" -ge 40 ] || { echo "Token is ${#CLAUDE_CODE_OAUTH_TOKEN} chars; the paste was truncated." >&2; exit 1; }
    export CLAUDE_CODE_OAUTH_TOKEN
    export CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000   # read from os.environ; --ae does not carry it

    echo "Checking the token..."
    _hdrs=$(mktemp /tmp/.klavis-hdrs.XXXXXX)
    _code=$(curl -s -D "$_hdrs" -o /dev/null -w '%{http_code}' --max-time 20 \
        https://api.anthropic.com/v1/messages \
        -H "authorization: Bearer $CLAUDE_CODE_OAUTH_TOKEN" \
        -H "anthropic-version: 2023-06-01" -H "anthropic-beta: oauth-2025-04-20" \
        -H "content-type: application/json" \
        -d '{"model":"claude-opus-5","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}' 2>/dev/null)
    case "$_code" in
        200|400) echo "  token live (HTTP $_code). Launching now." ;;
        429)     echo "  token authenticates (HTTP 429 from the raw API is expected for a subscription"
                 echo "  token and says nothing about your Claude Code quota)." ;;
        401|403) echo "  token REJECTED (HTTP $_code). Run 'claude setup-token'." >&2; exit 1 ;;
        000)     echo "  could not reach api.anthropic.com." >&2; exit 1 ;;
        *)       echo "  unexpected HTTP $_code; refusing to burn a run." >&2; exit 1 ;;
    esac
    rm -f "$_hdrs"
else
    # The agent runs INSIDE the trial container, so a `codex login` on the host
    # does not reach it. Harbor's codex agent authenticates one of two ways:
    #   OPENAI_API_KEY exported here (what CI uses), or
    #   CODEX_FORCE_AUTH_JSON=1, which makes harbor inject ~/.codex/auth.json.
    # This script never reads either credential; it only forwards the choice.
    if [ -n "${OPENAI_API_KEY:-}" ]; then
        echo "Using OPENAI_API_KEY from this shell (${#OPENAI_API_KEY} chars), as CI does."
    elif [ -f "$HOME/.codex/auth.json" ]; then
        KWARGS+=(--ae CODEX_FORCE_AUTH_JSON=1)
        echo "No OPENAI_API_KEY set; telling harbor to inject your existing codex login"
        echo "(~/.codex/auth.json) into the container. This script does not read it."
    else
        echo "No codex credentials found: neither OPENAI_API_KEY nor ~/.codex/auth.json." >&2
        echo "Run 'codex login' in this terminal, or export OPENAI_API_KEY." >&2
        exit 1
    fi
fi

# ---- run ------------------------------------------------------------------
# Pass the token the way the assignment documents it, as an agent env var.
if [ "$CONFIG" = "opus" ]; then
    KWARGS+=(--ae "CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN")
fi

for i in $(seq 1 "$REPS"); do
    job="$CONFIG-$MODE-$TASK-$(date +%Y%m%d-%H%M%S)-$$"
    [ -n "$PAR" ] && job="$CONFIG-$MODE-x$PAR-$TASK-$(date +%Y%m%d-%H%M%S)-$$"
    KN=(); [ -n "$PAR" ] && KN=(-k "$PAR" -n "$PAR")
    echo
    echo "=============================================================="
    echo ">>> $CONFIG $MODE  trial $i/$REPS  started $(date +%H:%M:%S)  job=$job"
    echo "=============================================================="
    uvx --python 3.12 --from "harbor==$HARBOR_VER" harbor run \
        -p "$TASK_PATH" --agent "$AGENT" -m "$MODEL" \
        --env "$ENV_BACKEND" --yes "${KWARGS[@]}" ${KN[@]+"${KN[@]}"} \
        -o "$JOBS" --job-name "$job" 2>&1 | tee "$JOBS/$job.log"
    rc=${PIPESTATUS[0]}
    echo "<<< trial $i finished $(date +%H:%M:%S), harbor exit $rc"

    # Classify each trial in the job. An execution failure is not a model
    # result and must not be counted as one; the brief is explicit about that.
    for tdir in "$JOBS/$job"/*/; do
        [ -d "$tdir" ] || continue
        tname=$(basename "$tdir")
        logf=$(find "$tdir" -name 'claude-code.txt' -o -name 'codex.txt' 2>/dev/null | head -1)
        void=""
        if [ -n "$logf" ]; then
            grep -qiE '"error_status":401|invalid bearer|has been revoked|authentication_failed' "$logf" && void="AUTH FAILURE"
            [ -z "$void" ] && grep -qiE 'ApiRateLimitError|rate_limit_error' "$logf" && void="RATE LIMIT"
        fi
        [ -f "$tdir/exception.txt" ] && void="${void:-$(head -c 80 "$tdir/exception.txt" | tr -d '\n')}"
        if [ -n "$void" ]; then
            echo "!!  $tname: $void — VOID (execution failure, not a model failure)." >&2
        else
            r=$(cat "$tdir/verifier/reward.txt" 2>/dev/null || echo "?")
            echo "==  $tname reward: $r"
        fi
    done
done

echo
echo "Done. Jobs under $JOBS"
echo "Summarise with:  tools/trials.sh --summary"
