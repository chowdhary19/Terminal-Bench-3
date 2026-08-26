#!/usr/bin/env python3
"""Adversarial probe: the reference, but tokens ignore the seed."""
import runpy, sys, re, pathlib
src = pathlib.Path("/tmp/constprobe/ref.py").read_text()
src = src.replace('f"{self.seed}|clearing_account|{entity}"', 'f"0|clearing_account|{entity}"')
exec(compile(src, "ref", "exec"), {"__name__": "__main__"})
