# Terminal-Bench 3 task contribution: desk-position-reconcile

This is a fork of [terminal-bench](https://github.com/harbor-framework/terminal-bench) carrying a
Terminal-Bench 3 task built for the Klavis AI founding engineer work trial by
**Yuvraj Singh Chowdhary**, system architect and AI engineer, founding engineer at Synvolv,
founding quant developer at Blockhouse.

**The submitted task is [`tasks/desk-position-reconcile`](tasks/desk-position-reconcile).**

Four earlier tasks of mine are also in `tasks/`: `sandbox-upgrade-continuity`,
`venue-settlement-close`, `settlement-stream-close` and `venue-gateway-cutover`. Frontier agents
solved all four, and they are kept deliberately rather than deleted, because working out why they
were too easy is how the submitted task was designed. [SUBMISSION.md](SUBMISSION.md) tells that
story, and keeping them means a reader can check it.

The fork is kept intact so the task can be validated against the real CI, static checks, rubric and
reviewer prompt rather than a copy of them. Upstream's README follows below this section.

## For the evaluator: what is mine and where to find it

This repository has 81 tasks and about 615 commits, because it is a fork of terminal-bench and the
fork is kept whole so the task can be validated against the real CI. Almost none of it is mine. Here
is the boundary, precisely.

**Everything I wrote branches from upstream commit `642ae58d`.** One command shows my work and
nothing else:

```bash
git diff 642ae58d..HEAD --stat     # every file I added or changed
git log  --oneline 642ae58d..HEAD  # my commits, oldest last
```

On GitHub, the compare view for `642ae58d...main` shows the same thing. Every commit after that base
is authored by me; every commit before it is upstream.

**The submitted task is one directory:** [`tasks/desk-position-reconcile/`](tasks/desk-position-reconcile).
That is the deliverable. If you read nothing else in `tasks/`, read that.

**What is mine, by path:**

| Path | What it is |
|---|---|
| [`tasks/desk-position-reconcile/`](tasks/desk-position-reconcile) | **The submitted task** |
| [`SUBMISSION.md`](SUBMISSION.md) | The submission: results, method, failure analysis |
| [`results/`](results) | All trial evidence and check output |
| [`tools/`](tools) | Trial launcher, rubric review, verification tooling |
| [`NOTICE`](NOTICE) | Authorship and licensing |
| [`docs/worktrial/`](docs/worktrial) | Working notes kept while building the task |
| [`tasks/sandbox-upgrade-continuity/`](tasks/sandbox-upgrade-continuity) | Four earlier tasks of mine, all of which agents solved. |
| [`tasks/venue-settlement-close/`](tasks/venue-settlement-close) | They are kept, not deleted, because working out why they |
| [`tasks/settlement-stream-close/`](tasks/settlement-stream-close) | were too easy is how the submitted task was designed. |
| [`tasks/venue-gateway-cutover/`](tasks/venue-gateway-cutover) | [`SUBMISSION.md`](SUBMISSION.md) tells that story; these let you check it. |

**Everything else is upstream terminal-bench**, including the other 76 tasks, `checks/`, `rubrics/`,
`.github/`, and the README content below this section. I have not modified any of it. The one
exception is this section, which is prepended to upstream's README.

A 20 minute path through it: this section, then [`SUBMISSION.md`](SUBMISSION.md), then
[`tasks/desk-position-reconcile/README.md`](tasks/desk-position-reconcile/README.md) for the
author-facing identity model that the task deliberately withholds from the agent.

## Start here

**[SUBMISSION.md](SUBMISSION.md)** is the main document. It covers the task, the trial results, how I
worked out where frontier agents actually fail, and the four days of tasks that agents solved before
this one stopped them.

## The task

[`tasks/desk-position-reconcile`](tasks/desk-position-reconcile) asks an agent to write the clearing
reconciliation for a prime-brokered market-making desk: eleven regulatory reports cut from four
venues' fill exports, with every clearing account resolved to the legal entity it belonged to on the
date each row is stated as at, inside a 128 MB memory ceiling. Accounts are reported as seeded opaque
tokens, so grading compares a partition rather than a string.

Six standard trials against the current TB3 CI defaults, six genuine failures:

| Configuration | Trials | Result |
|---|---|---|
| claude-code, `anthropic/claude-opus-5`, effort max | 3 | 0.000, 0.000, 0.000 |
| codex, `openai/gpt-5.6-sol`, effort xhigh | 3 | 0.000, 0.000, 0.000 |
| Adversarial (`/cheat`), claude-code | 1 | 0.000, after a completed attack |
| Adversarial (`/cheat`), codex | 2 | 0.000, both terminated early by an OpenAI safety refusal |

An earlier version of this task was solved by both agents, passing eight of eleven trials; that
record is in [`results/trials-v1/`](results/trials-v1) and is why it was rebuilt.

Oracle scores 1.000 and nop scores 0.000. The 22 shell static checks pass; the 23rd needs a
`GPTZERO_API_KEY` and is skipped without one. The 35-criterion implementation rubric clears with zero
failures: 33 pass and 2 not applicable. The CI gate fails only on a `fail` outcome. Raw evidence is in [`results/`](results).

## Running it yourself

You need Docker and [uv](https://docs.astral.sh/uv/). All commands run from the repository root.

Validate the task without any model credentials:

```bash
# 22 shell static checks, reporting any failure
f=0; for c in checks/check-*.sh; do bash "$c" tasks/desk-position-reconcile >/dev/null || { echo "FAIL $c"; f=$((f+1)); }; done; echo "$f failed"

# the 23rd is a python check; it needs GPTZERO_API_KEY and skips without one
python3 checks/check-ai-detection.py tasks/desk-position-reconcile

# 167 cross-source checks: policy against reference against verifier against
# generator against instruction against the built images, plus the leak guards
python3 tools/reconcile-task.py

# the reference solution must score 1.0
uvx --python 3.12 --from harbor==0.18.0 harbor run -p tasks/desk-position-reconcile \
    --agent oracle --env docker -o ./jobs

# an agent that does nothing must score 0.0
uvx --python 3.12 --from harbor==0.18.0 harbor run -p tasks/desk-position-reconcile \
    --agent nop --env docker -o ./jobs
```

### Getting model credentials

Both the rubric review and the agent trials run a coding agent inside a container, so the container
needs credentials. A login on your own machine does not reach it, which is worth knowing before you
spend an hour on a trial.

For Claude Code, mint an OAuth token and put it in your shell:

```bash
claude setup-token
```

Select the token it prints, copy it, then read it from the clipboard so it never lands in your shell
history:

```bash
export CLAUDE_CODE_OAUTH_TOKEN="$(pbpaste)"   # macOS; use xclip -o on Linux
echo "${#CLAUDE_CODE_OAUTH_TOKEN} chars"      # expect roughly 105 to 110
```

A short count means the copy was truncated, which happens when the token wraps across two terminal
lines and you drag-select instead of triple-clicking. Confirm it authenticates before spending a
trial:

```bash
curl -s -o /dev/null -w '%{http_code}\n' --max-time 20 https://api.anthropic.com/v1/messages \
  -H "authorization: Bearer $CLAUDE_CODE_OAUTH_TOKEN" \
  -H "anthropic-version: 2023-06-01" -H "anthropic-beta: oauth-2025-04-20" \
  -H "content-type: application/json" \
  -d '{"model":"claude-opus-5","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}'
```

200, 400 or 429 all mean the token is live. A subscription token returns 429 from the raw Messages
API almost regardless of remaining quota, and that says nothing about your Claude Code allowance.
401 or 403 means mint a new one. Note that minting a token revokes every earlier one, so do not run
`claude setup-token` while a trial is in flight.

For Codex, log in once:

```bash
codex login
```

That writes `~/.codex/auth.json`. The launcher detects it and tells Harbor to inject it into the
container with `--ae CODEX_FORCE_AUTH_JSON=1`. If you would rather use an API key, export
`OPENAI_API_KEY` instead and the launcher will use that, which is what CI does.

### Running the implementation rubric

```bash
tools/rubric-review.sh
```

This uses the same rubric file, reviewer prompt, staging layout and model as
`.github/workflows/review.yml`, and like CI it fails if the reviewer omits any criterion. It differs
in authenticating from your token rather than a repository secret. It prints a pass, fail or
not-applicable line for each of the 35 criteria and the explanation for any failure. Takes about
five to seven minutes.

### Running agent trials

The launcher hardcodes the agent, model and reasoning effort from `.github/harbor-run-defaults.yml`
and runs them on the docker backend rather than CI's modal. It does not parse that file, so if CI's
defaults change the launcher must be updated to match.

```bash
tools/trials.sh opus  run 1             # claude-code / claude-opus-5 / effort max
tools/trials.sh codex run 1             # codex / gpt-5.6-sol / effort xhigh
tools/trials.sh --parallel 2 opus run   # two isolated trials in one job
tools/trials.sh opus  cheat 1           # adversarial, must score 0
tools/trials.sh codex cheat 1
tools/trials.sh --summary             # one row per trial, read from $KLAVIS_JOBS
tools/trials.sh --dry-run opus cheat    # set a cheat trial up and inspect it, spending nothing
```

Each trial takes 30 to 50 minutes (the six scored trials ran 30 to 49). `--parallel 2` runs two at once in a single Harbor job, each with
its own environment container and its own verifier, which halves the wall clock. Three concurrent
trials need more than 8 GB allocated to Docker.

The launcher refuses to start on a dirty working tree, so every trial is tied to a commit. It
classifies authentication failures, rate limits, crashes and timeouts as void rather than as model
failures, which is what the assignment requires. In cheat mode it copies the task outside the
repository before appending `rubrics/hack-trial-prompt.md`, so the repository is never modified.

It passes credentials using the flags the assignment documents:
`--ae CLAUDE_FORCE_OAUTH=1 --ae CLAUDE_CODE_OAUTH_TOKEN=<token>` for Claude Code, and
`--ae CODEX_FORCE_AUTH_JSON=1` for Codex. It never writes a token to disk and never echoes one. It does
pass the token to harbor as an `--ae` argument, the form the assignment documents, so the token is
visible in `ps` output while a trial runs. It is unset on exit.

To run a trial by hand instead, without the launcher:

```bash
uvx --python 3.12 --from harbor==0.14.0 harbor run -p tasks/desk-position-reconcile \
    --agent claude-code --model anthropic/claude-opus-5 --env docker --yes \
    --ae CLAUDE_FORCE_OAUTH=1 --ae CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
    --ak reasoning_effort=max -o ./jobs

uvx --python 3.12 --from harbor==0.14.0 harbor run -p tasks/desk-position-reconcile \
    --agent codex --model openai/gpt-5.6-sol --env docker --yes \
    --ae CODEX_FORCE_AUTH_JSON=1 --ak reasoning_effort=xhigh -o ./jobs
```

## Working on the task

The task follows the standard Harbor layout. A few things specific to this one:

`tests/verifier_env/` holds copies of the reference, the generator and the policy, because the
verifier image is built from `tests/` alone and cannot reach the task root. Run
`tools/check-sync.sh` after editing any of the three, or `tools/reconcile-task.py`, which checks the
same thing among much else.

`tests/verifier_env/build_golden.py` computes the expected figures at image build time and asserts
that every trap in the task is still live: that a meaningful share of fills separate a dated
resolution from a folded one, that reading merger handles late moves fills, that local ids collide
across books, that FIFO and weighted-average cost disagree, that the financing waterfall exhausts,
that interest accrues. A change that quietly makes a rule decorative breaks the build instead of
shipping.

`tools/adversarial/` holds five exploit fixtures used to test the verifier: copying the golden files,
hiding memory in a detached orphan, ignoring the seed when minting tokens, holding the verifier's
output pipes open from a double-forked grandchild, and planting symlinks to the golden so the
privileged verifier dereferences them. The symlink one is there because an independent audit found
it and it worked, scoring reward 1 with no work done until the verifier was hardened. The task's own `cheat/reconcile.py` is a fifth: a
competent implementation whose only error is the merger fold. All six must score 0, and
`results/checks/automated-checks.txt` records that they do.

After changing the policy, the generator or the reference, rebuild both images before running
anything, since the period and the golden are baked in at build time:

```bash
docker build -t dpr-env:latest   tasks/desk-position-reconcile/environment
docker build -t dpr-tests:latest tasks/desk-position-reconcile/tests
```

## Repository layout

| Path | What is there |
|---|---|
| [`SUBMISSION.md`](SUBMISSION.md) | The submission: task, results, method, failure analysis |
| [`tasks/desk-position-reconcile/`](tasks/desk-position-reconcile) | The task, with its own README for the author-facing identity model |
| [`results/`](results) | Trial evidence, rubric verdict, automated check log |
| [`tools/`](tools) | Trial launcher, rubric review, cross-source checks, adversarial fixtures |
| [`docs/worktrial/`](docs/worktrial) | Working notes and design record kept while building the task |
| [`NOTICE`](NOTICE) | Authorship, license of the contributed work, and the evaluation terms |

Everything outside those paths, and outside the four earlier tasks named above, is upstream
terminal-bench.

## License and authorship

The contributed task and everything listed in [`NOTICE`](NOTICE) are original work by Yuvraj Singh
Chowdhary, released under the Apache License 2.0, the same license as upstream terminal-bench, so
the task stays compatible with the project it was built for.

Apache 2.0 requires that the copyright attribution in `NOTICE` be retained in any redistribution or
derivative work. `NOTICE` also records the scope of the Klavis AI evaluation grant: permission to
review and run the work for that evaluation, with no transfer of ownership or intellectual property.

The task carries the Harbor canary GUID as an anti-contamination marker. Please do not include
`tasks/desk-position-reconcile/` or `results/` in any training dataset.

---

## About the upstream project

This repository is a fork of [terminal-bench](https://github.com/harbor-framework/terminal-bench),
the benchmark framework the task is built for. Its own README, contributor list, dataset and
leaderboard live upstream, and everything under `checks/`, `rubrics/`, `.github/` and the other 76
tasks is theirs, unmodified. See the [upstream README](https://github.com/harbor-framework/terminal-bench#readme)
and [CONTRIBUTING.md](CONTRIBUTING.md).
