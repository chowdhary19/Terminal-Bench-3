# Design contract: sandbox-upgrade-continuity

Frozen at design level, revised after independent adversarial review. Changing anything under "Central
invariant", "Public observable contract", "Verifier architecture", or "Non-goals" means reopening this
document and adding a decision record.

Revision note: this supersedes the pre-review architecture. Six review findings were accepted as
blocking, and the sections on snapshot ownership, the reference lane, identity, pending work, and
artifact handling were rewritten rather than patched. Superseded decisions are recorded in
[DECISIONS.md](DECISIONS.md) rather than deleted.

## Production incident

A B2B software company runs per-team sandboxes for its customer platform. A sandbox is a self-contained
world holding that team's CRM accounts and support tickets, plus the bookkeeping the platform needs to
behave correctly: which client requests it has already carried out, what logical time it is inside the
sandbox, and what work it has scheduled but not yet performed.

Platform engineering is midway through moving sandboxes from the v1 storage layout to v2. Sandboxes move
by exporting a documented snapshot and restoring that snapshot into fresh v2 storage. Ops ran the path on
a customer-facing sandbox last night.

Nothing looked wrong. Accounts and tickets are all there with the right names, states, and references.
Reads return what they should. Simple writes work.

Then the reports started. A client that retried a request it had already made got back a different answer
than the first time. A ticket escalated at the wrong point. A ticket that had been moved during an account
merge came back attached to an account that no longer exists as a live entity.

The restore produced something that resembles the original sandbox and does not continue it.

### Why this is work someone is paid for

Sandbox and environment cloning is a shipped product feature at a lot of companies: test-mode data in
payment platforms, per-customer demo orgs in CRM vendors, database branching in managed Postgres, staging
environments restored from production. The two features the task leans on are native to that setting
rather than convenient for it. A logical clock exists because the point of a demo sandbox is
fast-forwarding it past a due date without waiting. Request identifiers exist because any API whose
clients retry needs them, and a sandbox platform's clients are automated.

## Central invariant

> A restored and upgraded sandbox must be a valid behavioral continuation of the original world, not
> merely a plausible copy of its visible records.

Operationally: for any sequence of commands, running that sequence against a restored sandbox must
produce the same observable results, and leave the same logical state, as running it against the original
sandbox that was never exported.

Three conceptual consequences, arising from one principle but requiring genuinely different repairs:

1. **Logical-identity continuity.** Relationships must resolve to the correct current logical entity,
   across regenerated physical identifiers and across a merge history.
2. **Completed-operation continuity.** A retried request must reproduce the result the original world
   recorded, not a result synthesised from present state.
3. **Deterministic pending-work continuity.** Scheduled work must fire at the logical time the original
   scheduling decision implied, under the policy revision that was in force when it was scheduled.

These are stated in the public specification as contract clauses. They are not enumerated in
`instruction.md` as a list of things to fix.

### Equivalence invariant

> The pristine baseline's documented v1 behavior and the intended correct v2 behavior are equivalent for
> the graded operation set.

v1 and v2 may differ in physical row identifiers, schema shape, normalization, where logically equivalent
state lives, and how derived state is represented. They may not differ in externally observable business
semantics for any operation that exists in both. The earlier "service tier captured at open time in v2,
read through the account in v1" idea is removed for violating this.

Build-time self-tests in the verifier image fail the build loudly if author-created fixtures violate the
invariant.

## Public observable contract

The tool is `/app/sandboxctl`, one CLI over one store path. The complete normative contract lives at
`/app/docs/SPEC.md` inside the task environment, which is the document the agent reads and the document
every hidden assertion maps to. `instruction.md` stays short and reports production symptoms.

Graded operation set:

| Command | Meaning |
|---------|---------|
| `account create` | Create a CRM account with a stable customer-facing reference |
| `account merge` | Merge one account into another; the source becomes a non-live alias |
| `ticket open` | Open a support ticket against an account reference, scheduling an escalation |
| `ticket resolve` | Resolve a ticket and cancel its pending work |
| `tick` | Advance the logical clock, firing work that becomes due |
| `world dump` | Emit the full logical state as canonical JSON |
| `sandbox restore` | Build a v2 store from a v1 snapshot document |

`sandbox export` also exists, because it is how ops produced the snapshot in the incident and because the
agent needs it to build its own reproduction worlds. It is **not graded**. The verifier never consumes a
subject-authored snapshot for the restore grade.

Rules that hold for every command, each stated normatively in the spec:

1. Deterministic. No wall clock, no unseeded randomness, no dependence on file ordering.
2. Every mutating command carries a caller-supplied request identifier. A repeat with the same identifier
   and the same arguments returns the originally recorded result envelope verbatim. A repeat with the
   same identifier and different arguments is a conflict error.
3. Time advances only through `tick`. Pending work fires when the clock reaches its due tick, exactly
   once, in canonical order.
4. `world dump` output is normative, canonically ordered, and carries no physical storage identifiers.
5. `sandbox restore` writes a fresh target and the result is self-contained. It does not read the
   original store and does not require it to exist.
6. Behavior outside the restore path is correct and must keep behaving as it does.

## Domain model

Two domains. Two is enough to make cross-domain references real; three would add volume, not difficulty.

**Accounts** carry a stable customer-facing reference, a display name, a service tier, a liveness status,
and, when merged, the reference they were merged into. Display names are not identifiers and may repeat.

**Tickets** carry a stable reference, the account reference recorded when they were opened, a subject, a
state, and a priority.

**Policy revisions** are world data, not configuration. Each revision declares the tick it becomes
effective and the escalation delay per priority. The revision in force at any tick is the latest one whose
effective tick has been reached. Revisions are public and appear in the snapshot.

Three kinds of state exist, and telling them apart is the task:

- Records, which the read commands expose.
- Derived state, a pure function of records, recomputable at any time.
- Continuation state, which is neither: invisible to reads, not recomputable from records, and
  determinative of future behavior. Here that is the completed-request ledger with its recorded result
  envelopes, the clock cursor, and the pending queue with the scheduling context each entry was created
  under.

### v1 state

Records with physical row identifiers plus stable references. Accounts carry a merge pointer. Tickets
reference accounts by the physical row that was current at open time. A request ledger keyed by request
identifier holds the operation, an argument fingerprint, and the immutable result envelope that was
returned. A clock row. A pending queue whose entries carry the ticket, the tick they were scheduled at,
and the policy revision in force at that moment.

### v2 state

The same two domains, reorganised. Physical identifiers are regenerated. Tickets resolve to the current
live account rather than to a merged-away row. The pending queue is normalised to a derived due tick. The
request ledger is stored in a normalised shape.

### Snapshot semantics

The snapshot is a **public, structured, non-runnable JSON document**. It is not a database file. Byte
copying it into the target path does not produce a working v2 world, because the runtime opens stores, not
documents.

The format is documented completely in `/app/docs/SPEC.md`. Hidden cases are hidden **instances** of a
public format, never hidden rules. There is no cryptography, no encoding trick, no compression puzzle, no
undocumented metadata, and no deliberately obscure serialization.

Snapshots used for grading are authored by the verifier at image build time. The subject never controls
both sides of correctness.

### Logical clock semantics

An integer tick counter with no relationship to wall time. `tick` advances it, firing due work in
canonical order as it goes. Two sandboxes at the same tick with the same state are indistinguishable,
which is what lets the verifier compare by exact equality with no tolerance.

### Completed-operation semantics

A mutating command whose request identifier already appears in the ledger is a retry. Same arguments
returns the recorded envelope verbatim. Different arguments is a conflict error. The recorded envelope is
historical: it reflects what the world returned when the operation first ran, and it does not track later
changes to the entities involved. Whether an implementation stores that envelope, or reconstructs it by
some other means, is its own business, as long as the behavior holds.

### Pending-work semantics

Opening a ticket schedules an escalation. Its due tick is `scheduled_at + delay(revision_in_force_at_scheduling, priority)`.
The scheduling decision is historical: a policy revision that takes effect later does not move work that
was already scheduled. Resolving a ticket cancels its pending work. Work whose due tick is already in the
past fires on the next `tick`, in canonical order, before newly due work.

## Intended exploration path

1. Read a small unfamiliar repository, read the specification, run the tool against the shipped sandbox.
2. Restore the shipped snapshot and diff. A ticket comes back attached to a merged-away account, because
   merge chains are followed one hop rather than to the live account.
3. Fix the resolution. The visible diff goes clean. This is a real repair and it is not sufficient.
4. Notice that the shipped material exercises none of the tool's other behavior across the restore
   boundary, and construct the missing experiments: retry a request that completed before the snapshot;
   restore a world whose policy changed after a ticket was scheduled, then advance time past the due tick.
5. Watch a retry return today's answer instead of the recorded one. Watch an escalation fire at the wrong
   tick.
6. Revise the model. The importer treats everything outside the records as derived, and some of it is not.
7. Make three genuinely different repairs at three sites, and verify.

Step 3 is the trap: a clean visible diff that is wrong.

## Intentional starter defects

One principle violated at three sites, each a plausible porting mistake rather than an omission. Nothing
is structurally missing: the snapshot carries every section, v2 has a home for every section, and the
importer writes every section. A reader who diffs v1 tables against snapshot sections against v2 tables
finds nothing absent.

1. **Identity.** The importer resolves a ticket's recorded account reference through a single merge hop.
   One hop covers the common case, so the shipped sandbox looks correct; a two-hop chain lands the ticket
   on a dead intermediate account.
2. **Receipts.** The importer carries the ledger but rebuilds each result envelope from the entity's
   current row rather than preserving the recorded envelope. Production rationale in the code: v2 stores
   receipts normalised and rehydrates results on read to avoid duplicating denormalised JSON.
3. **Pending work.** The importer derives the due tick with the escalation delay from the policy revision
   in force at snapshot time, rather than the revision recorded on the queue entry. The defect is a
   semantic translation error, not an ignored field: the entry's revision is read and stored, it is simply
   not the revision used for the delay lookup.

Deliberately not implemented as "snapshot contains due_tick, importer ignores due_tick".

The three repairs are decoupled. Fixing merge-chain resolution does nothing for envelopes or due ticks.
No single resolver change repairs two surfaces.

Code discipline: comments explain production rationale. No comment mentions tests, verification,
benchmarks, or a fix. No TODO points at a defect. No misleading comment is planted. No identifier is
obfuscated.

## Verifier architecture

Separate verifier, mandatory and enforced. **There is no runtime reference lane.**

### Build time

In a discarded Docker build stage:

1. Run a pristine baseline v1 implementation.
2. Generate deterministic hidden worlds from fixed seeds.
3. Execute hidden prefix workflows.
4. Emit **verifier-authored v1 snapshots** in the public format.
5. Execute hidden continuation workflows against the uninterrupted v1 worlds.
6. Record the expected transcript and the expected normalized final state.
7. Run self-tests: determinism across repeated generation, snapshot round-trip through the documented
   parser, and the v1/v2 equivalence invariant. The build fails loudly on violation.

Only the snapshots, transcripts, and expected states are copied into the final image. The baseline, the
generators, the seeds, and every transient database stay in the discarded stage. Multi-stage build rather
than same-layer deletion, because a discarded stage never ships at all.

A property worth stating: the expected outputs come from running the continuation on the **v1** world.
The verifier therefore never needs, and never contains, a correct v2 restore implementation. The answer to
the actual problem does not exist anywhere in the verifier image.

### Run time

No correct reference application exists. No reference lane executes. The verifier hands the subject the
verifier-authored snapshot for the case, the subject restores into a fresh v2 target, the verifier drives
the hidden continuation through the documented CLI, and compares observed behavior against the build-time
expectation.

Comparison is exact JSON equality after thin canonicalization: key ordering only. No fuzzy tolerance, no
silent field dropping, no semantic sorting beyond the collection orders the public spec defines. Physical
storage identifiers do not appear in normative output, so there is nothing to ordinalize and a broken
relationship shows up directly as the wrong logical reference.

Reward is binary. pytest emits CTRF, root derives the outcome, root writes exactly `0` or `1`.

### Artifact boundary and hostile-tree handling

`artifacts = ["/app/"]`, which is TB3-precedented. Narrowing does not remove hostile bytes because Harbor
materializes convention artifacts regardless, so containment happens in verifier handling.

Before any subject process starts, root-owned setup:

- creates `/logs/verifier` at mode 0700 and remains the sole writer of the reward and CTRF;
- inspects `/app` without following symlinks and rejects any symlink found;
- strips setuid, setgid, and sticky bits;
- normalizes only the executable modes the documented CLI needs;
- sanity-checks that the expected entry point is present;
- never runs `chown -R root:root /app` and never uses a default recursive copy on the hostile tree;
- leaves `/logs/artifacts` out of scope.

Two outcomes are kept strictly apart:

| Situation | Result |
|-----------|--------|
| Artifact transfer absent or broken | Verifier error. Non-zero exit, **no reward written.** Never laundered into a scoreable zero. |
| Artifact arrived but violates documented submission policy, for example a symlink or a setuid file | Deterministic reward 0 with a diagnostic. No crash. |

That distinction matters for both halves of the employer requirement: genuine model failures must be
distinguishable from infrastructure failures, and every `/cheat` run must produce a real zero.

### Subject process isolation

pytest orchestration stays root. Agent code is never imported into the verifier process; every invocation
is a subprocess through the documented CLI.

Each invocation runs with privileges dropped to an unprivileged user, an explicit clean environment, no
bytecode writing, isolated working and temporary directories, output captured to verifier-owned temp files
rather than inherited pipes, its own process group, and a process-group kill afterwards. No secret file
descriptors are inherited, no Docker socket is present, and no hidden fixture path appears in arguments or
environment. Fixture directories are root 0700 and fixture files root 0600, and a verifier self-test proves
the subject user cannot read the expected outputs.

No submitted process can write the reward, the CTRF report, the expected outputs, or any hidden fixture.

## Anti-cheat boundary

| Attack | Defence |
|--------|---------|
| Byte-copy the snapshot into the target | The snapshot is a JSON document, not a database. The runtime opens stores. A copy is not a world. |
| Author the snapshot and the restore, defining both sides | The graded snapshot is verifier-authored at build time. The subject's export is ungraded. |
| Alias the source world | No source store exists at verify time. The verifier holds a document, never a database. |
| Read hidden expectations | Fixtures are root 0700/0600. A self-test proves the subject user cannot read them. |
| Replay a correct implementation from the image | The image contains no correct v2 restore. Expected outputs were produced by v1 in a discarded build stage. |
| Fabricate the reward or CTRF | `/logs/verifier` is 0700 root-only before anything runs; root is the sole writer, deriving the verdict from child exit status. |
| Symlink or setuid smuggling through the artifact | Root-side pre-scan rejects symlinks and strips privilege bits, producing a deterministic 0 with a diagnostic. |
| Hardcode visible fixture values | The shipped sandbox uses different data from every graded world. |
| Canned output | Transcripts are compared against build-time expectations the subject cannot observe. |
| Daemonize and forge afterwards | Output to temp files, process-group kill, reward directory unreachable to the unprivileged user. |

`cheat/solve.sh` ships a deterministic reward-hacking oracle that must score 0, and the synthetic
adversarial set is exercised locally before any model trial.

## Non-goals

Concurrency and parallelism. Wall-clock time, sleeps, real schedulers. Network services, multi-container
topologies, HTTP interfaces. Third-party dependencies beyond the standard library. Performance or latency
requirements. A third domain or a fourth independent subsystem added to manufacture difficulty. Partial
credit. Any requirement on how the fix is implemented.

## Approximate expert solution horizon

Three to four hours for an engineer who already knows the answer. The final
`expert_time_estimate_hours` will be set from a measured implementation rather than from this estimate.

## Phase 1A scope

The smallest end-to-end slice containing all three surfaces: one small world, one cross-domain reference
crossing a two-hop merge, one completed operation retried after restore, one pending operation that
becomes due after restore under a superseded policy revision, one verifier-authored snapshot, one correct
continuation. One seed. No hidden matrix yet. The goal is proving the architecture and the failure
geometry before investing in scale.
