# Decision log

Short records. One decision each. Status is Accepted, Superseded, or Open.

---

## ADR-001: Build the task, with revisions

Decision. Proceed with `sandbox-upgrade-continuity`.

Context. The uniqueness scan read nine adjacent tasks in full. None grades the capability of deciding
which parts of a running system's state are logically load-bearing but invisible in its records. The two
closest neighbours own different cruxes: `payments-pipeline-fix` owns cold-start latency,
`session-window-debug` owns streaming watermark and GC correctness.

Alternatives. Reject on overlap. Pivot to a different domain.

Rationale. The gap is real and the domain is unoccupied. The revisions the scan forces are constraints on
implementation, not a different task.

Consequences. Three design constraints become binding: retries stay coupled to identifier translation,
pending work stays about carrying recorded due times, and the task is framed as continuity rather than
migration.

Status. Accepted.

---

## ADR-002: State the invariant, not the failure modes

Decision. `instruction.md` states one invariant and the public behavioural contract. It does not
enumerate identity, retry, and pending-work continuity as three things to fix.

Context. The three surfaces have to be discoverable, or the task is a checklist. The rubric also pushes
this way: `instruction_concision` fails instructions that hint at the approach, `outcome_verified` fails
instructions that enumerate steps.

Alternatives. List the three manifestations, which is easier to write and easier to verify as fair.

Rationale. The whole difficulty is realizing the partial repair is partial. Naming the parts removes it.

Consequences. `test_instruction_alignment` becomes load-bearing. Every graded behaviour must trace to a
numbered rule in the public contract, and the contract must be precise enough that a reader could write
the same tests. Verified by handing the instruction to a reader who does not know the solution.

Status. Accepted.

---

## ADR-003: Local CLI over SQLite, no HTTP layer

Decision. One CLI against a local SQLite store. No server, no ports, no compose file.

Context. The original direction allowed a small HTTP interface.

Alternatives. HTTP service. Multi-container with a sidecar store.

Rationale. An HTTP layer adds process lifecycle, port binding, readiness waiting, and a class of trial
flakiness, and contributes nothing to the state-continuity problem. SQLite is in the standard library,
which keeps the dependency surface at zero and makes `deterministic_reproducible` and
`environment_hygiene` easy. Multi-container is a Harbor capability, not a reason to use it.

Consequences. The verifier drives the subject entirely through subprocess invocations of one documented
entry point, which is also the cleanest place to drop privileges.

Status. Accepted.

---

## ADR-004: Logical clock, no wall time

Decision. Time advances only through an explicit `tick` command over an integer counter.

Context. Scheduled work has to survive the restore boundary, and scheduled work usually means timers.

Alternatives. Wall-clock timestamps with a frozen or injected clock.

Rationale. Two sandboxes at the same tick are exactly comparable with no tolerance windows, which is what
lets the verifier compare worlds by equality rather than by approximation. It also removes sleeps, timing
races, and machine-speed dependence from both the task and the harness.

Consequences. Scheduling is deterministic and reviewable. The cost is a slightly artificial product
surface, which is acceptable: internal sandbox and simulation tooling really is built this way.

Status. Accepted.

---

## ADR-005: Two lanes, pristine reference versus agent subject

Decision. The verifier runs a reference lane on the pristine baseline that never exports or imports, and
a subject lane on the agent's code that does the full round trip. It compares transcripts and final
state.

Context. Something has to define the correct continuation. Options were a hand-written reference
implementation in the verifier, golden transcripts baked at image build time, or running the pristine
baseline at verify time.

Alternatives considered.
- Independent reference implementation, as `risk-scorer-replay` does. Correct but expensive, and it
  duplicates the domain twice with the drift risk that implies.
- Golden transcripts baked into the verifier image. Cheap at verify time, but the transcripts silently
  drift out of sync with the generator whenever either changes.
- Compare the agent's restored world against the agent's own uninterrupted world. Implementation-agnostic
  and cheap, but an agent that degrades both sides identically passes.

Rationale. Running the pristine baseline at verify time cannot drift, needs no second implementation, and
gives ground truth the agent cannot influence. Comparing the pre-snapshot prefix as well as the
continuation makes symmetric degradation fail, because the reference side is not the agent's code.

Consequences. The pristine baseline must be baked into the verifier image byte-identically to what ships
in the agent environment, which the `separate_verifier_configured` criterion checks. Verifier runtime
roughly doubles, which the 900-second timeout absorbs.

Status. Superseded. Superseded by ADR-015. The runtime pristine reference lane is removed.

---

## ADR-006: Whole-tree artifact, not a diff-and-apply collect hook

Decision. `artifacts = ["/app/"]`. The verifier copies the tree into a scratch directory owned by the
unprivileged subject user and runs it there.

Context. The original hypothesis preferred a restricted patch applied to a pristine verifier-owned
baseline, on the theory that it contains hostile code better.

Alternatives. A `[[verifier.collect]]` git-diff hook with the strict apply chain the
`artifact_efficiency` criterion mandates: exact apply, then reset plus three-way, then a loud zero.

Rationale. The patch approach does not actually contain hostile code, because the verifier still executes
whatever the patch produced. What it buys is a smaller transfer and a reviewable diff, and the tree here
is small pure-Python with no dependency directories, so neither is worth much. What it costs is a real
failure mode: an unapplyable patch is an infrastructure failure that the rubric requires be treated as a
hard verifier error, and that is a false-negative surface aimed straight at the highest-ranked risk in
the register. `risk-scorer-replay` and `wal-recovery-ordering` both ship `artifacts = ["/app/"]` and both
execute agent code, so this is the precedented shape for tasks like ours.

Consequences. Containment rests entirely on privilege dropping and reward-channel isolation rather than
on transfer shape. Artifact size is tracked in the risk register; if an agent ships large scratch files,
the fix is an exclude list, not a redesign.

Status. Superseded. Amended by ADR-020. The whole-tree artifact stays, but root-side copying is replaced by in-place sanitisation.

---

## ADR-007: Never import agent code into the verifier process

Decision. The verifier invokes the subject only as a subprocess under
`setpriv --reuid nobody --regid nogroup --clear-groups --no-new-privs`, with a clean environment and
isolated interpreter flags. No agent module is ever imported by pytest.

Context. The `verifier_execution_isolation` criterion treats a pytest collection-time import of agent
code as execution at root, and `wal-recovery-ordering` ships a working exploit against exactly that:
a poisoned module that double-forks a reward-forging daemon and exits the importing stage successfully.

Alternatives. Import agent modules and isolate per-test with a forking conftest, which is what
`wal-recovery-ordering` does because its contract is a Python API.

Rationale. Our contract is a CLI, so the subprocess boundary is free. Dropping the in-process path
removes the entire import-time attack class instead of defending it.

Consequences. Combined with a root-only reward directory created before anything runs, root as the sole
writer of the reward, child output captured to temp files rather than pipes, and the child's process
group killed after each lane. `cheat/solve.sh` ships as the standing proof.

Status. Accepted.

---

## ADR-008: Delete the source store between export and import

Decision. The verifier removes the subject's source store after `snapshot export` and before
`snapshot import`, and hands the importer nothing but the snapshot file.

Context. The stated threat is an implementation that aliases the source world instead of restoring
independently. Detecting aliasing after the fact means chasing symlinks, hard links, copies, and
opportunistic reads of a known path.

Alternatives. Detect aliasing by inspecting the restored store, its inode, and the paths the process
opened.

Rationale. Removing the source makes aliasing impossible rather than detectable, needs no heuristics, and
matches how a real restore onto fresh storage works. The snapshot is also chowned to root at mode 0444
for the import and re-hashed afterwards, so the subject cannot rewrite its own input either.

Consequences. It becomes a stated part of the public contract that a restored sandbox is self-contained
and does not depend on the original store existing, which keeps the constraint honest rather than a
verifier trick.

Status. Accepted.

---

## ADR-009: Thin normalization, identifiers ordinalized

Decision. Compare after replacing physical row identifiers with their first-appearance ordinal within
each lane and canonicalizing JSON key order. Nothing else is stripped, rounded, or reordered.

Context. Differential verifiers leak through normalization. Strip too little and any implementation
choice fails; strip too much and a lazy restore passes.

Alternatives. Drop identifiers from the comparison entirely, which is simpler but makes identity
continuity untestable, since a restore that scrambles cross-domain links would compare equal.

Rationale. Ordinalization keeps the relational graph load-bearing while leaving implementations free to
number rows however they like. Everything else in the comparison is integral or symbolic, so exact
equality is achievable without tolerances.

Consequences. Any future addition to the compared surface has to be exactly reproducible across
implementations, or it does not belong in the comparison.

Status. Superseded. Superseded by ADR-018. Physical identifiers leave normative output entirely, so ordinalization is unnecessary.

---

## ADR-010: One conceptual defect, three behavioural surfaces

Decision. The starter code contains one mistake, that continuation state is treated as derived state,
showing up in three places, rather than three independent bugs.

Context. Three unrelated bugs would be a scavenger hunt and would fail `essential_difficulty`. One bug
with one surface would be solvable by inspection.

Alternatives. Three independent defects. A single defect with a single surface.

Rationale. One insight applied in three places is what produces the intended trajectory: a plausible
partial repair, a clean-looking diff, then the discovery that the model was incomplete. It also gives a
coherent story to a reviewer, which matters for `reviewable`.

Consequences. The three surfaces must be genuinely coupled. If implementation lets any of them be fixed
in isolation without the identity work, the overlap argument against `payments-pipeline-fix` weakens and
the difficulty drops.

Status. Accepted.

---

## ADR-011: Category Software, subcategory Databases

Decision. `category = "Software"`, `subcategory = "Databases"`.

Context. The taxonomy defines Software/Databases as storage engines, transactions, indexing, and
recovery, and Software/Systems as concurrency, backends, distributed systems, and infrastructure.

Alternatives. Software/Systems. A new subcategory such as "State management", which the taxonomy permits
when nothing fits.

Rationale. The task is state reconstruction and recovery across a schema version, which sits closer to
recovery than to backends. It also places the task alongside `live-database-cutover`,
`wal-recovery-ordering`, and `mvcc-lsm-compaction`, which is the honest neighbourhood for a reviewer
checking novelty.

Consequences. Revisit if the implementation ends up feeling more like application backend work than
storage work. Inventing a subcategory is the last resort.

Status. Open until the environment exists.

---

## ADR-012: Target the Harbor 0.14 feature set

Decision. Use only task-format features present in Harbor 0.14, and pin explicitly when running trials
locally.

Context. `/run` and `/cheat` install `harbor==0.14.0`; `/validate` and the rubric review install
`harbor==0.18.0`; `CONTRIBUTING.md` says install unpinned.

Alternatives. Build against 0.18 and accept the risk.

Rationale. A 0.18-only feature validates in CI and then fails under the trials, which are the expensive
part of the assignment. The intersection costs nothing here, because the shape we need is plain
`artifacts` and separate verifier mode.

Consequences. No `[[verifier.collect]]` hooks unless we confirm 0.14 supports them, which ADR-006 already
makes unnecessary. Trial commands record their Harbor version alongside their results.

Status. Accepted.

---

## ADR-013: Hidden fixtures are unreadable to the subject, and that is asserted

Decision. The pristine baseline, world seeds, operation sequences, and reference outputs are root-owned
at mode 0600 or 0700 for the duration of the subject lane. The verifier asserts, as a test, that the
subject's user cannot read them.

Context. The hostile review found that the strongest bypass is not forging a reward but replaying the
baseline: correct code sits in the same container as the subject, so a subject that could read it and the
hidden sequences could run them and echo the transcript without restoring anything.

Alternatives. Rely on `/tests` being root-owned, which by default is still world-readable at 0755.

Rationale. Default permissions leak the entire fixture set to the unprivileged user we deliberately run
the subject as. `risk-scorer-replay` already treats this as a real threat and asserts its private binary
is unreachable to `nobody`, which is the pattern to copy.

Consequences. The reference lane needs its own unprivileged identity distinct from the subject's, and its
working directory must not be traversable by the subject's user. Sequencing becomes load-bearing: the
reference lane runs to completion and is sealed before any agent code starts.

Status. Accepted.

---

## ADR-014: The defects are semantic, not structural

Decision. No continuation state is missing from the snapshot, from the v2 schema, or from the importer's
write path. Every section is present. The losses happen inside translations that are present and wrong.

Context. The hostile review found the fastest solve path: diff v1 tables against snapshot sections
against v2 tables, spot what is absent, carry it across. A strong model does that in one read, and it
would reduce the task to a five-minute exercise.

Alternatives. Omit sections from the snapshot or the importer, which is the more obvious way to stage the
bug and the way the first draft of this design had it.

Rationale. A structural diff has to come back clean, or the discovery phase collapses. Semantic defects
force the agent to run the system and observe divergence, which is the trajectory the task is for.

Consequences. Each defect has to be a plausible porting mistake rather than an omission: a null-tolerant
reference resolver whose caller reads null as absence, a ledger rekeyed through that same resolver, and a
v2 timer representation that stores an offset where v1 stored an absolute tick. Writing these so they
look inherited rather than planted is now an implementation requirement, and it is the part most likely
to need iteration.

Status. Superseded. Amended by ADR-016, ADR-017 and ADR-019, which respecify each defect site.

---

## ADR-015: Verifier-authored snapshots and build-time expected outputs

Decision. The verifier generates the graded v1 snapshots, the expected continuation transcripts, and the
expected final states in a discarded Docker build stage. At run time there is no reference lane and no
correct reference application in the image.

Context. The reviewer found the previous design unsound on two counts. A subject that controls both
`export` and `restore` defines both sides of correctness, so a byte-faithful round trip through a
subject-authored artifact proves nothing. Separately, a runtime pristine lane both assumes v1 and v2
semantics stay identical under a live comparison and ships correct code into the container the subject
runs in.

Alternatives. Keep the runtime lane and forbid subject-authored snapshots only for grading. Bake golden
transcripts without regenerating them, accepting drift risk. Write an independent reference
implementation in the verifier.

Rationale. Generating at build time removes drift, because the fixtures and the generator are produced by
the same run. It removes the runtime lane entirely. It also produces a strong security property that was
not available before: the expected outputs come from executing the continuation against the **v1** world,
so the verifier never needs and never contains a correct v2 restore. The answer to the actual problem
exists nowhere in the verifier image.

Consequences. Build-time self-tests become load-bearing, because a bad fixture is now baked rather than
recomputed. The build asserts determinism across repeated generation, snapshot round-trip through the
documented parser, and the v1/v2 equivalence invariant, and fails loudly on violation. `sandbox export`
stays in the product for the agent's own experiments but is explicitly ungraded.

Status. Accepted.

---

## ADR-016: The snapshot is a public structured document, not a database

Decision. The snapshot is JSON in a format documented completely in the in-task specification. A raw
SQLite file is never the interchange artifact.

Context. With a database file as the snapshot, byte-copying it into the target path is a plausible
"restore" and the trivial bypass is hard to close without heuristics.

Alternatives. Keep the database file and detect copying. Use a bespoke binary format.

Rationale. A document cannot be a running store, so the bypass closes by construction rather than by
detection. A public format also keeps the difficulty where it belongs: hidden cases are hidden instances
of a documented format, never hidden rules. No cryptography, encoding trick, compression puzzle,
undocumented metadata, or obscure serialization is used, because those manufacture difficulty rather than
test the state model.

Consequences. The format specification becomes part of the public contract and has to be complete enough
that two readers would write the same parser.

Status. Accepted.

---

## ADR-017: Identity and completed-operation continuity are decoupled

Decision. Logical links resolve against stable customer-facing references through a merge history.
Completed-operation receipts preserve an immutable recorded result envelope. Neither repair fixes the
other.

Context. The reviewer found that a single total-resolver fix would have repaired both surfaces at once,
collapsing two of the three consequences into one.

Alternatives. Keep a shared resolver and accept the collapse. Drop one of the two surfaces.

Rationale. The two questions are genuinely different in production. "Which live entity does this
relationship point at now" is a graph-resolution question. "What did this request return the first time"
is a historical-record question that must survive physical renumbering without being recomputed from
present state. Making the receipt defect a rehydrate-from-current-row bug guarantees that no amount of
link-resolution work repairs it.

Consequences. Two distinct repair sites with distinct reasoning. The design must resist any later
refactor that routes both through one helper.

Status. Accepted.

---

## ADR-018: No physical identifiers in normative output

Decision. `world dump` exposes stable customer-facing references only. Comparison is exact JSON equality
after canonical key ordering, with collection order fixed by the public specification.

Context. The previous design kept physical identifiers in the compared surface and ordinalized them by
first appearance, which is a normalization layer, and normalization layers are where differential
verifiers leak.

Alternatives. Keep ordinalization. Drop identifiers from comparison without exposing logical references,
which would make identity continuity untestable.

Rationale. Physical row identifiers are not externally meaningful, so publishing them in normative output
was a mistake. Removing them lets the comparison be exact equality with no normalization to attack, and it
makes a broken relationship directly visible as the wrong logical reference rather than as a permutation
mismatch.

Consequences. Implementations are free to number rows however they like, with no verifier machinery
required to permit it.

Status. Accepted.

---

## ADR-019: Pending work translates scheduling intent, not a stored due tick

Decision. The snapshot records the ticket, the tick work was scheduled at, and the policy revision in
force at that moment. The correct due tick derives from the historical revision. The defect uses the
revision in force at snapshot time instead.

Context. The reviewer found the previous shape, "snapshot contains a due tick that the importer parses and
ignores", too obvious to survive a careful reading.

Alternatives. Store the due tick directly. Omit the revision from the snapshot, which would make the
correct answer underivable and the task unfair.

Rationale. Policy revisions are ordinary world data with an effective tick, so the historical-versus-
current distinction arises from time passing rather than from a contrivance. The snapshot carries
everything needed to compute the right answer, so the task stays fully specified, while the translation
requires understanding that a scheduling decision is historical. The defect is a wrong lookup, not an
ignored field: the recorded revision is read and stored, it is simply not the one used for the delay.

Consequences. Policy revisions become part of the public domain model and the public snapshot format.
The shipped sandbox has no policy change during its lifetime, so this surface is invisible until the
agent builds the experiment.

Status. Accepted.

---

## ADR-020: Sanitise the hostile tree in place, never copy it as root

Decision. Keep `artifacts = ["/app/"]`. Before any subject process starts, root inspects `/app` without
following symlinks, rejects symlinks, strips setuid, setgid and sticky bits, normalizes only the
executable modes the CLI needs, and confirms the entry point exists. The tree is never recursively
chowned to root and never copied with a default recursive copy. Subject code runs in place.

Context. The reviewer pointed out that a whole-tree artifact can carry symlinks and privilege bits across
Harbor's transfer, and that root-side copying or chowning converts those into verifier compromise.
Narrowing the artifact does not help, because Harbor materializes convention artifacts regardless.

Alternatives. Narrow the artifact list. Copy the tree into a scratch directory as root before running.

Rationale. Containment belongs in handling, not in transfer shape. Copying as root is the specific step
that turns a hostile symlink into a root-side write, so the fix is to not do it.

Consequences. A pre-scan step becomes part of the verifier's critical path, and its failure mode has to be
specified precisely, which is ADR-021.

Status. Accepted.

---

## ADR-021: Reward zero and verifier error are different outcomes

Decision. A missing or broken artifact transfer is a verifier error: non-zero exit, no reward written. An
artifact that arrives and violates documented submission policy, such as a symlink or a setuid file, is a
deterministic reward 0 with a diagnostic and no crash.

Context. Pre-writing zero, which is the common pattern in merged tasks, launders infrastructure failures
into scoreable zeros.

Alternatives. Pre-write zero and overwrite on success, which is simpler and what several merged tasks do.

Rationale. The employer requirement makes this distinction load-bearing in both directions. Standard
trials only count when the agent genuinely failed rather than the harness breaking, and every adversarial
trial has to produce a real zero rather than an accidental one. Collapsing the two outcomes would corrupt
both records.

Consequences. The reward file is written once, at the end, by root, from an explicit outcome. Nothing
writes a placeholder.

Status. Accepted.

---

## ADR-022: The agent must not be able to run a correct legacy runtime

Decision. In the redesign, no v1 or v2 executable ships. Historical generations are documented as data
formats with field meanings; normative behavioural semantics exist only for the current runtime.

Context. The Opus 5 calibration returned reward 1 in 10 minutes 30 seconds. The trajectory shows the
decisive advantage was not clue density: a correct v1 runtime shipped alongside the broken restore, and
the graded property was "restored v2 behaves like continued v1", so the agent wrote a differential fuzzer
over 2,300 worlds in about three minutes and ran a mechanical find-and-fix loop.

Alternatives. Hide the symptoms, rename the helpers, scatter the same defects across files, cut
documentation. All of these tune clue density and leave the free oracle intact, so the fuzzer still finds
everything.

Rationale. Removing the oracle changes what is being measured, from "run a fuzzer and fix what it
reports" to "reason about what the historical state means and prove it from the specification". A
decommissioned legacy runtime is also the ordinary state of a real archive-restore codebase.

Consequences. The format-versus-behaviour split becomes load-bearing for fairness and is the first kill
criterion: if field meanings cannot be documented without amounting to a v1 behavioural specification,
the oracle returns and the design fails. Verification also stops being free for the agent, which is the
point, but it raises the risk of premature completion and has to be watched rather than celebrated.

Status. Accepted.

---

## ADR-023: Difficulty comes from migration composition, not from a harder single hop

Decision. Three schema generations with a declared version chain. Each hop is locally correct for the
pair of versions it was written for; the composition loses or reinterprets state a later version needs.

Context. Phase 1A's three defects were three wrong lines in one file. Once found, each repair was a small
local edit, and finding them was free given the oracle. Raising discovery cost alone would not have
changed the shape of the work.

Alternatives. Keep one hop and make the single migration subtler. Add more surfaces. Add scale.

Rationale. A composition failure has a property a single-hop bug does not: reading any one migration in
isolation shows nothing wrong, because nothing in it is wrong. The agent has to hold three schema models
at once and find an information-flow gap between them. That is a different and harder question than
"which line is incorrect".

Consequences. The three repairs must differ in kind or one rewrite covers them, so they are fixed as a
reconstruction, a point-in-time resolution, and an honest representation of absence. Component-level
schema versions are introduced with named release profiles, which is how staged rollouts really archive,
and a v2-origin profile is graded so that special-casing the oldest generation does not pass.

Status. Accepted.

---

## ADR-024: Generate fixtures forward from ground truth, then emit the snapshot

Decision. The verifier build stage constructs the intended current-runtime world natively, then emits a
historical snapshot representing it, applying the losses the generation boundary implies. Expected
outputs come from running the current runtime on the true world.

Context. Phase 1A generated expectations by running a correct v1 world, which worked because the
verifier never needed a correct v2 restore. The redesign has no legacy runtime to run, so expectations
could only come from a correct forward migration, which would put the answer in the build stage.

Alternatives. Keep a correct reference migration in the discarded build stage.

Rationale. Emitting backwards from ground truth preserves, and slightly strengthens, the Phase 1A
property: no correct forward migration exists anywhere in the verifier image, build stage included. The
build stage holds the inverse of the task, and inverting it is the work.

Consequences. The emitter must be genuinely lossy in exactly the ways the design needs, and the
reconstruction it forces must stay determinate on every supported profile. That determinacy is the third
kill criterion.

Status. Accepted.

---

## ADR-025: Abandon the migration family on empirical grounds

Decision. The sandbox migration and replay family is rejected. Both `sandbox-upgrade-continuity` and the
proposed `sandbox-upgrade-chain` are retired as task candidates. Their implementations, documents and
calibration evidence stay in the repository as the record of why.

Context. Opus 5 at reasoning max solved the Phase 1A task legitimately in 10 minutes 30 seconds. The
trajectory attributed that to a pointwise labelled-example generator: for input snapshot X the expected
output is whatever the shipped correct runtime produces, so every failing case localised to a field and
credit assignment was free. Redesign 1B proposed removing the generator by decommissioning the legacy
runtime, which was sound but treated the symptom.

Alternatives. Implement Redesign 1B and calibrate it. Harden Phase 1A further.

Rationale. The independent review and the calibration agree on the deeper point: deterministic
transformation problems admit pointwise oracles, and where a pointwise oracle exists a frontier agent
converts the task into a mechanical find-and-fix loop. That is a property of the family, not of any one
instance, so a third attempt inside it is a poor use of the remaining schedule.

Consequences. Two implementation days are written off, though the verifier harness, the anti-cheat
boundary and the whole documentation workflow carry forward intact. The selection rule for what replaces
it is ADR-026.

Status. Accepted.

---

## ADR-026: Select for cheap detection and hard construction

Decision. Task candidates are now selected on one rule: the agent may generate counterexamples freely,
but counterexample generation must be insufficient to establish global correctness. `tool-gateway-
metastability` is chosen against that rule.

Context. The failure mode we keep hitting is not weak difficulty in the abstract, it is that the agent
can manufacture a labelled oracle and let it do the reasoning.

Alternatives. Continue judging candidates on how subtle the defect is, which is what produced two
too-easy designs.

Rationale. Scheduling and overload problems give a fuzzer a trajectory-level verdict, "this schedule
starved that tenant", never a label on any individual scheduling decision. Credit assignment stays with
the engineer. Sampling schedules is also evidence rather than proof for a property quantified over
schedules.

Consequences. One derived constraint becomes load-bearing and is a verification obligation before
implementation: the repair must be structural rather than parametric. If any assignment of constants to
the shipped architecture satisfies the contract, a fuzz-guided grid search finds it in minutes and the
design fails. The feasible region must be shown empty first.

Status. Accepted.

---

## ADR-027: Python for the gateway, against the stated preference

Decision. Implement the gateway simulation in Python rather than TypeScript.

Context. The brief prefers TypeScript for asynchronous request lifecycles, cancellation, scheduling and
provider adapters, and allows Python where repository evidence shows it materially reduces incidental
complexity.

Alternatives. TypeScript on Node with a pinned toolchain.

Rationale. The design forbids real asynchrony: it is a deterministic event queue over integer ticks with
no threads, no promises and no wall clock, because that is what makes the verifier exact and the agent's
reproductions reliable. The TypeScript advantage applies to the machinery we deliberately removed.
Meanwhile the verifier harness, the pytest and CTRF tooling the static checks enforce, and every reusable
piece from Phase 1A are Python; there is no build step to pin; and no `node_modules` tree lands in the
`/app` artifact, which the `artifact_efficiency` criterion flags directly.

Consequences. The verifier invokes the subject through a CLI subprocess either way, so nothing
architectural changes. Revisit only if the gateway turns out to need genuine async semantics, which would
itself indicate the deterministic model had been abandoned.

Status. Accepted.

---

## ADR-028: A terminal deadline is terminal

Decision. Once a request reaches its terminal deadline it is finished: no new provider attempt may start
for it, no retry may be generated, and any delayed retry state is invalidated. Four timing concepts are
kept distinct: the request terminal deadline, the provider attempt timeout, the internal dispatch lease,
and retry eligibility.

Context. The first gateway design used "deadline expiry is classified retryable" as its sustaining
mechanism. Reviewer B rejected that as semantically wrong, and the rejection is correct: retrying work
whose caller has already given up is not a defensible production behaviour, so a task built on it would
be teaching the wrong lesson.

Consequences. The feedback loop may not depend on retrying dead work, which removed the original
sustaining mechanism and forced the search that ADR-029 records the outcome of.

Status. Accepted, and it stands regardless of which task family ships.

---

## ADR-029: The provider-orphan mechanism cannot produce cross-domain metastability

Decision. Record as disproven, before implementation, that provider-side orphaned work can sustain a
metastable failure across isolation domains.

Context. The gate mandated formalising orphaned downstream work: an attempt timeout means the gateway
stops waiting, not that the provider stops executing, so the slot stays occupied. That is a real
production phenomenon and it was modelled faithfully.

Evidence. A scratch model swept 432 configurations across offered load, provider concurrency, attempt
timeout, backoff, attempt cap and deadline, filtered to those where the no-fault baseline passes cleanly.
Zero produced harm to the unaffected provider. The condition `Σ P_i > W` is necessary but not sufficient,
and even `P_i ≥ W` does not produce contention.

Rationale. Three structural facts, none numeric. A timeout is a release, so a faulted domain cannot hold
the shared bottleneck. Orphaned work occupies provider capacity, which is per-provider and therefore
cannot starve a different provider. Retry pressure costs one worker-tick per attempt, and attempt volume
is bounded by the healthy-load assumption. Cross-domain starvation needs the faulted domain to hold the
shared resource; orphaning needs it to release early. Parameters move along that trade-off, they do not
escape it.

Consequences. The two-provider formulation is dead. A single-provider multi-tenant variant does exhibit
the failure, but as a bounded transient rather than an attractor and with a one-insight repair, so it is
not carried forward either. The parametric-feasibility proof is moot because the premise fails.

Status. Accepted.

---

## ADR-030: Stop pivoting; harden the working task instead

Decision. No fourth task family. The remaining schedule goes to the Phase 1A task, which passes every
mechanical gate, plus targeted difficulty hardening and the eight required trials.

Context. Three families explored across three days. Family one was solved by Opus in ten and a half
minutes. Family two was retired on the generalisation of that finding. Family three has now been
disproven at the design gate before any code was written. Roughly four days remain, and they have to
cover implementation, calibration, eight trials and a written analysis.

Alternatives. Continue searching for a fourth family. Ship Phase 1A unchanged.

Rationale. The expected value of a fourth search is low: two of three families failed empirically after
substantial investment, and the third failed on paper. The Phase 1A task already has a hardened verifier,
a proven anti-cheat boundary, a passing oracle, a failing nop and complete documentation. Its known
weakness is difficulty, and the levers for that are identified and cheap to try: remove the symptom
enumeration from the instruction, which violated ADR-002 and handed the agent three targets; stop
co-locating the defects; and consider adding hidden state the agent cannot observe locally. A submission
that runs, with eight real trials and an honest difficulty analysis, is a better artifact than a fourth
abandoned design.

Consequences. The 0-of-3 requirement may not be met. If hardening does not move the calibration
materially, that is reported as a measured result with the trajectory evidence rather than concealed.
Every abandoned design and every calibration stays in the repository as the record of how the conclusion
was reached.

Status. Accepted, pending the candidate's agreement, since it changes what gets submitted.

---

## ADR-031: The credit broker is rejected before implementation

Decision. `tool-stream-credit-broker` is rejected on paper. No task directory is created.

Context. It was proposed as the final family, selected on evidence that LLM-generated concurrent code
remains vulnerable to races, deadlocks and starvation.

Evidence. Two gates from the brief itself, both settled empirically in a scratch interleaving model. The
global-lock attack: one broker-wide lock with a condition variable and a generation counter satisfies all
four graded properties, verified exhaustively over 240 states with zero violations. The repair-size gate:
the complete diff from a racy broker to a correct one is eleven lines of generation and terminal guards
on top of the standard pattern.

Rationale. Property D, independent progress, was expected to punish coarse locking and does not, because
waiting on a condition variable releases the lock. That is definitional, not accidental. More generally,
in a bounded-interleaving model a global lock serialises atomic steps and preserves every safety and
liveness property; what it loses is throughput, which is a timing property the brief correctly forbids
grading. There is no semantic progress property left that a single-lock design fails.

The design also fails the labelled-generator test that motivated the entire search, and fails it worse
than its predecessors. An interleaving explorer returns the two steps that raced, which is a pointwise
label on a repair site. A model checker is a stronger labelled repair oracle than the differential fuzzer
that solved the first family in ten minutes.

Consequences. Four families evaluated, three against evidence. The common finding is that a task is easy
for a frontier agent whenever the agent can build something that labels the repair. Engineering that
absence into a small codebase has failed three times, which is itself the most useful result of the
trial.

Status. Accepted.

---

## ADR-032: Model the failure before designing the task around it

Decision. Any future candidate must have its central failure mode demonstrated numerically, in a scratch
model, before any task code is written.

Context. Family one cost two implementation days and was disproven by a live calibration. Family three
was disproven by an afternoon's sweep. Family four was disproven by a 240-state check in under an hour.
The cost of being wrong fell by an order of magnitude each time the check moved earlier.

Rationale. Design review, including hostile review, did not catch any of the three. In every case the
argument sounded right and the numbers disagreed. Reviewer B's condition on shared capacity is the
sharpest example: it was correct in direction, necessary, and still not sufficient, which only the sweep
revealed.

Consequences. The spike comes first, the design document second. This is cheap, it is repeatable, and it
is the practice worth carrying out of this trial regardless of what ships.

Status. Accepted.

---

## ADR-033: The trajectory store is rejected on measurement

Decision. `trajectory-store` is rejected. No task directory is created.

Context. It was proposed as a construction and optimisation task rather than a debugging one, on the
theory that Pareto-frontier navigation gives an agent only low-resolution feedback.

Evidence. A scratch spike built three realistic workload regimes, six baselines and a reference
prototype, and measured size, build memory, build time, random trajectory access and range access. In
the mixed regime the reference reached 4.4 MB at 6.4 ms per query against whole-corpus LZMA at 3.8 MB and
0.10 ms. In the prefix-heavy regime, the case the reference architecture is designed to win, it was both
larger and 57 times slower. The strongest generic answer, whole-file LZMA with a decompress-once open
path and an mmap offset index, achieved best size and best random access simultaneously.

Rationale. Structural interning and content addressing remove redundancy a large-window dictionary
compressor already removes, and remove it less well, while the block structure needed for random access
shrinks the compressor's effective window. The one axis where application structure should win, random
access without full decode, is defeated by decoding once into scratch and indexing.

Consequences. Kill conditions 1, 5, 6 and 8 all fire. Making the generic answer fail would require a
tight resident-memory bound and a ban on scratch files, numbers chosen to defeat a baseline rather than
derived from the problem.

Status. Accepted.

---

## ADR-034: Five families, one cause

Decision. Record the common finding across every rejected family, because it is the substantive result of
the search and it should not have to be rediscovered.

The families, and how each was settled:

| Family | Outcome | Settled by |
|--------|---------|-----------|
| Sandbox migration | Solved in 10m30s | Live Opus calibration |
| Migration chain | Retired | Generalisation of the above |
| Gateway metastability | Mechanism disproven | 432-configuration sweep |
| Credit broker | Rejected on paper | 240-state exhaustive check |
| Trajectory store | Rejected on measurement | Six baselines across three regimes |

Originality was clean for all five. Difficulty was binding for all five.

For the four debugging families the cause is one thing: a task is easy for a frontier agent whenever the
agent can construct something that **labels the repair**. A pointwise output oracle, a differential
fuzzer, and a model checker naming the two steps that raced are the same object from the agent's side.
The trajectory store was chosen specifically to avoid that, and failed differently and more simply: the
competent first move was also the winning move, because a general-purpose compressor already does what
the application-specific design was supposed to add.

The corpus is consistent with both readings. Tasks that are genuinely beyond frontier carry inherent
depth: 16 to 60 hours of expert time, formal proof, deep regulatory or scientific knowledge, or real
scale. Small codebase plus subtle defect, and small codebase plus clever representation, are both shapes
frontier agents are strongest at.

Status. Accepted as the record.

---

## ADR-035: tool-session-failover rejected during build

Decision. Rejected after implementing the starter and measuring the repair. The task directory was
removed; the prototype is preserved in scratch.

Context. Proposed as a final construction attempt: a real multi-process tool-execution service where a
logical call must survive client disconnect and gateway process failure. Real processes, real HTTP, real
shared SQLite in WAL mode, deterministic failpoints driven from durable state rather than sleeps.

Evidence. The failure geometry is genuine. The starter duplicates a provider side effect after a
mid-flight process death, loses terminal state across processes, and cannot cancel a call from a second
process. But the complete repair measures **29 lines**: one line for a stable idempotency key derived
from the logical call id, eight to allocate the event sequence in a transaction and commit terminal
state atomically, and about twenty to read call state and the cancel flag from the database instead of a
process dict. Verified by applying it and re-running every scenario.

Rationale. Every element of that repair is a standard pattern a backend engineer applies by reflex. The
task also hands the agent a constructible oracle: the provider exposes an effect log, the database is
inspectable, and gateway processes are restartable by the agent, so it can build the same scratch driver
used here in about fifteen minutes, and every counterexample localises the repair exactly. Estimated
Opus at maximum reasoning: well under thirty minutes.

Consequences. Sixth family rejected, and the fastest yet at about forty minutes, because the failure was
modelled and the repair measured before any verifier, oracle or scenario matrix was written. That
ordering is ADR-032 and it is now the most valuable process artifact of this trial.

Status. Accepted.

---

## ADR-036: rollout-batch-scheduler rejected; task search closed

Decision. Rejected inside the 90-minute box. Per the gate's stop condition no further family is
proposed and the search for an original task ends here.

Context. A hidden-instance combinatorial optimisation task over a post-training rollout fleet:
sequence-dependent adapter and prefix setup costs, deadlines, finite workers, one coherent objective
combining wasted setup time and weighted tardiness.

Evidence. Simple baselines look badly beaten, 1.16x to 16x the reference across three regimes, and no
single heuristic wins every regime. But the strongest baseline had run 4,000 iterations against the
reference's 60,000. At equal budget a naive simulated annealer, one greedy seed plus relocate and swap,
lands at 1.109x, 0.950x and 1.084x on the three regimes, beating the reference outright on the
deadline-heavy regime. Adding only multi-alpha seeding reaches median 1.004x there.

Rationale. The apparent gap was search budget, not architecture. The proposed 1.03 to 1.05x threshold is
already met by the program the gate itself names as the expected Opus first attempt. Strengthening the
reference would be tuning a threshold to defeat a baseline, which every gate here has forbidden.

The family also cannot escape the oracle property. A public cost simulator must ship or the task is not
well posed, and `cost(schedule)` is exactly what a metaheuristic needs. Unlike the previous six families,
where the oracle was incidental, here it is structural.

Consequences. Seventh family rejected. The fallback submission becomes the plan.

Status. Accepted.
