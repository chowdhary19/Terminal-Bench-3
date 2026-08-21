# Redesign 1B: sandbox-upgrade-chain

Design only. Nothing under `tasks/` changes on the strength of this document.

Working name: `sandbox-upgrade-chain` (three slug tokens, at the cap the slug check enforces).

## 1. Production incident

A sandbox platform restores archived customer sandboxes into the current runtime. The archive spans
several product generations and the legacy runtimes were decommissioned when their releases went end of
life, so the only way a historical sandbox becomes usable is to migrate its snapshot forward through the
declared version chain.

Each migration shipped with its release, was reviewed against that release's requirements, and still
passes the regression tests it shipped with. Restores complete without error and the resulting sandboxes
look healthy: accounts and tickets are present, reads work, ordinary writes work.

Days later the workflow misbehaves. A ticket escalates at a logical time that matches nothing in its
history. A retried request returns a confident answer the platform has no record of ever giving. A ticket
restored from the oldest archive generation is attached to a customer that has nothing to do with it.

Every migration in the chain is correct for the pair of versions it was written for. The chain is not.

## 2. Central invariant

> Every supported historical snapshot must migrate through the declared version chain into a current
> sandbox that is a valid continuation of the logical workflow state that snapshot represents.

The insight the task is built to measure:

> Local correctness of version-to-version migrations does not imply correctness of their composition,
> because an intermediate version's model can fail to represent state that a later version needs.

## 3. Why the first version failed

The Opus 5 calibration returned reward 1 in 10 minutes 30 seconds, at 8.75% of the time budget, with no
wrong turns, and produced code better than the reference solution. The decisive advantage was not clue
density. It was that a correct v1 runtime shipped in the same repository as the broken restore path, and
the graded property was "restored v2 behaves like continued v1". That let the agent build the verifier
itself: it wrote a differential fuzzer over 2,300 randomly generated worlds in about three minutes, then
worked a mechanical find-and-fix loop that frontier agents are extremely good at.

Three secondary clues compounded it and are worth recording because they must not recur. The instruction
enumerated the three symptoms one-to-one with the three defects, which violated ADR-002. The
specification stated all three contract clauses normatively, which is load-bearing for fairness and was
correct. The three defects sat in three adjacent helpers in one 130-line file.

Removing all three secondary clues would not have saved that run. The fuzzer finds divergences without
knowing what to look for. The oracle is the thing to remove.

## 4. Legacy evidence available to the agent

Everything here has an operational reason to exist in a real archive-restore codebase.

| Artifact | Why it exists |
|----------|---------------|
| `current_runtime/` | The v3 runtime, executable and correct. It is the product. |
| `docs/format-v1.md`, `docs/format-v2.md`, `docs/format-v3.md` | Snapshot **format** references: field-by-field meaning, ranges, encodings. Written when each generation was archived. |
| `docs/runtime-contract.md` | Normative behavioural semantics of the **current** runtime only. |
| `docs/releases/*.md` | Release notes stating each migration's design decisions and why they were acceptable at the time. |
| `migrations/` | Source for `v1_to_v2` and `v2_to_v3`, per component. |
| `migrations/tests/` | The per-release regression tests each migration shipped with. They pass. |
| `snapshots/` | A handful of small example archives: two v1-origin, one v2-origin. |
| `incidents/` | A restore log, two support incident reports, one continuation transcript captured from a live v1 sandbox before decommissioning, and current-runtime diagnostic output. |

## 5. Legacy evidence deliberately unavailable

- No v1 or v2 executable runtime, and nothing from which one can be derived.
- No normative behavioural specification for v1 or v2. Historical versions are documented as **data
  formats with field meanings**, which is what survives a decommissioning. Behaviour over time is
  specified only for the runtime that still exists.
- No known-good full migration, in the repository or the verifier image.
- No generator, no expected states, no arbitrary ground-truth query of any kind.

The distinction in the second point is the hinge of the whole design and is argued in section 12.

## 6. State models

Four state domains, each with its own component schema version in the snapshot manifest.

**CRM.** v1: accounts keyed by `ref`, merges recorded as `merged_into` on the loser. v2: introduces
`party_id` as internal identity and demotes `ref` to a **reassignable display alias**, with an
`alias_refs` history carrying `released_at_tick`. v3: keeps `party_id`, and inbound links are by
`party_id` only.

**Support.** v1: tickets carry `age_ticks`, a coarse **bucketed** age at snapshot time (v1 recorded age
in bands rather than an absolute open tick; the format doc gives the band bounds). v2: tickets carry an
absolute `opened_at_tick`. v3: same, and `opened_at_tick` becomes the **SLA epoch** that scheduling is
measured from.

**Operations.** The completed-request ledger. v1: receipts carry `request_id`, `operation`,
`argument_fingerprint`, `recorded_at_tick`, and either a full `result` body or, for receipts past the v1
retention window, only a `result_digest` with the body pruned. v2: receipts require a full body. v3:
receipts carry a body plus attestation against the digest, and a receipt whose body cannot be attested is
recorded body-absent.

**Scheduler.** v1: queue entries carry `remaining_ticks` relative to the snapshot tick, plus
`policy_revision`. v2: absolute `deadline_tick`. v3: `sla_offset` relative to the ticket's SLA epoch.

### Origin profiles

Component versions are per-component because component migrations shipped on separate releases, which is
how staged rollouts actually work. To keep the space comprehensible, only the release profiles the fleet
actually produced are supported, and they are named and documented:

| Profile | crm | support | operations | scheduler |
|---------|-----|---------|------------|-----------|
| `2023.1` | v1 | v1 | v1 | v1 |
| `2023.2` | v2 | v1 | v1 | v1 |
| `2024.1` | v2 | v2 | v2 | v2 |

`2023.2` exists because the CRM migration shipped a release ahead of the others. Arbitrary combinations
are not supported and the manifest declares its profile.

## 7. Migration-chain architecture

```
snapshot/          document reader, manifest, profile resolution
migrations/
  v1_to_v2/        crm.py  support.py  operations.py  scheduler.py
  v2_to_v3/        crm.py  support.py  operations.py  scheduler.py
  chain.py         profile -> ordered stage list
  tests/           the per-release regression suites, still passing
current_runtime/   the v3 sandbox: domain, store, scheduler, ledger, CLI
docs/              formats, release notes, runtime contract
incidents/         restore log, support reports, transcript, diagnostics
snapshots/         example archives
```

`sandboxctl restore` resolves the manifest profile, runs the stages, and writes a v3 store.

## 8. The locally plausible decisions

Each of these is defensible on its own terms and is stated in the release notes of the version that made
it.

**D1, support v1 to v2.** v1 has no absolute open tick, only a bucketed age band. The migration sets
`opened_at_tick = 0`. The 2024.1 release notes record that v2 used `opened_at_tick` only for display
ordering in the ticket list, so an approximate value was acceptable and zero was chosen as an explicit
"unknown".

**D2, operations v1 to v2.** v2 requires a full receipt body. For receipts the v1 retention window had
pruned, the migration synthesises a body from current entity state. The release notes record that v2
retry semantics were documented as best effort, so a reconstructed body was within contract.

**D3, support v2 to v3.** Tickets link to parties. The migration resolves `account_ref` through the CRM
`ref` index as it stands at the end of the CRM migration. Correct for v2-native data, where a ref that
appears in the index belongs to the party it currently names.

**D4, scheduler v2 to v3.** `sla_offset = deadline_tick - ticket.opened_at_tick`. Correct arithmetic
against v2 state.

## 9. How composition creates the failure

**Surface C, pending deterministic work.** D1 and D4 compose badly. v3 measures scheduling from the SLA
epoch, so a `2023.1` or `2023.2` sandbox arrives with every SLA epoch at zero and every escalation
mis-timed. Neither D1 nor D4 is wrong: D1 was fine while `opened_at_tick` was cosmetic, and D4 is correct
arithmetic. The composition is wrong because v2's model did not treat as load-bearing something v3 later
made load-bearing.

**Surface A, logical identity across domains.** D3 composes badly with v2's demotion of `ref` to a
reassignable alias. A ref released by a v1-era merge and later reassigned to a different party resolves,
under the current index, to the wrong party. The correct resolution is point in time: the party that held
that alias when the ticket was opened, which the CRM `alias_refs` history records. Note this repair needs
a correct open tick, so it is downstream of Surface C without being repaired by it.

**Surface B, completed-operation outcomes.** D2 composes badly with v3's attestation rule. A synthesised
body reaches v3 and is presented as a genuine historical envelope, so a retry of a pruned request returns
a fabricated success. The runtime contract says a receipt whose body cannot be attested against its digest
is body-absent and a retry against it returns `receipt_unavailable`. Fabricating is worse than failing.

## 10. Why every repair is independently necessary

The three repairs differ in kind, which is what stops one rewrite from covering them:

- **C is a reconstruction.** The open tick is not in the snapshot in usable form and must be derived.
- **A is a point-in-time resolution.** The data is present and complete; the wrong instant is being asked
  about.
- **B is a representation of absence.** The data is genuinely gone and the correct action is to say so
  rather than invent it.

Carrying more fields forward from v1 does not touch A or B. A time-aware resolver does not touch B or C.
Refusing to fabricate bodies does not touch A or C.

The reconstruction in C deserves its own note, because it is what raises repair cost above field copying.
`age_ticks` is bucketed and does not pin the open tick by itself. The tick is determined by intersecting
three constraints: the band bounds from `age_ticks` against the snapshot tick; the `recorded_at_tick` of
the `ticket.open` receipt when that receipt survived the retention window; and, when it did not, the
effective window of the `policy_revision` the scheduler entry was queued under together with
`remaining_ticks`. The intersection is a single tick. This is an ordinary backfill of a column from
sibling tables, and it is real work rather than a rename.

## 11. Fastest credible Opus path, pessimistically

1. Read the tree, all migrations, all format docs and release notes. **15 minutes.**
2. Restore an example v1-origin archive, run the v3 runtime, diff against the captured continuation
   transcript in `incidents/`. Divergences appear. **20 minutes.**
3. Surface B. The v3 attestation rule and the synthesising branch are both explicit once read together.
   **20 to 40 minutes.**
4. Surface A. Requires noticing that refs are reassignable in v2 and that resolution must be anchored in
   time. The alias history makes this findable. **30 to 60 minutes.**
5. Surface C. Requires seeing that a cosmetic default became load-bearing two versions later, then that
   the obvious source is bucketed, then constructing the three-source intersection and handling the
   pruned-receipt case. **60 to 120 minutes.**
6. Verification without an oracle. Only the finite transcript is available, so the agent must reason its
   way to confidence. This is where premature completion becomes likely.

Pessimistic floor if all three land quickly: **about 90 minutes.** Central estimate: **2.5 to 5 hours**,
with real failure probability concentrated in step 5 and in step 6.

That clears the one-hour warning line, but not by a comfortable margin, and I would rather say so than
present a flattering number. Section 18 turns this into a kill criterion.

## 12. Fairness argument

The task is fully specified, and the mechanism that makes it so is the split between format and
behaviour.

Historical versions are documented as **data formats with field meanings**: what `age_ticks` bands mean,
what `result_digest` attests, what `released_at_tick` records, what each migration's release notes say it
decided and why. That is exactly the documentation that survives a decommissioning, and it is enough to
determine the correct migration.

The **current** runtime has a complete normative behavioural contract, because it is the thing that must
behave correctly afterwards, and because every graded assertion is about its behaviour.

What is deliberately not provided is a normative behavioural specification of v1 or v2 over time, because
that is what would let the agent reimplement a legacy runtime and rebuild the unlimited oracle. A field's
meaning is not a runtime. Knowing that `remaining_ticks` counts down to an escalation does not let anyone
generate authoritative v1 worlds on demand.

Every graded assertion maps to `docs/runtime-contract.md` or to a stated format meaning. Two competent
readers given these documents write the same migration.

## 13. Verifier strategy

Reuse the Phase 1A architecture, with one improvement.

**Generate forward from ground truth.** The build stage constructs the intended v3 world natively with
the v3 runtime, then **emits** a historical snapshot representing it, applying exactly the losses the
generation boundary implies: bucket the age, prune receipts past the retention window, release aliases.
Expected continuation outputs come from running the v3 runtime on the true world.

The consequence is strong and better than Phase 1A: **no correct forward migration exists anywhere in the
verifier image, not even in the discarded build stage.** The build stage holds an emitter, which is the
inverse of the task, and inverting it is the work. The reference migration lives only in `solution/`.

Hidden cases must cover snapshots originating at `2023.1`, at `2023.2` (so the staged-rollout path is
exercised), and at `2024.1` (so a fix that special-cases v1 origins does not pass), plus cross-domain
state and post-migration continuation.

Build-time self-tests: byte-identical regeneration, emitted snapshots parse under the documented reader,
and the shipped chain does not already satisfy the case.

Everything else carries over unchanged: whole `/app` artifact treated as hostile, symlink rejection,
privilege-bit stripping, pytest at root, subject subprocess at uid 65534 with no-new-privs, root-only
fixtures and reward, and the reward-zero versus infrastructure-error distinction.

## 14. Oracle strategy

`solution/solve.sh` installs corrected migration modules from `solution/files/`. Three files, in three
different component migrations across two hops, which is itself evidence that the repairs are not one
change: `v1_to_v2/support.py` (epoch reconstruction), `v2_to_v3/support.py` (point-in-time alias
resolution), `v1_to_v2/operations.py` and `v2_to_v3/operations.py` (attested bodies and honest absence).

## 15. Reuse from Phase 1A

Surviving essentially unchanged: the verifier harness (`sanitize.py`, `runner.py`, the test-script
structure, the reward and CTRF handling), the multi-stage verifier Dockerfile pattern, the environment
Dockerfile pattern, the whole anti-cheat boundary, `cheat/solve.sh`, and the task metadata shape. All of
it is validated and none of it was implicated in the calibration failure.

Surviving with rework: the domain model, the store layer and the CLI become the v3 runtime. The Phase 1A
snapshot module becomes the v1 and v2 format readers.

Discarded: the single-hop restore, the demo sandbox as a trap, the symptom list in the instruction, and
the v1 runtime itself.

## 16. Implementation estimate

| Piece | Estimate |
|-------|----------|
| v3 runtime (adapted from Phase 1A) | 0.3 day |
| Formats, migration chain, four component migrations per hop | 0.5 day |
| Documentation: three format refs, release notes, runtime contract | 0.4 day |
| Incident artifacts and example snapshots | 0.2 day |
| Verifier: snapshot emitter, fixtures, self-tests | 0.3 day |
| Oracle, cheat oracle, gate runs and iteration | 0.3 day |

About **two days** to Phase 1A-equivalent gates. Against a seven-day trial with day one spent, that
leaves roughly two days for calibration and hardening and two for the eight required trials and the
write-up. Codex remains blocked on a personal plan upgrade, which is on the critical path for three
standard trials and one adversarial trial.

## 17. Semantic-overlap audit

Re-ran the neighbour scan over the 74-task corpus for multi-hop migration, migration composition,
skipped-version upgrade, backward-compatible restore, historical schema evolution, and independently
versioned component state. The phrases "migration chain", "skipped version", "intermediate version",
"upgrade chain" and "schema generation" return nothing.

| Neighbour | Its crux | Distinction |
|-----------|----------|-------------|
| `live-database-cutover` | CDC under live traffic, latency parity after a dialect port | Single hop, both engines running, difficulty is concurrency and performance. Ours has no traffic and no live source. |
| `wal-recovery-ordering` | Durability prefix ordering in one engine | One log, no schema change, everything graded is present in the snapshot. |
| `payments-pipeline-fix` | Cold-start latency with exactly-once notifications | Restart, not restore. Same storage, same identifiers, no version boundary. |
| `risk-scorer-replay` | Black-box behavioural inference from a live probe binary | The closest on evidence shape and the one to watch. It hands the agent an oracle it may query during development; we hand it a finite artifact set and no executable legacy. Its crux is inferring an unknown function; ours is composing known transformations. |
| `mp-checkpoint-consolidation` | Inverting a sharding scheme | Static one-shot conversion verified numerically. Nothing continues running. |
| `vba-userform-port` | Porting a legacy app to a new stack | The legacy source is the source of truth and is fully available, which is the opposite of our constraint. |

Nothing in the corpus grades migration composition across a version chain.

## 18. Kill criteria

Abandon this design if any of the following becomes true during implementation.

1. The format documentation cannot describe field meanings without amounting to a behavioural
   specification of v1, at which point the oracle returns.
2. The three surfaces collapse: one rewrite, or one generic passthrough, repairs more than one.
3. The reconstruction in Surface C turns out not to be determinate on some supported profile, which makes
   the task unfair rather than hard.
4. A calibration trial solves it in under an hour, or solves it cleanly at all with time to spare.
5. Difficulty ends up resting on the agent not being able to check its work rather than on the reasoning
   being hard. Removing the oracle is meant to stop free verification, not to make correctness
   unknowable.
6. The verifier cannot distinguish a real migration from a bypass.
7. Implementation exceeds three days, at which point the trial schedule is at risk and shipping the
   already-working Phase 1A task with honest documentation of its difficulty becomes the better outcome.
