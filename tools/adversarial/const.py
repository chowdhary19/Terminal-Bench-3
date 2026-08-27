#!/usr/bin/env python3
"""Adversarial fixture: the reference, but the entity token ignores --seed.

Must score 0. The verifier's seed-sensitivity test requires every token to change
when the seed changes while no other column does.

Usage: mounted at /app/reconcile.py with the reference at /app/_ref.py.
"""
import pathlib, sys

ref = pathlib.Path("/app/_ref.py")
src = ref.read_text().replace('f"{self.seed}|clearing_account|{entity}"',
                              'f"0|clearing_account|{entity}"')
exec(compile(src, str(ref), "exec"), {"__name__": "__main__", "sys": sys})
