# Final search gate: rollout-batch-scheduler

Verdict: **REJECT — GENERIC OPTIMIZER GOOD ENOUGH.** Settled in about 35 minutes of the 90-minute box.
Prototype in `/tmp/rbs`, outside the repository. No task directory created.

## Workload model

Post-training rollout fleet. `W` workers, each running one rollout at a time. A job carries
`job_id, tenant, adapter, prefix, duration, deadline, weight`. Setup is sequence dependent: switching
adapter costs `A` and invalidates the prefix cache; switching prefix costs `P`. Consecutive compatible
rollouts pay nothing.

Objective, one coherent quantity. Total execution time is invariant under any valid schedule, so it drops
out and what remains is fleet time wasted plus service obligations missed:

```
cost = total_setup_time + sum_j weight_j * max(0, completion_j - deadline_j)
```

Three seeded regimes, 60 jobs and 4 workers each: **A** setup-heavy (A=90, P=35, loose deadlines),
**B** deadline-heavy (A=12, P=5, tight deadlines), **C** mixed.

## Baselines vs reference, 30 instances

Reference is ruin-and-recreate LNS with simulated-annealing acceptance, multi-alpha greedy seeding,
60,000 iterations. Numbers are `cost / reference_cost`, median with worst case.

| Baseline | A | B | C |
|---|---|---|---|
| B0 FIFO | 5.76x (9.19) | 13.54x (15.53) | 16.21x (22.29) |
| B1 EDF | 4.11x (4.52) | 4.93x (6.04) | 8.79x (14.87) |
| B2 setup affinity | 2.38x (2.59) | 7.12x (9.59) | 5.22x (7.18) |
| B3 weighted greedy | 1.39x (1.49) | 6.57x (7.69) | 2.85x (3.70) |
| B4 multistart + hill climb, 4k iters | 1.16x (1.27) | 1.32x (1.53) | 1.18x (1.37) |

At first reading this looks like a real gap: the strongest simple baseline sits 16 to 32 percent above the
reference, and no single heuristic wins every regime. That last property is genuine and is the one
attractive feature of the family.

## Why it fails: the gap is search budget, not architecture

B4 ran 4,000 iterations against the reference's 60,000. Equalising the budget removes almost all of it.

**Naive simulated annealing**, meaning a single greedy seed plus relocate and swap moves, no
ruin-and-recreate, no seed diversity, 60,000 iterations:

| Solver | A | B | C |
|---|---|---|---|
| Hill climb, 60k | 1.192x (1.235) | 1.076x (1.165) | 1.110x (1.347) |
| **Naive SA, 60k** | **1.109x** (1.176) | **0.950x** (1.059) | **1.084x** (1.353) |

Naive SA **beats the reference on regime B**, median 0.950x. A second competent iteration, adding
multi-alpha greedy seeding and nothing else, reaches median 1.004x on B with a best case of 0.858x, and
1.13x on A and C with best cases of 1.000x and 0.817x.

So the "reference" is not a stronger architecture. It is the same metaheuristic family with slightly
better tuning, and a straightforward implementation matches or beats it on a third of instances.

## Against the gate's own criteria

- Section 7 asks the strongest generic baseline to miss by 10 to 15 percent **on multiple regimes**.
  Naive SA misses by 11 percent on A and 8 percent on C and **wins** on B. That is not a structural gap.
- Section 8's proposed threshold of 1.03 to 1.05x is already met or beaten by naive SA on regime B, and
  a threshold cannot be anchored to a reference that a simpler solver beats.
- Section 11 is decisive and explicit: *"Assume Opus does: read objective, build EDF/setup greedy,
  implement local search, benchmark public cases. If this is likely to clear the proposed hidden
  threshold, REJECT."* `naive_sa` is precisely that program, and it clears it.

## The oracle problem, a seventh time

The task must ship a public cost simulator, or it is not well posed: the candidate has to be able to
evaluate its own schedules. But `cost(schedule)` is exactly the oracle a metaheuristic needs. The agent
never requires optimal schedules, only a fast evaluator, and we are obliged to hand it one.

That is the same structural property behind all six rejected families and all three clean probe passes.
Here it is unavoidable rather than incidental, which is what makes the family unfixable rather than
merely weak.

## Why not simply strengthen the reference

Tempting and wrong. Any stronger reference could be matched by giving the candidate's solver more
budget or one more move operator, so the exercise becomes tuning the reference until a generic solver
cannot match it. That is choosing a threshold to defeat a baseline rather than deriving it from the
problem, which every gate in this project has forbidden. Metaheuristics for sequence-dependent setup
scheduling with tardiness are a commodity: competent implementations cluster within a few percent, which
is why the field benchmarks against published instance libraries rather than against one author's solver.

## Originality

Clean, and not the binding constraint. `tardiness`, `job shop`, `makespan`, `bin pack`, `local search`,
`simulated annealing` and `branch and bound` return nothing across the corpus. The nearest neighbours are
`production-planning`, which grades ERP/MES/WMS rule conformance with a lexicographic tie-break rather
than cost against a reference, and `freight-dispatch-shift`, which grades lifecycle correctness and
reason codes. No corpus task grades "beat a hidden reference optimiser's cost".

## Verdict

`REJECT — GENERIC OPTIMIZER GOOD ENOUGH`. Per the stop condition, no further family is proposed and the
task search ends here.
