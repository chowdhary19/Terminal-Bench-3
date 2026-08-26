# Sealed implementer probe

Run before any real trial was spent, as a fairness check. Three independent Opus agents, each
sealed in its own sandbox with exactly what the task container provides: the instruction, the
policy, and one generated period. No reference implementation, no golden files, no repository
access. Each was told to build the reconciliation to a professional standard and to report its own
judgment calls honestly.

All three failed. Each directory holds the submission that agent wrote and its grade, produced by
`tools/grade-implementer.py`, which applies the same token bijection logic the real verifier uses.

| Implementer | Verdict | What it got wrong |
|---|---|---|
| 1 | fail | Resolved every dimension at the fill's trade date instead of the row's date |
| 2 | fail | Same, and said so at 75 percent confidence in its own report |
| 3 | fail | Treated mergers as an unconditional rewrite, producing 18 token collisions |

Two results mattered.

The failures split across two different traps, and every individual decision was reached correctly
by at least one of the three. That is what showed the task was fair rather than arbitrary:
everything is derivable from the shipped artifacts. What none of them managed was getting all of
the decisions right at once.

And all three verified themselves into their errors. Every one built a second independent
implementation and reported zero mismatches against it. Implementer 2's own words: "I wrote a
separate naive Decimal/Fraction implementation of all 11 reports. Every row of all 11 files matches
exactly." It was wrong on nine of them.

## Reproducing

```bash
python3 tools/grade-implementer.py <output-dir> <golden-dir>
```
