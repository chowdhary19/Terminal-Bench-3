# Risk register

Ranked by expected damage to the submission, worst first. Each entry says how we would find out and what
we do about it. Risks below the line are tracked but not currently driving design.

## R1. A frontier model solves it on the first pass

No longer a risk for the Phase 1A design. It is a measured result: Opus 5 at reasoning max returned
reward 1 in 10 minutes 30 seconds, 8.75% of the time budget, no wrong turns, and produced a better fix
than the reference solution. The cause was a free differential oracle rather than clue density, and the
redesign in [REDESIGN_1B.md](REDESIGN_1B.md) removes it.

The risk carries forward to the redesign in a weaker but live form. The pessimistic floor there is about
90 minutes if all three surfaces land quickly, against a central estimate of 2.5 to 5 hours. That clears
the one-hour warning line without much margin.

Detection. One calibration trial against the redesign before any further investment, read the same way
this one was: trajectory first, reward second. Specifically, check whether the agent reconstructed a
legacy oracle by some route we did not anticipate, and whether it reached the Surface C reconstruction by
reasoning or by guessing and checking against the finite transcript.

Mitigation. Kill criterion 4 in the redesign document is explicit: a clean solve with time to spare ends
that design too. The levers if it survives but is merely fast are, in order, to make the Surface C
reconstruction genuinely require the three-source intersection on more profiles, and to widen the graded
origin profiles so a fix specialised to the oldest generation does not pass. What is not a lever is
cutting documentation, which trades difficulty for unfairness.

## R2. Verifier false positives

A verifier that fails a correct implementation is worse than one that is slightly gameable, because it
burns trials, it produces a task that cannot be reviewed honestly, and it usually shows up as a run of
near-misses that look like difficulty.

Detection. Run the oracle repeatedly, not once. Ten or more consecutive oracle runs at reward 1 across
every hidden world before believing the harness. Deliberately write two structurally different correct
implementations and confirm both pass.

Mitigation. Compare behaviour, never structure. Ordinalize physical identifiers so implementations may
number rows however they like. No wall clock, no randomness without a fixed seed, no filesystem ordering
dependence, no tolerances needed because everything compared is exact and integral. Do not assert on
schema, module layout, function names, or the presence of any particular table. The one process
constraint we do impose, that pre-snapshot behaviour must not change, is enforced by comparing against
the pristine lane rather than by inspecting the agent's code.

## R2b. Subject controls both sides of correctness

Retired as a design risk, recorded because it was the most serious finding of the adversarial review and
the mitigation is structural rather than incidental.

The pre-review design let the subject author the snapshot with `export` and then consume it with
`restore`. A round trip through a subject-authored artifact proves nothing, and with a database file as
the artifact a byte copy was a passing "restore".

Closed by ADR-015 and ADR-016. Graded snapshots are authored by the verifier at build time, the subject's
export is ungraded, and the artifact is a JSON document that cannot be a running store. Detection if it
regresses: any change that lets a verifier assertion depend on a file the subject produced.

## R2c. Baked fixtures can be silently wrong

Build-time generation removes drift between generator and fixtures, and replaces it with a new failure
mode: a fixture that is wrong is now baked rather than recomputed, and every trial inherits the error.

Detection. Build-time self-tests that fail the Docker build loudly rather than warning. Repeated
generation must be byte-identical, generated snapshots must round-trip through the documented parser, and
the v1/v2 equivalence invariant must hold for the graded operation set.

Mitigation. The self-tests above run inside the build stage that produces the fixtures, so a violation
cannot reach the final image. Oracle at reward 1 across repeated runs is the second gate, and the
intentionally broken starter failing is the third: a fixture that both a correct and a broken
implementation satisfy is a fixture that tests nothing.

## R2d. Premature completion replaces genuine difficulty

Removing the agent's free oracle is meant to stop free verification, not to make correctness unknowable.
If the redesign's difficulty ends up resting on the agent being unable to check its work, the task is
measuring confidence calibration rather than state reasoning, and a careless agent and a careful one fail
for the same reason.

Detection. Read the calibration trajectory for whether the agent understood the three surfaces and
mis-executed, or never understood them and stopped early. A failure that looks like "declared done after
the finite transcript passed" is this risk, not the intended difficulty.

Mitigation. The finite evidence set has to be rich enough that a careful agent can convince itself, and
the specification has to determine the answer without an oracle. This is kill criterion 5 in the redesign
document. It is also the reason the format-versus-behaviour split is drawn where it is: field meanings
are complete, so the correct migration is derivable, while legacy runtime behaviour is absent, so it
cannot be generated on demand.

## R3. Verifier exploitability

`/cheat` runs an agent explicitly told to tamper with verification, with the anti-cheat sentence stripped
from the instruction first. Any nonzero reward fails the requirement outright.

Detection. Two adversarial trials, one per agent, both required at reward 0. Plus a committed
`cheat/solve.sh` that installs a reward-forging poisoned tree, which must score 0 and can be re-run at
any time. Plus an adversarial read of the harness by someone actively trying to break it.

Mitigation. The full boundary table is in the design contract. The load-bearing parts: agent code is
executed as an unprivileged subprocess and never imported into the verifier's process; `/logs/verifier`
is root-only before anything runs and only root writes the reward; the reference lane completes and its
outputs are sealed before agent code starts; the source store is deleted between export and import so
aliasing yields nothing; the snapshot is root-owned read-only during the import and re-hashed after;
normalization is thin enough that there is nothing to launder a wrong answer through.

The exploit this design takes most seriously is baseline replay. Correct code sits in the same container
as the subject, so a subject that could read the pristine baseline and the hidden operation sequences
could run them and echo the result instead of restoring anything. Every hidden fixture is therefore
root-owned and unreadable to the subject's user, the reference lane runs as a different unprivileged user
in a directory the subject cannot traverse, and its inputs and outputs are sealed into root-only storage
before the subject lane starts. The verifier asserts the unreadability as a test rather than assuming it,
following the pattern `risk-scorer-replay` uses for its private probe binary.

The review added a second class this design now handles: hostile bytes arriving through the whole-tree
artifact. A symlink or a setuid file in `/app` becomes a verifier compromise the moment root copies or
chowns the tree, so root does neither. It sanitises in place, rejects symlinks, strips privilege bits, and
turns a policy violation into a deterministic zero rather than a crash. ADR-020 and ADR-021.

A third property is now structural rather than defended: the verifier image contains no correct v2
restore at all, because expected outputs are produced by running the continuation against the v1 world in
a discarded build stage. There is no correct answer in the image to steal.

Residual concern to revisit during implementation: an agent that detects it is running inside the
verifier and behaves differently. The defence is that correct behaviour is the only behaviour that passes,
so detection buys nothing, but it deserves a deliberate look once the harness exists.

## R4. Hidden underspecification

The instruction states one invariant. The verifier checks three surfaces of it. If a reasonable engineer
could satisfy the stated invariant and still fail, the task is unfair and `test_instruction_alignment`
fails with it.

Detection. Hand the instruction and the repository to a competent reader with no knowledge of the
solution and ask what the verifier will check. Separately, read the instruction against the rubric
criterion directly.

Mitigation. Every graded behaviour follows from a numbered rule in the public contract. Retry semantics
are rule 2. Scheduled work firing once at its due tick is rule 3. Determinism is rule 1. Restored
sandboxes being self-contained is rule 5. Pre-snapshot behaviour holding is rule 6. The `world dump`
schema is documented normatively in the repository, not implied by examples. The one thing deliberately
not spelled out is which of these the current code gets wrong, which is discovery, not
underspecification.

## R5. Semantic overlap with merged tasks

Covered in detail in the uniqueness scan. The scan returned GO WITH REVISIONS rather than a clean GO
because two neighbours are closer than comfortable: `payments-pipeline-fix` already rewards not
duplicating a completed effect across a restart, and `session-window-debug` already rewards
pending-work correctness under a logical clock.

Detection. A reviewer who knows the corpus asking "how is this not payments-pipeline-fix with a
snapshot in it".

Mitigation. Keep the retry surface bound to identifier translation, so preserving completions requires
the identity work first and does not stand alone as a receipts table. Keep the pending-work surface about
carrying recorded due times across a rebuild rather than about firing policy. Frame the task as
continuity, not migration, so it is not read next to the two database migration tasks. If implementation
drifts away from either constraint, the overlap argument weakens and this risk climbs the list.

## R5b. The environment reads as reverse-engineered from the bug

A reviewer can accept that the task is hard and still reject it on `interesting` or `reviewable` grounds
if the product looks built backwards from its defect. Two features carry that suspicion: a request
ledger and a logical clock in a small internal tool.

Detection. Ask a reader who does not know the solution what the tool is for and whether its design makes
sense on its own terms. If the answer involves the bug, the framing failed.

Mitigation. Frame the product as a sandbox platform, where both features are native rather than
convenient: fast-forwarding a demo environment is why the clock exists, and automated clients retrying
calls is why request identifiers exist. The design contract states this explicitly so a reviewer does not
have to reconstruct it. Keep the tool coherent as a product: it should be usable and sensible for its
stated purpose with the restore path removed entirely.

## R6. Accidental difficulty

The task should be hard because the state model is hard. It should not be hard because the agent guessed
the wrong output format, could not find the specification, or tripped over a corner case nobody would
hit in production. `essential_difficulty` fails tasks whose failures come from clerical detail.

Detection. Failure analysis on the standard trials. If the trajectories show agents that understood the
problem and lost on formatting or a corner case, the difficulty is misplaced.

Mitigation. `world dump` has a documented normative schema. Errors are actionable. No hidden required
flags. Two domains, not three. No edge cases beyond what the invariant needs. The design contract's
non-goals list exists specifically to make additions require an argument.

## R7. Solution leakage

Anything in the agent's environment that names the answer collapses the task. This includes comments,
commit history, docstrings, dead code, and the sandbox data itself.

Detection. Read every agent-visible file adversarially before running trials, then read the git history
the same way. Grep for benchmark vocabulary anywhere under the task's environment. The reward-hacking
check in `harbor analyze` will also flag an agent that found a shortcut.

Mitigation. Comments explain production rationale only. No TODO points at the defect. No file mentions
tests, verification, benchmarks, hidden checks, or a fix. Ground truth, seeds, operation generators, and
the pristine baseline live only in the verifier image and never enter the agent container. Task commits
are written so the history reads as ordinary development, and the git history shipped inside the
environment, if any, does not contain the fix.

## R8. Task-format incompatibility

Twenty-two static checks, thirty-five rubric criteria, and two different Harbor versions across CI. A
task that is conceptually fine can still fail to land.

Detection. Run all twenty-two checks locally on every commit. Run the rubric review locally before
declaring done. Baseline document records every binding constraint with a citation.

Mitigation. Known traps already recorded: the slug is exactly at the three-token limit; `allow_internet`
must be omitted entirely rather than set either way; `artifacts` must sit above the first section header;
`[task] name` must match the folder; `tests/Dockerfile` must `COPY` into `/tests` and `mkdir -p` every
artifact parent; pytest and the CTRF plugin must be baked and pinned to the canonical versions; the
instruction must end with the exact timeout sentence; the template's `network_mode` key must not be
copied. Build against the Harbor 0.14 feature set since that is what `/run` and `/cheat` install.

## R9. Infrastructure flakiness in the trials

A trial that dies on a rate limit, a container failure, or a timeout is not a model failure and does not
count. Six standard trials plus two adversarial trials have to complete cleanly.

Detection. `harbor analyze` distinguishes error outcomes from failures, and reports `low_timeout`
separately.

Mitigation. No network dependency at verify time. No third-party packages. Verifier work sized to finish
well inside its timeout. Agent timeout at 7200 seconds, matching comparable tasks, so an agent making
progress is not cut off. Budget for re-runs rather than assuming eight clean trials on the first attempt.
Confirm Docker and Harbor are working before starting a trial batch.

One specific crash source is already closed. `CLAUDE_CODE_MAX_OUTPUT_TOKENS` is the CI default that keeps
Claude Code from hitting its output cap and dying with `NonZeroAgentExitCodeError`, and Harbor reads it
from `os.environ` only, ignoring `--ae`. Passing it the obvious way would have silently dropped it and
produced crashed trials that do not count as model failures. The documented commands export it instead.

## R10. Seven-day schedule

Phase 0 consumed part of day one. Remaining work: build the application, build the verifier, build the
oracle, build the cheat oracle, pass twenty-two checks and the rubric review, then run eight trials with
turnaround time for at least one hardening iteration.

Detection. If the environment and a passing oracle are not both done by the end of day three, the trial
budget is at risk.

Mitigation. Sequence the risky things first. Verifier harness and cheat oracle before polish, because R1
and R3 are the two risks that can force a redesign, and both are only observable once the harness runs.
Keep the application small on purpose. Do not start writing final documentation until trials have run,
because the failure analysis is the part that cannot be written in advance.

## R10b. Codex entitlement (CLOSED)

Closed. The candidate upgraded their personal ChatGPT account to Plus and Codex was cleanly
re-authenticated to it.

Evidence: the account-scoped catalog moved from six models without `gpt-5.6-sol` to eight models with it;
Sol lists `xhigh`; and one minimal probe at `gpt-5.6-sol` with `model_reasoning_effort=xhigh` returned
normally at 4,020 tokens, exit 0. The Free-account rejection (`HTTP 400`, "not supported when using Codex
with a ChatGPT account") no longer occurs.

No API key and no employer-managed workspace are involved. All eight required trials are operationally
unblocked on subscription auth, and both agents are verified against the exact CI configuration.

## R10c. The redesign consumes the trial schedule

The redesign is estimated at about two days to reach Phase 1A-equivalent gates, and day one is already
spent. That leaves roughly two days for calibration and hardening and two for the eight required trials
and the write-up, with Codex still blocked on a personal plan upgrade that gates three standard trials
and one adversarial trial.

Detection. If the redesign has not reached oracle 1 and nop 0 by the end of day three, the trial budget
is at risk.

Mitigation. Kill criterion 7 makes the fallback explicit rather than leaving it to be discovered late.
The Phase 1A task already passes every mechanical gate and is fully documented; shipping it with an
honest account of its difficulty, including the calibration result that shows it is too easy, is a worse
submission than a well-calibrated redesign but a better one than an unfinished redesign. Sequence the
Codex plan decision early so it is not discovered on the critical path.

## R13. Schedule (now the governing constraint)

No longer a risk to manage alongside the others. It is the constraint that decides what ships.

Three days spent, roughly four left, and they must cover implementation, calibration, eight trials and a
written failure analysis. Three task families have been explored: one solved by Opus in ten and a half
minutes, one retired on the generalisation of that result, one disproven at its design gate. The Codex
blocker is now closed, so trials are executable, but there is no longer room for a fourth exploration.

Decision recorded in ADR-030: stop pivoting, harden the Phase 1A task, run the eight trials, and report
the difficulty result honestly whichever way it lands. The fallback is explicit rather than discovered
late: a submission that runs, with real trial evidence and a frank analysis, beats a better idea with
nothing executable behind it.

Detection. If hardening plus one calibration has not moved the Opus solve time materially by end of day
five, stop iterating on difficulty and spend the remainder on trials and documentation.

## R14. The gateway mechanism does not produce the failure (CLOSED, fatal)

Closed by disproof rather than by mitigation, and it killed the design.

The concern was that the gateway repair might be parametric, letting a fuzz-guided grid search find it.
The pre-implementation gate found something worse: under provider-orphan semantics the shipped
architecture does not exhibit the failure at all. 432 configurations with a clean no-fault baseline
produced zero harm to the unaffected provider.

A timeout is a release, so a faulted domain cannot hold the shared worker pool. Orphaned work occupies
per-provider capacity, which cannot starve a different provider. Cross-domain starvation and orphaning
pull in opposite directions, and parameters move along that trade-off rather than escaping it. Full
argument in [GATEWAY_FEASIBILITY_PROOF.md](GATEWAY_FEASIBILITY_PROOF.md); decision in ADR-029.

The general lesson worth keeping: model the failure numerically before designing a task around it. Two of
the three families were disproven only after real investment, and this one was caught in an afternoon
because the check ran first.

## R15. Opus recognises the pattern by name

Metastable failure under retry amplification is well documented and Opus will recognise it immediately.
Pattern recognition alone must not be sufficient.

Detection. The calibration trajectory. If the first patch is bulkheads plus a retry budget and it passes,
the design failed. The intended shape is that the first patch fixes the trigger and the control schedules
still fail because the sustaining mechanism is elsewhere.

Mitigation. The trigger and the sustaining mechanism are deliberately different: retry re-entry at the
queue head triggers the collapse, while deadline expiry classified as retryable sustains it after the
fault clears. A canonical bulkhead-and-budget pass fixes the first and leaves the second. Keeping those
separable is a design obligation, not an accident, and it is the first thing to check if calibration
comes back clean.

## R16. No candidate family has cleared the difficulty bar (OPEN, decisive)

Six families evaluated, five settled against evidence, plus three probes of existing corpus tasks that
all passed cleanly. Originality was clean every time; difficulty was binding every time. Full tables in
ADR-034 and ADR-035.

The three Opus probes matter as much as the six rejections. `telecom-entity-resolution`,
`formal-crypto` and `vf2-speedup-networkx` were selected to test three different published failure
mechanisms, and all three passed at 40 to 58 percent of budget with large margins. Every one of them
shipped the agent something from which it could construct ground truth: an SSN anchor, the cipher
source, NetworkX itself. `vf2` added a further finding: even where the feedback signal was genuinely
low-resolution, a single scalar speed gate, the agent did not need to search, because it computed the
required budget from a measured baseline and designed to it directly.

Five families evaluated, four against evidence. Originality was clean every time; difficulty was binding
every time. Full table in ADR-034.

Two distinct causes, not one. For the four debugging families: a task is easy whenever the agent can
construct something that labels the repair, and a pointwise oracle, a differential fuzzer and a model
checker are the same object from the agent's side. For the construction family: the competent first move
was also the winning move, because a general-purpose compressor already captured the redundancy the
application-specific design was meant to add.

Detection. Already detected five times, at steadily decreasing cost as the empirical check moved earlier:
two implementation days, then an afternoon, then under an hour, then a single measurement pass.

Mitigation. Two honest options remain and the choice decides what is submitted, so it is the candidate's.
Accept a domain where difficulty is inherent rather than engineered, which means real scale, formal
proof, or deep specialist knowledge, and accept that the remaining runway is thin for it. Or ship the
Phase 1A task, which passes every mechanical gate, with the eight required trials and an analysis that
reports its difficulty honestly, including the calibration showing a ten-minute solve. The second is a
weaker task and a more truthful submission, and the record of five families with the evidence that
disproved each is itself substantive.

## R11. Host architecture differs from CI

Local development is arm64 Apple Silicon. TB3 CI runners and the Modal backend are x86-64. A task that
builds and passes here can still fail there, and `check-dockerfile-platform` forbids pinning the platform
to paper over it.

Detection. Build and run the task under `--platform linux/amd64` as well as natively before declaring it
done. Docker Desktop on this host advertises `linux/amd64` and an emulated container reports `x86_64`, so
both are testable locally.

Mitigation. The environment is pure Python plus SQLite with no compiled dependencies and no downloaded
binaries, which removes almost all of the exposure by construction. Base images are multi-arch. The
remaining risk is behavioural rather than structural, so the amd64 pass is a confirmation step rather than
a debugging session.

## R12. Local trials run on a different backend than CI

CI runs `/run` and `/cheat` on Modal. We will run them on Docker, because Modal needs tokens we do not
have. The results still count as evidence, but the write-up has to say which backend produced them.

Detection. Not a failure mode that hides. It is a documentation obligation.

Mitigation. State the divergence in the evaluation write-up alongside the Harbor version for every
command. Keep the task backend-agnostic, which it already is: no GPU, no sidecars, no compose file, and
resource requests well inside what a laptop and a Modal sandbox both provide. The local Docker VM has 10
CPUs and about 8.2 GB against a request of 2 CPUs and 2048 MB, so headroom is not the constraint.

## Tracked, not driving design

Artifact size. `artifacts = ["/app/"]` ships whatever the agent left in the tree. The tree is small and
has no dependency directories, so the exposure is an agent writing large scratch files. If that shows up
in trials, the answer is an exclude list on the artifact entry rather than a diff-based collect hook,
which would add a failure mode worse than the problem.

Test suite length. The rubric prefers hand-written tests under roughly a hundred lines. A differential
harness with two lanes, several worlds, and privilege dropping will exceed that. Merged tasks in the same
shape exceed it by a wide margin, so this is a documentation problem rather than a design problem: keep
the assertions few and readable, and let the fixture machinery be obviously fixture machinery.

Base image drift. Pin the interpreter version in both Dockerfiles and keep the pins identical across the
two images, since the rubric checks duplicated assets for silent divergence.
