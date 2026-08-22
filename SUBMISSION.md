# Terminal-Bench 3 work trial — submission

**Candidate:** Yuvraj Singh Chowdhary
**Deliverable:** one original TB3 task, plus the measurement record that explains its difficulty.
**Task:** [`tasks/venue-settlement-close`](tasks/venue-settlement-close)

---

## 1. Status against the stated bar

The brief asks for a task that frontier agents fail three times out of three. **This task does not
meet that bar, and I am reporting that plainly rather than presenting a task I know to be solvable
as if its difficulty were unmeasured.**

Claude Opus 5 at `reasoning_effort=max` solves it twice out of two, in under ten minutes each time:

| run | commit | reward | wall clock | output tokens | cost |
|---|---|---|---|---|---|
| after the spec fix | `1f28eb1c` | **1.0** | 11m 23s | 35,062 | $1.77 |
| after the self-trade fix | `b915cbb1` | **1.0** | 9m 35s | 30,944 | $2.03 |

Everything I have been able to verify locally is green — 22/22 static checks, oracle reward 1.0,
nop reward 0.0, a verifier that executes nothing the agent produces. The 35-criterion implementation
rubric (`harbor check`) runs an LLM reviewer and needs credentials this build environment does not
hold, so **it has not been run on this task**; the command is in section 4 and it is the one
outstanding gate. What is missing beyond that is difficulty, and section 3 is my account of why,
backed by eight measured attempts.

## 2. What the task is

A settlement close for a crypto market-making desk over one seven-day period on two venues. The
agent reads a normative spec (`VENUE_MECHANICS.md`, 211 lines) and ten input files — 116 fills, 42
funding events, transfers, fee schedules, FX references, 30 days of volume history — and must
produce exact closing balances and PnL attribution in integer minor units.

Correctness is conjunctive: roughly twenty-five interacting rules, all of which must be right, with
a binary reward and no localising feedback. The rules that carry the weight:

- **Minor-unit conversion** between base and quote across every monetary path
- **Spot fees charged in the asset delivered to the desk** — base on a BUY, quote on a SELL
- **Fee tiers from a rolling 30-day window**, which *downgrades* the perpetual desk mid-period as
  high-volume history days age out faster than the period replaces them
- **Maker rebates as negative fees** that credit the account, live on 16 pre-downgrade fills
- **Self-trades excluded from qualifying volume** — a matched 5.24 BTC pair whose wrongful
  inclusion suppresses the tier downgrade and misprices the entire back half of the period
- **Three distinct rounding modes**: away-from-zero on fees, half-even on notional, truncation on
  realised PnL

**Every rule is load-bearing and the rule-set is uniquely determined.** I verified this by
exhaustive search: a parameterised reference over six rule axes yields 144 candidate rule-sets, and
**exactly one** reproduces the golden output. Flipping any single axis changes the output. No rule
in the spec is decorative, and no two rule-sets collide — so the task is neither padded nor
ambiguous.

## 3. Why it is not hard, and how I know

I designed, built, and measured **eight task families**. Seven were killed before completion on
evidence; the eighth is this one. Three merged TB3 tasks were probed as controls. The record is in
[`docs/worktrial/DECISIONS.md`](docs/worktrial/DECISIONS.md) (ADR-001 … ADR-036).

### 3.1 The measurement that matters most

The three merged TB3 tasks I probed as controls were all **solved** by Opus — so they cannot be
compared on pass/fail. They can be compared on **effort**:

| task | reward | wall clock | output tokens | cost |
|---|---|---|---|---|
| `formal-crypto-scheme-proof` | 1.0 | 73.3 m | 180,079 | $21.71 |
| `telecom-entity-resolution` | 1.0 | 86.5 m | 242,252 | $18.93 |
| `vf2-speedup-networkx` | 1.0 | 73.8 m | 274,174 | $25.33 |
| **`venue-settlement-close`** | 1.0 | **9.6 m** | **30,944** | **$2.03** |

**Agent effort on tasks the agent passes is a difficulty proxy that needs no failure to measure.**
By it, this task is not marginally short of corpus difficulty — it is **7–8× short**. That single
number reframes the result: the gap is structural, not a matter of adding more rules.

### 3.2 The mechanism

Across eight families the same two escapes kept appearing. A task is easy if **either** holds:

> **1. The agent can verify locally.** If it can construct any signal that discriminates correct
> from incorrect — a reference implementation, a reconciliation identity, a worked example, a
> property test — it stops reasoning and searches. I named this the *constructible oracle*; it
> killed families one through seven.
>
> **2. The spec is complete enough to execute.** A fully specified rule set is a transcription
> exercise, and frontier models transcribe near-perfectly at length. This killed family eight.

Difficulty requires denying **both**. But denying the second normally makes a task underdetermined
— which is unfair, not hard. I have direct evidence for that trade-off from this very task: an
earlier revision of `VENUE_MECHANICS.md` §3.1 stated `gross_quote = price * quantity` without the
minor-unit rescaling the reference applied. Opus read it literally, said so explicitly in its
trajectory, implemented exactly what was written, and scored 0. I diagnosed that reward-0 as **my
defect, not a model failure** — every perp figure differed from the reference by exactly 100×, the
base/quote minor-unit ratio — fixed the spec, and did not count it. Ambiguity produced a failure,
but an illegitimate one.

The tasks in TB3 that genuinely resist frontier agents escape the bind a third way: they rest on
**external ground truth** — a real regulatory standard, real hardware behaviour, a real protocol —
that the agent must apply correctly with no local oracle available and no complete restatement in
the environment. That is a property of the *subject matter*, not of the task's construction, which
is why it cannot be manufactured by making an authored spec longer or more intricate.

### 3.3 The near-miss worth recording

After family eight passed, my next design was to withhold three rules and supply a prior settled
period — inputs plus signed-off outputs — from which they had to be inferred. I built the
identifiability checker before building the task, and while writing it realised the design was
self-defeating: **a worked example is a labelled example.** The enumerator I wrote in ten minutes
searches 144 rule-sets and finds the unique fit, and the agent has a shell and Python and can write
the same thing. It was the constructible oracle again in new clothing. I killed it before spending
the build time, which is the check working as intended.

## 4. Reproducing the results

Harbor is version-split per the brief: `0.14.0` for `run`/`cheat`, `0.18.0` for `check`.
All invocations are isolated via `uvx`; nothing is installed globally.

```bash
# static checks (22/22)
for c in checks/check-*.sh; do bash "$c" tasks/venue-settlement-close; done

# implementation rubric, 35 criteria (requires credentials; not yet run on this task)
uvx --python 3.12 --from harbor==0.18.0 harbor check tasks/venue-settlement-close \
    -r rubrics/task-implementation.toml

# oracle must score 1.0
uvx --python 3.12 --from harbor==0.18.0 harbor run -p tasks/venue-settlement-close \
    --agent oracle --env docker -o ./jobs

# nop must score 0.0
uvx --python 3.12 --from harbor==0.14.0 harbor run -p tasks/venue-settlement-close \
    --agent nop --env docker -o ./jobs

# frontier trial
uvx --python 3.12 --from harbor==0.14.0 harbor run -p tasks/venue-settlement-close \
    --agent claude-code --model anthropic/claude-opus-5 --agent-kwarg reasoning_effort=max \
    --env docker -o ./jobs
```

Uniqueness of the rule-set is reproduced by the enumerator described in §2, which parameterises the
reference over six axes and asserts that exactly one of 144 combinations reproduces the golden
output.

## 5. What I would do with more time

Not another authored-spec task — eight measurements say that family is exhausted. I would build on
**external ground truth**: take a published venue specification the agent must apply without a
local oracle, and verify against behaviour derived from that source rather than from a spec I wrote.
That is the only one of the three escapes I have not closed, and §3.1 gives the metric to tell
whether an attempt is working — **agent effort on a passing run** — without needing to manufacture
a failure first.

## 6. Honest ledger

- Eight families designed and measured; seven killed on evidence, one completed
- Three merged TB3 tasks probed as controls; all three passed, at 7–8× this task's cost
- Two defects found in my own work and fixed rather than banked: an under-specified unit conversion
  that produced an illegitimate reward-0, and a stated rule with no observable consequence
- One deliverable task that is fair, complete, uniquely determined, mechanically green — and
  solvable by Opus 5 in ten minutes
