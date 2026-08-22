# Intrinsic-gap spike: trajectory-store

Verdict: **rejected. There is no structural gap.** The spike asked whether an
application-specific trajectory representation can beat generic storage by a large margin while
preserving exact semantics, efficient random access and bounded memory. It cannot, and the reason
generalises past this design.

All prototype code and generated corpora live under `/tmp/tjspike`, outside the repository. No task
directory was created.

## 1. Workload

Three regimes from one deterministic generator, each 800 trajectories of 8 to 22 steps, with realistic
production shape: shared tool schemas, shared system prompts, trajectories branching from common
prefixes, observations with repeated structural keys, a pool of recurring prose with a minority of
unique tokens, and diverging suffixes. No adversarial entropy.

| Regime | Raw JSONL | Character |
|--------|-----------|-----------|
| W1 prefix-heavy | 82.7 MB | 40-trajectory groups sharing 75% of their step prefix |
| W2 schema-heavy | 132.8 MB | little prefix sharing, heavy repeated schemas and observations |
| W3 mixed | 93.6 MB | balanced |

## 2. Results, W3 mixed

| Store | Size MB | Ratio | Build RSS | Build s | Open s | get ms | range ms | Query RSS |
|-------|---------|-------|-----------|---------|--------|--------|----------|-----------|
| B0 raw JSONL + index | 93.6 | 1.0 | 24 | 0.3 | 0.00 | 0.11 | 0.11 | 23 |
| B1 whole-corpus gzip | 9.8 | 9.5 | 149 | 1.3 | 0.20 | 0.14 | 0.13 | 315 |
| B1 whole-corpus lzma | **3.8** | 24.8 | 213 | 36.1 | 0.56 | **0.10** | 0.10 | 307 |
| B2 per-trajectory gzip | 10.4 | 9.0 | 24 | 1.4 | 0.00 | 0.15 | 0.15 | **23** |
| B2 per-trajectory lzma | 7.5 | 12.5 | 42 | 34.5 | 0.00 | 1.15 | 1.14 | 23 |
| B3 SQLite normalised | 14.2 | 6.6 | 30 | 1.2 | 0.00 | 0.28 | 0.28 | 25 |
| REF (gzip, 256 KB blocks) | 7.1 | 13.2 | 33 | 1.2 | 0.00 | 0.42 | 0.35 | 35 |
| REF (lzma, 256 KB blocks) | 4.4 | 21.3 | 49 | 23.4 | 0.01 | 6.38 | | 51 |
| REF (lzma, 4 MB blocks) | 3.8 | 24.6 | 96 | 26.3 | 0.01 | 24.27 | | 127 |

## 3. Results, W1 prefix-heavy

This is the regime the reference architecture is designed to win, with 75% prefix sharing across
40-trajectory groups.

| Store | Size MB | Ratio | get ms | Query RSS |
|-------|---------|-------|--------|-----------|
| B1 whole-corpus lzma | **2.4** | 34.1 | **0.09** | 274 |
| REF (lzma, 256 KB blocks) | 2.8 | 29.9 | 5.12 | 27 |
| B2 per-trajectory gzip | 8.8 | 9.4 | 0.13 | 23 |

The reference is **larger and 57 times slower** than whole-corpus lzma, in its best case.

## 4. The strongest generic solution

The obvious frontier-agent answer is not plain whole-file compression. It is whole-file compression plus
a decompress-once open path: stream-decompress the archive to scratch, build an offset index, mmap.
Measured on W3:

```
archive       3.8 MB          (ties the best size in the entire study)
build         36.6 s, RSS 203 MB
open          0.6 s one-time, 94 MB scratch file
get           0.114 ms        (fastest of everything except raw JSONL)
query RSS     206 MB
```

It achieves **best size and best random access simultaneously**. The reference cannot match it: at equal
archive size, 3.8 MB, the reference is 213 times slower per query.

## 5. Why the idea fails, and why that generalises

Structural interning and content addressing remove redundancy that a strong general-purpose compressor
already removes, and they remove it less well. A large-window LZMA pass over the whole corpus finds the
shared prefixes, the repeated tool schemas, and the recurring prose. That is what a dictionary-based
compressor does. An application-specific dedup layer mostly reorganises bytes LZMA was going to collapse
anyway, while imposing a block structure that shrinks the effective window and costs compression ratio.

The one axis where application structure genuinely should win is random access without full decode. The
generic answer defeats that too, by decoding once into scratch and indexing.

The block-size sweep shows the trade with no escape: gzip at 256 KB gives 0.42 ms queries at 7.1 MB;
LZMA at 4 MB gives 3.8 MB at 24 ms. Moving along that curve is all the architecture can do. It does not
dominate anywhere.

## 6. Kill conditions triggered

**#1, whole-file generic compression passes every intended constraint.** Demonstrated in section 4.

**#5, reference advantage is modest.** Against the strongest random-access baseline on W3, 10.4 MB
versus 7.1 MB is 1.46x, below the 1.5x threshold. On W1 the advantage is negative against whole-corpus
lzma.

**#6 and #8, the remaining constraints would have to be arbitrary.** The only axis where the generic
answer is weak is resident memory and scratch disk. Making it fail requires a tight RSS bound and a ban
on scratch files, both numbers chosen to defeat a specific baseline rather than derived from anything.
That is the arbitrary threshold the gate forbids and the unnaturally shaped workload it forbids.

## 7. What was not reached

The alternative architecture families, search-gradient analysis, verifier isolation plan and schedule
estimate were not developed. Each presupposes a gap that section 3 shows does not exist. Sketching two
passing designs for a feasible region that is empty, or planning phase isolation for a task that would
be solved by `lzma.compress`, would be documentation of a problem that is not there.

For the record, the verifier isolation plan is the one part that would have been straightforward:
build phase produces the archive, verifier deletes the source corpus and kills every subject process,
query phase runs in a fresh process against the archive alone. Nothing about that was the blocker.

## 8. Originality

Clean, and not the binding constraint. `rs-archive-clone` is black-box reimplementation of an archive
tool's exact behaviour, not compact storage design. `distributed-dedup` grades a Spark near-duplicate
pipeline against resource budgets. No corpus task grades application-specific compact storage under
simultaneous exactness, random-access, memory and size constraints. The family fails on difficulty, not
on novelty.

## 9. Human expert path, and why it is short

A strong storage engineer would identify the redundancy classes, estimate how much of it a general
compressor already captures, and reach the answer in section 4 quickly. That is the problem: the
competent first move is also the winning move. The task would reward knowing that `lzma` has a large
window, which is not a benchmark-worthy capability.
