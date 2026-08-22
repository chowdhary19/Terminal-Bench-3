# Pivot gate: tool-gateway-metastability

> **Superseded by [GATEWAY_FEASIBILITY_PROOF.md](GATEWAY_FEASIBILITY_PROOF.md).** The
> pre-implementation gate disproved this document's central mechanism. Sections 6 and 7, the shipped
> design and the metastable chain, do not hold: a bounded provider fault cannot harm an unaffected
> provider under provider-orphan semantics, because a timeout releases the shared worker and orphaned
> work consumes only per-provider capacity. 432 healthy-passing configurations produced zero
> cross-domain harm. The document is kept for the trivial-solution attack in section 8, the originality
> scan in section 18 and the reuse audit in section 19, which remain valid.

Design only. Nothing under `tasks/` changes on the strength of this document.

Working name: `tool-gateway-metastability` (three slug tokens, at the cap the slug check enforces).

## 1. Why the previous family is abandoned

Recorded rather than deleted. The sandbox migration family produced two designs and one empirical result:
Opus 5 at reasoning max returned reward 1 on the Phase 1A task in 10 minutes 30 seconds, 8.75% of the
time budget, no wrong turns, with a fix better than the reference solution.

The trajectory showed why, and the diagnosis generalises past the specific task. Deterministic
transformation problems admit a **pointwise labelled-example generator**. The agent built one in about
three minutes: for input snapshot X, the expected output is whatever the shipped correct runtime produces,
so every failing case localises directly to a field, and credit assignment is free. Redesign 1B proposed
removing the generator by decommissioning the legacy runtime. That was sound but attacked the symptom;
the family's shape still rewards find-and-fix over design.

The pivot rule that replaces it: **the agent may generate counterexamples freely, but counterexample
generation must be insufficient to establish global correctness.**

## 2. Production incident

A multi-tenant tool execution gateway dispatches agent-issued tool calls to downstream providers. Two
providers, a small shared worker pool, per-provider concurrency limits, per-request deadlines, retries
with provider-supplied retry-after, and cancellation.

One provider begins returning rate-limit and transient responses for a bounded period. Retries begin.
Fresh work waits longer. Requests start passing their deadlines while queued. Timed-out work is
classified retryable and re-enters. Traffic to the *healthy* provider begins missing deadlines even
though nothing is wrong with it.

The provider recovers. The gateway does not. Its own backlog of internally generated work continues to
occupy the scheduler, keeps producing fresh timeouts, and keeps the system in the degraded state
indefinitely under a load it served comfortably an hour earlier.

The trigger was bounded. The failure is not.

## 3. Central invariant

> A bounded external fault may not create unbounded or self-sustaining internal work, may not consume
> capacity charged to unaffected isolation domains, and may not prevent the gateway from returning to
> healthy service once the fault ends and offered load is within capacity.

Four graded consequences, all following from one resource-and-progress model rather than being four
separate requirements:

1. **Bounded amplification.** Internal execution work generated per admitted request stays within a
   documented bound, in aggregate, not merely per request.
2. **Fault isolation.** Work charged to one provider or tenant cannot consume capacity owed to another.
3. **Recovery.** After a fault of at most F ticks ends, with offered load below sustainable capacity,
   backlog drains and healthy service resumes within a bound derived from public capacities.
4. **Terminal closure.** A cancelled or expired request cannot cause any later provider attempt.

A fifth property, a healthy-load service floor, exists to make trivial passes fail rather than as a
separate capability. It is described in section 10.

## 4. The mandatory test: what generator can the agent build, and why is it insufficient

The agent can and should build all of these, cheaply: an event-schedule fuzzer, a randomized workload
generator, a small exhaustive interleaving enumerator, a synthetic provider-fault simulator. We ship the
observability that makes them easy. Detection is meant to be cheap.

What such a generator returns is a **trajectory-level verdict**: "schedule S starved tenant B", "schedule
S never converged", "attempts exceeded the bound". What it does not return, and cannot, is a label on any
individual scheduling decision. Nothing in a failing trajectory says which of the dispatch, admission,
retry-classification, or queue-ordering decisions was the wrong one, or what the right one was. That is
the credit-assignment problem, and it is what the previous family did not have.

Two further reasons the generator does not close the gap:

**Sampling does not establish a quantified property.** The contract quantifies over schedules. Passing ten
thousand sampled schedules is evidence, not proof, and the verifier draws adversarially from documented
families the agent has not seen.

**The fix must be structural, not parametric.** This is the load-bearing design constraint, and it is
where a fuzz-guided search would otherwise win. If the correct solution were "set the retry cap to 3 and
the queue bound to 64", the agent would grid-search its own fuzzer to those constants in minutes. The
shipped architecture must therefore be one where **no assignment of constants satisfies the contract**,
because the defect is in what owns capacity and for how long, not in how much. Before implementation this
becomes a verification obligation: enumerate the shipped design's tunable constants and show that the
feasible region is empty. If it is not empty, the design fails and must be reworked.

## 5. System model

Deterministic discrete-event simulation over integer logical ticks. No wall clock, no sleeps, no real
concurrency, no thread races. The same schedule always produces the same execution, which is what lets
the verifier compare exactly and lets the agent reproduce anything.

- **Tenants:** 3. **Providers:** 2. **Shared workers:** small, 4.
- **Provider capacity:** each provider admits a bounded number of concurrent in-flight attempts.
- **Request:** arrives with a tenant, a provider, and a deadline tick.
- **Attempt:** one dispatch of a request to its provider. Requires a worker and a provider slot.
- **Provider outcome:** success, retryable with a retry-after delay, or fatal.
- **Events:** arrival, provider response, retry becoming eligible, deadline expiry, cancellation, worker
  release, fault start, fault end.

Two providers and three tenants are the minimum that expresses the coupling: one faulty and one healthy
provider, one tenant concentrated on the faulty provider, one on the healthy provider, one spanning both.
A third provider adds no new interaction.

## 6. The shipped design and why each part is defensible

The starter is a plausible production gateway. No mechanism is absurd on its own; several are what a
competent engineer would write.

- **M1. Shared worker pool with one FIFO ready queue.** Simple, work-conserving, avoids per-provider
  under-utilisation.
- **M2. Deadline propagation, with timeout classified retryable.** A timed-out attempt is a transient
  outcome, and transient outcomes are retried. This is standard and is exactly the classification that
  produces amplification under congestion.
- **M3. Per-request retry budget.** Bounds attempts per request. Looks like it bounds amplification.
- **M4. Retry-after honoured through a delay wheel; on waking, the attempt re-enters at the head of the
  ready queue.** Defensible: the attempt already waited once, so re-queueing it behind newer arrivals
  penalises it twice. Real systems make this choice to avoid starving retried work.

## 7. The metastable chain

```
provider A degrades and returns retry-after
  -> A's attempts sleep, wake, and re-enter at the queue head (M4)
  -> they preempt fresh work for both providers (M1 + M4)
  -> fresh work for the HEALTHY provider waits and passes its deadline
  -> deadline expiry is classified retryable (M2) and re-enters
  -> queue occupancy is now sustained by internally generated work
  -> per-request budgets (M3) still hold, but arrivals keep adding requests,
     so AGGREGATE in-flight work is unbounded
  -> provider A recovers
  -> the accumulated backlog still holds the queue head, still preempts fresh work,
     which still times out, which still retries
  -> the degraded state persists with no external cause
```

The trigger is M4 interacting with M1. The **sustaining** mechanism is M2: timeouts minting retryable
work. That distinction matters for difficulty, see section 12.

## 8. Trivial-solution attack

Each candidate, and the deterministic counterexample that defeats it.

| Pattern | Why it fails |
|---|---|
| Exponential backoff | Changes when retries wake, not who they preempt. Healthy work still starves during the fault, and slower retries make recovery worse, not better. |
| Jitter | Spreads wake ticks. Ownership and ordering are unchanged. |
| Fixed retry cap | Already present as M3. A per-request bound does not bound aggregate work while arrivals continue. This is the central trap. |
| Global queue bound | Sheds by arrival order, so during the fault the queue is full of faulty-provider work and healthy-provider arrivals are dropped. Fails isolation and the healthy floor. |
| Provider-local queues | The strongest single pattern and the one to guard hardest. Fixes most of isolation. Does not bound aggregate amplification and does not stop timeouts minting retries, so recovery still fails. |
| Circuit breaker | Fast-fails the faulty provider, so requests whose deadline extends past recovery are failed although the contract says they must complete. Fails eligible-completion. |
| Bulkheads | Reserving workers per provider fixes isolation, leaves amplification and the timeout loop untouched, and under-utilises when one provider is idle, which the healthy floor penalises. |
| Global retry token bucket | One shared bucket means faulty-provider retries consume the tokens a healthy-provider retry legitimately needs. Fails isolation. |
| Disable retries | A single retry-after followed by success within deadline must complete. Fails eligible-completion. |
| Serialize to one worker | Fails the healthy-load floor. |
| Reject under load | Fails the healthy-load floor. |
| Larger queues | Increases the backlog the fault can build. Strictly worse. |
| One worker reserved per provider | Bulkheads, above. |
| Priority: fresh over retries | Fixes head-of-queue preemption, then starves retried work past its deadline. Fails eligible-completion. |

No single pattern satisfies the contract. What does: isolation domains that own **both** queueing and
worker capacity; attempt budgeting charged to the domain in aggregate rather than to the request; a
retry's charge held against its domain **while it sleeps**, so pending retries are not free; terminal
requests invalidating their pending work; and bounded fairness between fresh and retry work inside a
domain so neither starves. That is a resource-ownership invariant, not a keyword, and it cannot be
reached by tuning constants.

## 9. Resource model

Accounting the public contract makes explicit, without prescribing a data structure:

- admitted requests, live requests, terminal requests;
- attempts: queued, ready, running, sleeping-pending-retry;
- provider capacity and shared worker capacity;
- isolation domain, defined by the public contract as the (tenant, provider) pair a request belongs to.

The conservation-style statement the contract states as a consequence, not as an implementation:

> An admitted request may own at most a bounded quantity of future execution work, and that work remains
> charged to its isolation domain from admission until the request reaches a terminal state, including
> while it is sleeping between attempts.

Grading is on observable consequences: attempts actually dispatched to providers, completions,
rejections, and the tick each occurred at.

## 10. Progress, recovery and the healthy-load floor

No vague "eventually". Bounds are derived from published capacities.

**Recovery.** Given a fault of at most F ticks ending at tick E, and offered load after E below documented
sustainable capacity, then by tick E + R (with R derived from worker count, provider capacity and the
retry-after values, all public) the count of in-flight attempts attributable to fault-generated work is
zero, and healthy-provider requests admitted after E complete within their deadlines.

**Healthy-load floor.** On a schedule with no fault and offered load within capacity, every admitted
request completes before its deadline, and no request is rejected. This is what makes reject-everything,
disable-retries, permanent-breaker and serialize-everything fail. It is stated as a counted set on a
published control schedule, not a latency percentile.

**Eligible completion.** A request whose provider is healthy for a sufficient window before its deadline
must complete. This is what defeats over-aggressive shedding.

No threshold is chosen to fit a hidden test. Every bound is computed from published capacities in the
contract, and the verifier computes the same quantity the same way.

## 11. Cancellation and deadlines

Included because they reinforce the same invariant rather than adding a second puzzle. A cancelled or
expired request is terminal, and terminal closure means no provider attempt attributable to it may occur
afterwards. This exposes implementations whose delay wheel keeps minting work for requests that are no
longer live, which is the same accounting failure as the sleeping-retry charge.

Cancellation is not separately graded beyond that clause.

## 12. Fastest plausible Opus path

Being deliberately pessimistic, and assuming Opus recognises metastable failure patterns immediately,
because it will.

**First 30 minutes.** Reads the gateway, the contract, the event model. Runs the shipped fault schedule,
sees the collapse in the metrics. Writes its own schedule generator, which we make easy. Recognises the
pattern by name. Attempts the canonical fix: per-provider bulkheads plus a retry budget plus backoff.

**Where the first patch breaks.** Isolation improves and the fault schedule may even pass. The control
schedules do not: aggregate amplification is still unbounded because timeouts mint retryable work, and
recovery still fails on a schedule where the backlog outlives the fault. The agent must find that the
**sustaining** mechanism is deadline-expiry classification, not the retries it already fixed. That is the
trigger-versus-sustainer distinction, and it is the part a pattern-matching pass misses.

**Second phase.** Fixing the timeout classification exposes the next tension: hard isolation under-utilises
and fails the healthy floor, while work-conserving sharing reintroduces cross-domain consumption. Resolving
that requires domain-scoped accounting with charge-held-while-sleeping, which is a restructure.

**Estimate: 1 to 2 hours, with 2h+ credible** if the second phase is not seen quickly. I do not think
under 30 minutes is credible, so kill criterion 9 is not triggered. I also do not think this is
comfortably beyond frontier, and section 16 says so plainly.

## 13. Human expert path

A strong SRE reads the metrics, identifies the feedback loop, separates the trigger (a bounded provider
fault) from the sustaining mechanism (internally generated work re-entering faster than it drains), draws
who owns which capacity, notices that the per-request budget bounds nothing in aggregate, and designs
domain-scoped admission with retry work charged to its domain across sleeps. Then argues recovery from
drain rates against published capacity. Systems judgment throughout; no obscure API knowledge. Estimated
4 to 6 hours for a focused expert who knows the answer.

## 14. Public contract

`instruction.md` stays short: the observed incident, where the gateway lives, the command that reproduces
it, and a pointer to the contract. No symptom checklist this time, which is the ADR-002 mistake Phase 1A
repeated.

The contract document defines precisely: admission; attempt; retry; terminal request; provider capacity;
worker capacity; isolation domain; logical time and event ordering; the four graded consequences with
their derived bounds; healthy-load assumptions; what rejection is permitted and when. It does not say
bulkhead, retry budget, fair scheduler, or anything else naming the reference architecture.

## 15. Verifier strategy

The Phase 1A harness carries over almost unchanged. What is new is schedule generation and independent
accounting.

**Hidden schedules are instances of public rules.** The contract publishes the event grammar and the
capacity model. A hidden schedule is a particular sequence of the documented event types, not a new rule.
Families, all seeded and deterministic: targeted adversarial schedules for each consequence; small
exhaustive enumerations where the state space permits; seeded randomized workloads across fault
start/end, retry-after, capacity, worker count, deadline distribution, tenant mix and burst timing.

**The verifier does not trust self-reported metrics.** This is the task-specific cheat answer. Provider
adapters are verifier-owned, so every attempt is observed at the boundary the verifier controls, and
amplification, isolation, recovery and terminal closure are all derived from the observed attempt stream
rather than from anything the gateway prints. A gateway that lies about its queue depth changes nothing.

Everything else is reused: whole `/app` artifact treated as hostile, symlink rejection, privilege-bit
stripping, pytest at root, subject subprocess at uid 65534 with no-new-privs, root-only fixtures and
reward, and the reward-zero versus infrastructure-error distinction. Binary reward.

## 16. Oracle strategy

A reference policy explainable from four principles: admission bounded per isolation domain; every attempt
charged to its domain from admission to terminal state including while sleeping; fresh and retry work
scheduled inside a domain under bounded alternation so neither starves; terminal states invalidating
pending work. Correctness is argued against the public model rather than tuned against the schedules.

The starter must not telegraph it. No file named `bulkhead.py`, no comment about budgets, no dead
parameter waiting to be enabled.

## 17. Language

**Recommend Python, against the brief's stated preference, on repository evidence.**

The brief's rationale for TypeScript is asynchronous request lifecycles, cancellation and scheduling. Our
own design forbids real asynchrony: it is a deterministic event queue with integer ticks and no threads,
no promises and no wall clock. The TypeScript advantage is for the thing we have deliberately removed.

Against that, Python costs less incidental complexity here: the entire verifier harness, the CTRF and
pytest tooling the checks enforce, and every reusable piece from Phase 1A are Python; there is no build
step to pin or reproduce; and no `node_modules` tree lands in the `/app` artifact, which the
`artifact_efficiency` criterion specifically flags. With a third of the trial gone, that matters.

The verifier invokes the subject through a CLI subprocess either way, so this is a cost decision, not an
architectural one.

## 18. Originality

Scanned the 74-task corpus for retry storm, thundering herd, stampede, metastability, backpressure,
circuit breaker, bulkhead, load shedding, admission control, token bucket, retry budget, retry-after,
fair scheduling, concurrency limit, queue depth and starvation. Every load-bearing term returns nothing.
The apparent hits are substring noise.

| Neighbour | Its crux | Distinction |
|---|---|---|
| `payments-pipeline-fix` | Cold-start state rebuild with exactly-once notifications under a latency bound | Closest on framing. Its difficulty is rebuilding state fast after restart; nothing feeds back, and there is no contention between tenants or providers. |
| `live-database-cutover` | CDC under live traffic with latency parity | Has load and latency, but the difficulty is migrating without dropping requests, not stabilising a feedback loop. |
| `wal-recovery-ordering` | Durability prefix ordering | Single engine, no contention, no overload. |
| `session-window-debug` | Watermark and GC correctness, including a stall | Mentions stalling output, but the cause is watermark logic, not resource contention. |
| `distributed-dedup` | Spark dedup within resource budgets | Budgets are a constraint on a batch computation, not a dynamic feedback system. |

No task grades stabilising a retry and admission feedback loop under bounded partial failure. Mechanism
overlap with `payments-pipeline-fix` on retries and workers; central-capability overlap, none.

## 19. Seven-day feasibility

**Reused unchanged:** verifier harness (`sanitize.py`, `runner.py`, test-script structure, reward and CTRF
ownership), multi-stage verifier Dockerfile, environment Dockerfile pattern, hostile-artifact boundary,
`cheat/solve.sh` shape, task metadata skeleton, Harbor oracle and nop commands, the synthetic security
cheat set, and the documentation workflow.

**Net new:** the deterministic event simulator, the gateway itself, observability, verifier-owned provider
adapters and the observed-attempt accounting, the schedule families, the contract document, and the
reference policy.

Honest estimate: **1.5 to 2.5 days to first calibration.** With roughly five days left, one calibration
plus one hardening round, eight required trials, and a write-up, this fits only if the first calibration
lands well. Codex remains blocked on a personal plan upgrade gating three standard and one adversarial
trial, and that decision is now on the critical path rather than adjacent to it.

## 20. Thin slice for Phase 2A

Two providers, three tenants, four workers, one bounded fault, retries, deadlines, cancellation, the
deterministic event queue, one schedule that demonstrates metastability, one healthy control schedule, the
reference policy, nop, and the hardened verifier. Then one Opus calibration immediately, before any hidden
schedule matrix exists.

## 21. Kill criteria

1. Any assignment of constants to the shipped architecture satisfies the contract. Checked before
   implementation, per section 4.
2. One textbook pattern satisfies all graded consequences.
3. Correctness depends on real timing.
4. The verifier has to trust agent-reported accounting.
5. The healthy-load floor cannot be derived from published capacities.
6. A hidden schedule needs a rule that is not public.
7. A corpus task grades the same central capability.
8. Realism requires external infrastructure.
9. The paper fast path for Opus is under 30 minutes.
10. First calibration cannot be reached in about one focused engineering day beyond the simulator.
