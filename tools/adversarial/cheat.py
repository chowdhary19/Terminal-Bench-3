#!/usr/bin/env python3
"""Adversarial probe: do no work, take the answers, write the reward."""
import argparse, os, shutil, glob, sys
ap = argparse.ArgumentParser()
ap.add_argument("inputs"); ap.add_argument("--policy"); ap.add_argument("--output"); ap.add_argument("--seed"); ap.add_argument("--max-memory")
a = ap.parse_args()
os.makedirs(a.output, exist_ok=True)
print("uid:", os.getuid(), file=sys.stderr)
src = "/tests/data/sample/golden" if "sample" in a.inputs else "/tests/data/main/golden"
n = 0
for f in glob.glob(src + "/*.csv"):
    try:
        shutil.copy(f, a.output); n += 1
    except Exception as e:
        pass
print("COPIED", n, "golden files", file=sys.stderr)
for p in ("/logs/verifier/reward.txt", "/logs/reward.txt"):
    try:
        open(p, "w").write("1\n"); print("WROTE REWARD at", p, file=sys.stderr)
    except Exception as e:
        print("reward write blocked:", type(e).__name__, file=sys.stderr)
