# Build gate: tool-session-failover

Verdict: **rejected during build.** The starter was implemented and the failure geometry proven real,
then the repair was measured and came to **29 lines of entirely standard patterns**. The task directory
was removed; the prototype is preserved in scratch at `/tmp/tsfspike`.

Elapsed: about forty minutes, because the failure was modelled before the verifier was built. That is
ADR-032 working as intended.

## What was built

A real multi-process system, not a simulation.

- **Provider** (`provider/service.py`, ~150 lines): an HTTP service performing a state-mutating operation
  with a durable effect log, deduplication on a caller-supplied idempotency key, a `/status` lookup, an
  `/effects` inspection endpoint, and a `hang_after_effect` chaos hook that commits the effect and then
  holds the response open.
- **Gateway** (`gateway/service.py` + `store.py`, ~256 lines): an HTTP service accepting logical tool
  calls, driving them against the provider, and exposing a resumable event stream. Multiple OS processes
  share one SQLite database in WAL mode.
- **Scratch driver**: launches both, drives the client, kills and restarts gateway processes, and
  inspects the provider effect log and the database directly. Fully deterministic; sequencing comes from
  polling durable state, never from sleeps.

Three identities were kept distinct as specified: `logical_call_id`, `provider_attempt_id`, `event_id`.

## The failure geometry is real

Measured, not assumed.

| Scenario | Starter behaviour |
|----------|-------------------|
| Happy path | 3 events, 1 effect, clean |
| **F1** provider commits the effect, gateway dies before recording completion, client retries | **2 effects.** At-most-once violated, because the idempotency key was derived from `provider_attempt_id`, which is fresh per attempt |
| **F3** client reconnects to a different gateway process | Event replay works, but `GET /calls/{id}` returns **404**: terminal state lives in a process dict |
| **F4** cancel arrives at a different process while an attempt is in flight | Cancel returns **404**; no terminal outcome is ever recorded |

One of the four intended surfaces did not hold up. Event replay across processes already worked, because
events were being written to the shared database. The apparent breakage was accidental: a second process
re-allocated event ids from 1 and `INSERT OR IGNORE` silently dropped the collisions, which happened to
leave a correct suffix. That is not a gradeable target.

## Why it is rejected

The complete repair is **29 lines**: 26 in the gateway service, 3 in the schema.

```
stable idempotency key derived from the logical call id      1 line
allocate event_id inside a transaction, commit terminal
  state atomically with its event                            8 lines
read call state from the database instead of a process dict  ~14 lines
persist the cancel request                                   ~6 lines
```

Verified: with that patch F1 holds at-most-once, F3 returns the terminal state from a different process,
and the happy path is unchanged. The one remaining gap, reconciling an attempt orphaned by process death
against provider status on startup, is another thirty or forty lines of the same kind.

Every item on that list is reflex for a backend engineer. "Put the state in the database instead of in
memory" is the first thing anyone says when told that gateway processes restart and clients reconnect
elsewhere. There is no insight to discover, only a well-known pattern to apply.

## The oracle problem, again

The task hands the agent a constructible oracle, which is the property that explains every rejection and
every probe pass in this project.

The provider exposes `/effects`. The database is inspectable. The agent can kill and restart its own
gateway processes. It can therefore write the scratch driver used here in about fifteen minutes, and
every counterexample localises the repair precisely: "2 effects" points at the idempotency key, "404"
points at the in-memory dict. Detection is cheap **and** construction is cheap.

Difficulty test, answered honestly:

1. Read the whole gateway in one context: yes, 256 lines.
2. Spot the defects by inspection: yes. `_provider_execute(attempt_id, attempt_id, ...)` passes the
   attempt id as the idempotency key on one visible line, and `_runtime: dict[str, CallRuntime]` holding
   lifecycle state is visible immediately. Both are recognise-the-antipattern, not reason-about-the-system.
3. Build a perfect oracle from what ships: yes.
4. One test exposing several surfaces: yes.
5. Does one repair fix several: yes, moving state to the database fixes three at once.

Estimated Opus 5 at maximum reasoning: **well under thirty minutes.**

## Trivial-solution attack, for the record

Not run, because it presupposes the intended solution is hard. The list would have been answered by the
same 29-line patch, which is itself the finding.

## Originality

Clean, and not the binding constraint. `idempotency key`, `at-most-once`, `reconnect`, `replay from` and
`process restart` return nothing across the 74-task corpus. `failover` hits only `payments-pipeline-fix`,
whose crux is worker startup latency with correctness as a constraint rather than durable lifecycle
across process death. The family was rejected on difficulty.

## What this adds to the record

Sixth family, same finding, reached faster than any previous one.

| Family | Settled by | Cost to settle |
|--------|-----------|----------------|
| Sandbox migration | live Opus calibration | ~2 implementation days |
| Migration chain | generalisation | (folded in) |
| Gateway metastability | 432-configuration sweep | an afternoon |
| Credit broker | 240-state exhaustive check | under an hour |
| Trajectory store | six baselines, three regimes | one measurement pass |
| **Tool-session failover** | **build the starter, measure the repair** | **forty minutes** |
