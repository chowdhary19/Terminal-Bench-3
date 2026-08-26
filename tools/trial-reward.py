#!/usr/bin/env python3
"""Pull the reward out of a harbor job result. The mean lives under
stats.evals.<eval>.metrics[0].mean, not at the top level."""
import json, pathlib, sys
p = pathlib.Path(sys.argv[1]) / "result.json"
if not p.exists():
    print("?"); raise SystemExit(0)
j = json.load(p.open())
evals = (j.get("stats") or {}).get("evals") or {}
for name, e in evals.items():
    for m in e.get("metrics", []):
        if "mean" in m:
            print(m["mean"]); raise SystemExit(0)
print("?")
