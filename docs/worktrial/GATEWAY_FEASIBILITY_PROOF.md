# Gateway feasibility gate: result

Verdict: **the gate fails, and it fails earlier than expected.** The question posed was whether the
starter architecture's feasible parameter region is empty. The answer turned out to be that the
*bad regime* is empty: under the mechanism the review specified, there is no parameter assignment in
which a bounded provider fault harms an unaffected domain while the healthy baseline still passes.

Analysis was done in a scratch model outside the repository. Nothing under `tasks/` was touched.

## 1. Model

Discrete logical ticks. Shared worker pool `W`, per-provider concurrency `P_i`, service time `S`,
terminal deadline `D`, attempt timeout `T_a` with deadline propagation `T = min(T_a, remaining)`,
attempt cap `B`, backoff `b`, dispatch gate `believed_occupancy < θ`, queue bound `Q`, queue ordering.

Provider-orphan semantics as specified: the gateway's attempt timeout releases the worker and decrements
the gateway's *believed* occupancy; the provider keeps the slot until its real completion. Dispatching
into a saturated provider costs one worker-tick and returns a fast capacity rejection.

Capacity condition from the review, `Σ P_i > W`: `W = 4`, `P_A = P_B = 3`, later also 4 and 6.

Terminal deadlines are terminal throughout. No expired request is ever retried.

## 2. What was measured

Healthy baseline: no fault, offered load below capacity, every admitted request must complete.
Fault case: provider A degraded for a bounded window, everything else unchanged.

The property of interest is whether the **healthy** provider's requests are harmed, and whether the
system fails to recover promptly after the fault ends.

## 3. Result

**Sweep 1, load.** Periods 6 through 2 at `P = 3`. At every load where the healthy baseline is clean,
the fault harms only provider A's own requests. Provider B completes 100% in every case. At period 2 the
offered load exceeds capacity and the healthy baseline itself fails, which is ordinary overload and is
excluded by the contract's own load assumption.

**Sweep 2, single-provider monopolisation.** The review's condition `Σ P_i > W` is necessary but not
sufficient, so `P` was raised to 4 and 6 so that one provider alone could in principle occupy the whole
worker pool. Provider B still completed 100% in every case.

**Sweep 3, exhaustive.** 432 configurations across period, `P`, `T_a`, backoff, `B`, and `D`, filtered to
those where the healthy baseline passes cleanly:

```
configs where healthy passes cleanly: 432
configs where the HEALTHY provider is harmed under fault: 0
```

**Sweep 4, the orphan spiral.** The hypothesised self-sustaining loop was: queueing delay shrinks
remaining deadlines, deadline propagation shortens effective attempt timeouts, short timeouts orphan
attempts, orphans consume provider capacity, which lengthens queues. Every configuration in which that
spiral appeared also failed the **no-fault** baseline, because the spiral requires `T_a < S`, which is a
broken configuration with no fault present. Wherever the no-fault baseline was clean, the fault produced
same-provider losses only and recovery was prompt.

## 4. Why, structurally

Three facts, none of them specific to the numbers.

**A timeout is a release.** The purpose of an attempt timeout is to free the local resource. So work
belonging to a faulted domain cannot hold the shared worker pool for long, by construction. The faster
the gateway gives up, the less shared capacity it holds.

**Orphaned work is provider-side, and provider capacity is not shared.** An orphan consumes a slot on the
provider it was dispatched to. By definition that cannot starve a different provider. The mechanism the
review asked to be made load-bearing produces same-domain capacity waste, not cross-domain coupling.

**Retry pressure is O(1 tick) of shared worker per attempt.** A capacity rejection is fast, so the shared
footprint of retrying into a saturated provider is bounded by attempt rate. Attempt rate is bounded by
`B × arrival rate`, and arrival rate is bounded below capacity by the healthy-load assumption. The
product is too small to saturate `W`.

These pull in opposite directions and cannot be satisfied together: cross-domain starvation needs the
faulted domain to **hold** the shared bottleneck, while orphaning needs it to **release** early. Any
parameter choice moves along that trade-off rather than escaping it.

## 5. The one variant that does exhibit the failure

Collapsing to a **single provider with multiple tenants** makes the contended resource the same one the
orphans hold, and the failure appears:

```
period  P  T_a |  healthy X/Y lost |  fault X/Y goodput  Y lost  orphans
     3  4    8 |         0/0       |        84/82            18        7
     4  6    8 |         0/0       |        67/67             8        9
     5  4    8 |         0/0       |        54/52             8        6
```

12 of 12 configurations show the healthy tenant harmed while the no-fault baseline is clean. The gateway
believes the degraded tenant holds nothing, keeps admitting its work, and its orphans hold the slots the
healthy tenant needs. A correct gateway prevents this by tracking per-tenant occupancy including orphaned
attempts.

Two reasons this is not a rescue.

**It is a bounded transient, not metastability.** Harm is confined to the fault window. Once the degraded
tenant's requests are fast again the orphans drain and service resumes without intervention. Section L of
the gate is explicit that the correct term must be used, and the correct term here is prolonged
degradation, not a persistent attractor. There is no positive feedback that reproduces the condition
after the trigger is removed.

**It is one insight deep.** The whole repair is "count orphaned occupancy per tenant". That is Reviewer
B's third finding arriving on schedule: the task collapses to a small composition of standard patterns.
Grading it also requires a threshold on how many healthy-tenant losses are acceptable, and any such
threshold is the arbitrary magic number the gate forbids, because some loss during the fault is
physically unavoidable.

## 6. Consequence for the parametric-feasibility question

The question is moot in the form it was asked. A proof that no constant repairs the architecture requires
the architecture to fail in the first place. Under the specified mechanism and the mandated constraints,
the shipped architecture does not fail on any healthy-passing configuration, so there is nothing for a
constant to repair.

For completeness, the tension analysis prepared for each knob still holds and is recorded here, because
it is what would have been argued had the bad regime existed: the dispatch gate `θ` would need to be
`P_i` for the healthy floor and `0` under orphan saturation, which no constant satisfies; `B` must be at
least 3 to admit retry, retry, success within the deadline while any `B` large enough for that also
admits the amplification; larger backoff pushes the two-retry case past its deadline while smaller
backoff raises dispatch pressure; fresh-first ordering starves recoverable retries and retry-first
starves fresh work; and `T_a` cannot exceed the remaining deadline once propagation is on, so no cap
value prevents late-dispatch orphaning. None of that gets used, because the premise fails.

## 7. Superseded mechanisms

Recorded rather than deleted.

- **Retrying an expired request** as the sustaining mechanism. Rejected by the review as semantically
  wrong, and correctly so. A terminal deadline is terminal.
- **Head-of-queue retry re-entry** as the trigger. Retained in the model and shown insufficient: it
  reorders work but does not make the faulted domain hold shared capacity.
- **Provider-orphaned downstream work** as the sustaining mechanism across domains. Disproven above. It
  survives only in the single-provider multi-tenant form, where it is a bounded transient.
- **`Σ P_i > W`** as the sufficient condition for shared-worker contention. Necessary but not
  sufficient; even `P_i ≥ W` does not produce it, because timeouts release workers.

## 8. Reproduction

The scratch model and sweeps live outside the repository, under the session scratchpad at
`gwmodel/model.py`. They are analysis, not task code, and are deliberately not committed. The result
reproduces from the parameters in sections 1 and 3.
