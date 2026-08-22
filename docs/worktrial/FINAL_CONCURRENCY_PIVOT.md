# Final pivot gate: tool-stream-credit-broker

Verdict: **rejected on paper, before implementation.** The design fails two of its own gates
independently, and both were settled empirically in under an hour rather than argued.

Analysis ran in a scratch interleaving model outside the repository. Nothing under `tasks/` was created
or touched.

## 1. What was proposed

A broker multiplexing streaming tool output to clients, with a bounded global credit pool, logical
streams, generations created by restart, cancellation, backpressure and late callbacks from retired
generations. Four graded consequences: capacity conservation, generation isolation, terminal
correctness, independent progress.

## 2. Gate 5, the global-lock attack

The gate says: attempt the simplest correct implementation, one broker-wide lock, and reject the design
if it satisfies every public requirement.

It does.

A deterministic model was built in which each thread is a list of atomic steps and a scheduler explores
every interleaving exhaustively. A thread holding the lock cannot be preempted mid-critical-section,
which is what a lock means, and a thread that waits releases the lock, which is what a condition variable
means. The scenario exercises exactly the failure geometry the brief specifies: a producer reserves a
credit under generation 1, the stream is restarted to generation 2, a late generation-1 callback arrives,
and an unrelated stream competes for the single credit.

```
states explored: 240
violations: 0

=> ONE GLOBAL LOCK + CONDVAR + GENERATION COUNTER satisfies A, B, C, D
```

Property D, independent progress, was expected to be the one that punishes coarse locking. It does not,
and the reason is definitional rather than accidental: a producer blocked on credit waits on the
condition variable, and waiting **releases the lock**, so an unrelated stream acquires it and proceeds.
Blocking while holding a global right is the bug, not the requirement, and the standard way to write the
blocking path already avoids it.

The deeper reason generalises past this design. In a bounded-interleaving model, operations are atomic
steps and a global lock simply serialises them. Serialisation preserves every safety property and, with
condition variables, every liveness property. The only things coarse locking loses are throughput and
parallelism, which are timing properties, and section 8 of the brief rightly forbids grading on timing.
So there is no semantic progress property available that a single-lock design fails.

## 3. Gate 6, the size of the complete repair

Measured rather than estimated. Removing only the generation and terminal guards from the correct model
and diffing back:

```
lines added to fix: 11
```

The entire repair is one global lock, one condition variable, and eleven lines of guards of the form
"if this callback's generation is not the stream's current generation, drop it" and "if this generation
is already terminal, do nothing". That is the standard pattern plus well under twenty obvious lines,
which section 6 says to report before building.

## 4. Gate 4, the labelled-example-generator test

The design also fails the test that motivated this whole search, and fails it worse than the previous
families did.

The rule is that finding counterexamples must not mechanically identify the repair. In a scheduling
problem that holds: a failing trajectory says "this schedule starved that tenant" and labels no
individual decision, so credit assignment stays with the engineer. That was the entire argument for
pivoting to gateway work.

Concurrency inverts it. An interleaving explorer does not return a trajectory-level verdict; it returns
**the two steps that interleaved badly**. That is a pointwise label on a repair site. The mechanical
response, make that region mutually exclusive or guard it by generation, is correct essentially every
time. A model checker is therefore a stronger labelled repair oracle than the differential fuzzer that
solved the first family in ten minutes, not a weaker one.

## 5. Verifier technology

Not evaluated. Both Option A (Java PathFinder or equivalent) and Option B (a deterministic controlled
interleaving harness) were left unbuilt, because a verifier mechanism is only worth spiking once the
thing it would verify is known to be hard. Option B is clearly feasible; the scratch model in this gate
is a small instance of it. Feasibility was never the blocker.

## 6. Originality

Scanned the corpus for race condition, data race, deadlock, livelock, linearizability, concurrency,
mutex, lock-free, atomic, producer-consumer, bounded buffer, backpressure, multiplexer, stream broker,
buffer credits, cancellation, generation, ABA, thread safety, happens-before, memory model, interleaving
and model checking. Most hits are substring noise.

Two real neighbours. `kv-live-surgery` is the corpus's genuine concurrency task: replace a live
key-value server under load without breaking in-flight requests, with mutex and lock-free vocabulary
throughout. Its crux is live process surgery under load rather than ownership under cancellation.
`wal-recovery-ordering` requires that concurrent higher-LSN writers not expose state before lower LSNs
are durable, which is an ordering discipline in a single engine.

Neither grades concurrent bounded streaming ownership under cancellation and restart, so **originality
is clean**. It is not the binding constraint; gates 4, 5 and 6 are.

## 7. What this closes

Four families have now been evaluated, three of them against evidence rather than intuition:

| Family | Outcome | How it was settled |
|--------|---------|--------------------|
| Sandbox migration | Solved in 10m30s | Live Opus calibration |
| Migration chain | Retired | Generalisation of the above |
| Gateway metastability | Mechanism disproven | 432-configuration sweep |
| Credit broker | Rejected on paper | 240-state exhaustive check |

The pattern across all four is one finding. A task is easy for a frontier agent when the agent can
construct something that labels the repair, whether that is a pointwise output oracle, a differential
fuzzer, or a model checker naming the two steps that raced. Difficulty has to come from somewhere the
agent cannot mechanise, and three attempts to engineer that into a small codebase have not succeeded.

The corpus agrees. Tasks that are genuinely beyond frontier carry inherent depth: 16 to 60 hours of
expert time, formal proof, deep regulatory or scientific domain knowledge, or real scale. Small
codebases with subtle defects are the shape frontier agents are strongest at, and that is the shape all
four candidates shared.
