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

Each directory holds the submission that agent wrote and the grade it received. To regrade one, run
its `reconcile.py` against a generated period and compare against a golden built from the same
period:

```bash
# generate a period and build the golden for it
T=tasks/desk-position-reconcile
python3 $T/environment/data/generate_inputs.py /tmp/period --seed 4242 \
    --accounts 40000 --rollups 12000 --links 6000 --fills 25000 --days 365 --shards 4
cp $T/environment/data/reporting_policy.yaml /tmp/period/
python3 $T/tests/verifier_env/build_golden.py /tmp/period /tmp/golden

# run one implementer's submission and grade it
python3 results/implementer-probe/implementer-1/reconcile.py /tmp/period \
    --policy /tmp/period/reporting_policy.yaml --output /tmp/impl1 --seed 42
python3 tools/grade-implementer.py /tmp/impl1 /tmp/golden
```

`tools/grade-implementer.py` applies the same token bijection logic the real verifier uses: it reads
the attribution to establish which submitted token corresponds to which real entity, then compares
every report exactly through that mapping.
