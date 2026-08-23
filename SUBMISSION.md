# Terminal-Bench 3 work trial — submission

**Candidate:** Yuvraj Singh Chowdhary
**Primary task:** [`tasks/venue-gateway-cutover`](tasks/venue-gateway-cutover)
**Supporting task:** [`tasks/settlement-stream-close`](tasks/settlement-stream-close)

---

## 1. Status against the bar, stated first

The brief requires a task that frontier agents fail three times out of three. **The task
submitted here does not meet that bar.** Claude Opus 5 at `reasoning_effort=max` solved it on the
first clean trial, in 13m48s for $1.58. Everything else the brief asks for is green, and section 5
is the measured account of why the difficulty bar was not reached, across ten designed task
families.

I am reporting that in the first paragraph rather than burying it, because a task whose difficulty
is unmeasured is worth less than a task whose difficulty is measured and found wanting.

## 2. What the task is

A market-making desk's connector to one venue is losing money: its positions do not match the
venue's. Four compose services stand in for the desk's connectivity.

| service | role |
|---|---|
| `venue` | Meridian's market-data edge: a paced, at-least-once message stream of fills, amendments and busts, addressed by `(epoch, seq)` |
| `ledger` | the desk's Postgres — a `fills` log and a `positions` table |
| `main` | the desk's box, running the connector under a supervisor; where the agent works |
| `desk` | the risk monitor, sampling once a second how far the ledger trails the session |

The connector on the box is plausible working code that is wrong in ways that only show up under
the venue's real behaviour. Repairing it requires getting six things right at once:

1. the stream is **at-least-once and rewinds itself** after a connection flap
2. batches contain **holes** that re-reading never heals; only the metered REST backfill repairs them
3. the venue **fails over mid-session** into a second epoch whose sequence restarts at 1, so 18,000 sequence numbers exist under both
4. the venue **restates trades** — 2,212 amendments and 1,250 busts over a session, revisions up to 4 — and the book is the highest revision per `fill_id`
5. **busted trades leave the book entirely**
6. the session is **live and paced**, and the natural "ask the ledger which sequences are missing" query degrades until the connector falls permanently behind

The property the design turns on is that **each correct fix creates the next trap.**
`ON CONFLICT (seq) DO NOTHING` is exactly right against a rewinding feed, and then silently
discards every message in the second epoch. `DO NOTHING` on restatements drops all 2,212
amendments. Switching to `DO UPDATE` lets a redelivered *old* copy overwrite a newer one. Only a
conflict rule keyed on `(epoch, seq)` that compares revisions is correct.

The agent cannot check its own answer. The venue's book is served on the venue container's
loopback only and returns 403 to the desk's box, and the REST backfill is metered so the session
cannot be replayed over it — 40,000 messages would need roughly 1,000 seconds against a session
that lasts 180.

## 3. Automated checks — all green

```bash
# 22 required static checks
for c in checks/check-*.sh; do bash "$c" tasks/venue-gateway-cutover; done

# implementation rubric
uvx --python 3.12 --from harbor==0.18.0 harbor check tasks/venue-gateway-cutover \
    -m anthropic/claude-opus-4-8

# oracle must score 1.0
uvx --python 3.12 --from harbor==0.18.0 harbor run -p tasks/venue-gateway-cutover \
    --agent oracle --env docker -o ./jobs

# nop must score 0.0
uvx --python 3.12 --from harbor==0.14.0 harbor run -p tasks/venue-gateway-cutover \
    --agent nop --env docker -o ./jobs
```

| check | result |
|---|---|
| 22 required static checks | **22/22 pass** |
| Docker build (5 images, 4 services) | **pass** |
| oracle validation | **reward 1.000**, 7/7 tests, five consecutive clean runs |
| nop validation | **reward 0.000**, 6/7 failing, 0 exceptions |
| Harbor built-in quality rubric | **11/11 pass**, exit 0, run twice |
| repository 35-criterion rubric | **did not run** — see below |

The repository rubric (`harbor check -r rubrics/task-implementation.toml`) failed to produce a
result on two attempts: the reviewing agent finished normally (`end_turn`, `is_error: false`,
$1.52) but never wrote `check-result.json`, and Harbor reported *"Check agent did not produce a
valid result."* That is a limitation of the tool on this task, not a finding about the task, and I
have not represented it as a pass.

The built-in rubric independently confirmed the parts that matter most:

> **Anti Cheating Measures — pass.** *"The venue's authoritative book is served on loopback only
> and returns 403 to the main box, so the agent cannot read the answer… Multiple independent axes
> mean fabricating numbers without real ingestion fails."*

> **Hardcoded Solution — pass.** *"It does not echo or cat final answers into the database."*

## 4. Trial results

Configuration is the current TB3 CI default from `.github/harbor-run-defaults.yml`:
`claude-code` on `anthropic/claude-opus-5` with `reasoning_effort=max` and
`CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000`, `--env docker` per the assignment's own sample invocation.

```bash
uvx --python 3.12 --from harbor==0.14.0 harbor run -p tasks/venue-gateway-cutover \
    --agent claude-code --model anthropic/claude-opus-5 --env docker --yes \
    --ae CLAUDE_FORCE_OAUTH=1 --ak reasoning_effort=max -o ./jobs
```

Fifteen trials were run in total, of which **seven were void** and are excluded: three
`ApiRateLimitError`, three `NonZeroAgentExitCodeError`, and one further rate-limit failure. Under
the brief these are execution failures, not model failures, and none is counted.

| task | outcome | wall clock | note |
|---|---|---|---|
| `venue-gateway-cutover` | **reward 1.0** | 13.8 min | solved cleanly; no exploit |
| `settlement-stream-close` | reward 0.0 | 56.7 min | **not counted** — caused by a defect in my spec, section 6 |
| `venue-settlement-close` | reward 1.0 | 11.4 min | after fixing my own defect |
| `venue-settlement-close` | reward 1.0 | 9.6 min | confirmation |
| `telecom-entity-resolution` (control) | reward 1.0 | 86.5 min | merged TB3 task |
| `vf2-speedup-networkx` (control) | reward 1.0 | 73.8 min | merged TB3 task |
| `formal-crypto-scheme-proof` (control) | reward 1.0 | 73.3 min | merged TB3 task |

**Codex (`gpt-5.6-sol`, `reasoning_effort=xhigh`) has not been run.** That half of the trial
matrix is outstanding and I am not claiming otherwise.

## 5. Failure analysis: ten task families

Ten families were designed and measured; nine were killed on evidence before completion. The
record is in [`docs/worktrial/DECISIONS.md`](docs/worktrial/DECISIONS.md).

### 5.1 The measurement that carries the argument

The three merged TB3 tasks probed as controls were all **solved** by Opus, so they cannot be
compared on pass/fail. They can be compared on **effort**, which is a difficulty proxy that needs
no failure to measure:

| task | reward | wall clock | output tokens | cost |
|---|---|---|---|---|
| `telecom-entity-resolution` | 1.0 | 86.5 min | 242,252 | $18.93 |
| `vf2-speedup-networkx` | 1.0 | 73.8 min | 274,174 | $25.33 |
| `formal-crypto-scheme-proof` | 1.0 | 73.3 min | 180,079 | $21.71 |
| `venue-settlement-close` (mine) | 1.0 | 10.8 min | 35,194 | $1.81 |
| `venue-gateway-cutover` (mine) | 1.0 | **13.8 min** | 29,255 | $1.58 |

Corpus tasks that Opus passes still cost it 73–87 minutes. Mine cost it 14. The gap is not
marginal; it is roughly **5×**, and it is the honest measure of how far short the difficulty fell.

### 5.2 What does not create difficulty

Three mechanisms were each tried, measured, and found not to work.

**A constructible oracle ends the task.** If the agent can build any signal that separates correct
from incorrect — a reference implementation, a reconciliation identity, a worked example — it stops
reasoning and searches. Seven families died this way. The lesson is load-bearing enough that I
built the same mistake into `venue-gateway-cutover` myself: the venue's book was reachable from
the agent's box until an audit caught it. It is now 403.

**A complete specification is a transcription exercise.** Frontier models transcribe long, intricate
rule sets near-perfectly. `venue-settlement-close` documented roughly twenty-five interacting
settlement rules, verified to be uniquely determined by exhaustive search over 144 candidate rule
sets. Opus implemented it exactly in ten minutes.

**Conjunction of known patterns is a checklist, not a wall.** `venue-gateway-cutover` stacks six
interlocking distributed-systems traps. Every one is a pattern a frontier model knows —
idempotency keys, epoch numbers, last-writer-wins, tombstones, gap backfill, rate limits. Opus's
own submitted connector opens with a docstring naming each of them, including a subtlety I never
wrote down: *"Revisions of a trade can cross an epoch boundary, so the book keys on fill_id
globally."* It did not merely survive the traps; it reasoned past them.

### 5.3 The finding I did not expect

**Five of the reward-zeros this work produced were caused by defects in my own task, not by model
failure**, and I found them by reading trajectories rather than by trusting the score:

| defect | how it surfaced |
|---|---|
| `gross_quote = price * quantity` omitted a base→quote minor-unit conversion | every perp figure differed from the reference by exactly 100×, the minor-unit ratio |
| whether a spot sell fee applies to rounded or exact `gross_quote` was undetermined | **the model wrote a CLI flag for my ambiguity**: `SELL_FEE_ON_ROUNDED = "--sell-fee-on-rounded" in sys.argv` |
| a 1200s runtime limit was enforced but never stated | its engine was 21× slower than the reference and failed a limit it was never given |
| the spec placed funding before a same-timestamp fill; the reference did the opposite | 30 fills land exactly on a funding timestamp |
| the reference emitted a `TRANSFER` pseudo-instrument row the document never mentions | the task was unpassable by anyone; the model's engine mentions `TRANSFER` zero times in 1,023 lines |

The pattern is uncomfortable and worth stating plainly: **the model specified more precisely than I
did.** Every one of those was my error surfacing as its apparent failure. None was counted. I
switched from discovering them through hour-long trials to differential auditing — running the
model's own independent implementation against my reference across generated periods — which found
the last two in ninety seconds instead of an hour and $16 each.

### 5.4 What the evidence points to next

Difficulty in this corpus is **deep, not broad**. `takens-embedding-lean` is one Lean proof at 60
expert-hours. `gpt2-codegolf` is one target: GPT-2 inference in under 2,000 bytes.
`vf2-speedup-networkx` is one number: a 1000× geometric-mean speedup over NetworkX, measured as a
ratio against a baseline in the same container so the target cannot drift with the machine. That
last one cost Opus 74 minutes — five times what my best task cost it.

The only thing that has actually broken an implementation in this work is a hard performance
ceiling: the model's first settlement engine was 21× too slow, and so was my own first reference,
independently. A single aggressive quantitative target with a correctness gate beside it is the
remaining hypothesis I would build against, gated on measuring the naive-to-optimal gap before
committing to it.

## 6. Reproducing everything

Harbor is version-split per the repository's CI: `0.14.0` for `run` and `cheat`, `0.18.0` for
`check` and validation. Every invocation is isolated through `uvx`; nothing is installed globally.

```bash
# static checks
for c in checks/check-*.sh; do bash "$c" tasks/venue-gateway-cutover; done

# oracle, nop, rubric, and a frontier trial: see sections 3 and 4
```

The verifier reads four files captured from the compose services after the agent's session ends —
what the venue published, what the ledger holds, what the risk desk observed, and the connector
itself. No agent code is executed or imported by the verifier. A missing collected file exits
non-zero without writing a reward, so harness breakage surfaces as a verifier error rather than a
scoreable zero; a ledger whose schema the agent destroyed is scored as a failure, because that is
the agent's doing.

## 7. Ledger

- Ten task families designed and measured; nine killed on evidence
- Three merged TB3 tasks probed as controls; all passed, at ~5× the cost of my best task
- Fifteen trials run, seven void and excluded, all void causes recorded
- Five defects found in my own work through trajectory analysis and differential auditing, fixed rather than banked as failures
- Two exposures found by audit and closed: the venue's book was readable from the agent's box, and the REST backfill could replay the whole session
- One deliverable task that is fair, hardened, mechanically green, reviewed 11/11 — and solved by Opus 5 in fourteen minutes
