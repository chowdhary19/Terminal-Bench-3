#!/usr/bin/env python3
"""Adversarial fixture: plant symlinks to the golden and let root dereference them.

The mechanism: the verifier's output directory is chmod 0o777, and the verifier
process (root) reads the submission's report files by path. So an unprivileged
submission can plant symlinks pointing at the root-only golden, and root will
dereference them. The sample-period tests never compare against a golden, so
they can be satisfied with fabricated rows.

The verifier now rejects any report that is not a regular file with a single
link sitting directly in the output directory, and opens every report with
O_NOFOLLOW, so this scores 0.
"""
import argparse
import hashlib
import os
import pathlib
import time

REPORTS = {
    "attribution.csv": (["fill_id", "account", "instrument", "counterparty", "notional_usd"], [1]),
    "positions.csv": (["as_of", "account", "instrument", "net_quantity", "gross_quantity"], [1]),
    "fees.csv": (["as_of", "account", "venue", "fee_usd", "commission_usd", "rebate_usd"], [1]),
    "exposure.csv": (["as_of", "account", "settlement_asset", "net_exposure_usd",
                      "gross_exposure_usd", "haircut_usd", "net_after_haircut_usd"], [1]),
    "counterparty_exposure.csv": (["as_of", "counterparty", "settlement_asset",
                                   "net_exposure_usd", "gross_exposure_usd"], []),
    "netting_exposure.csv": (["as_of", "netting_set", "settlement_asset",
                              "net_exposure_usd", "gross_exposure_usd"], []),
    "venue_summary.csv": (["as_of", "venue", "fill_count", "gross_notional_usd",
                           "fee_usd", "commission_usd"], []),
    "margin.csv": (["as_of", "account", "initial_margin_usd"], [1]),
    "lots.csv": (["account", "instrument_id", "open_quantity", "average_cost_usd",
                  "realised_pnl_usd", "fifo_realised_pnl_usd"], [0]),
    "financing.csv": (["account_ref", "priority", "cap_usd", "allocated_usd", "cumulative_usd"], []),
    "interest.csv": (["account", "closing_balance_usd", "interest_accrued_usd"], [0]),
}

ap = argparse.ArgumentParser()
ap.add_argument("inputs")
ap.add_argument("--policy")
ap.add_argument("--output")
ap.add_argument("--seed")
ap.add_argument("--max-memory")
a = ap.parse_args()

out = pathlib.Path(a.output)
out.mkdir(parents=True, exist_ok=True)

# Which period is this? The submission is handed the path, so it can just look.
is_main = "main" in str(pathlib.Path(a.inputs).resolve())

if is_main:
    # Plant symlinks into the world-writable output dir. root dereferences them.
    for name in REPORTS:
        tgt = out / name
        try:
            if tgt.exists() or tgt.is_symlink():
                tgt.unlink()
        except OSError:
            pass
        os.symlink(f"/tests/data/main/golden/{name}", tgt)
    # Give the RSS sampler something to sample so `peak > 0` holds.
    blob = bytearray(4 * 1024 * 1024)
    blob[0] = 1
    time.sleep(2.0)
else:
    # The sample-period tests only compare the submission to itself, so any
    # deterministic, seed-sensitive content passes them.
    tok = "ent_" + hashlib.sha256(f"{a.seed}|x".encode()).hexdigest()[:12]
    for name, (cols, tokcols) in REPORTS.items():
        row = []
        for i, c in enumerate(cols):
            row.append(tok if i in tokcols else ("2026-01-01" if c == "as_of" else "1"))
        with (out / name).open("w", newline="") as f:
            f.write(",".join(cols) + "\n")
            f.write(",".join(row) + "\n")
    time.sleep(0.5)
