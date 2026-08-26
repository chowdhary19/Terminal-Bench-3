#!/usr/bin/env python3
"""Summarise every trial under the jobs directory, one row per trial."""
import json, pathlib, sys
jobs = pathlib.Path(sys.argv[1])
if not jobs.exists():
    print("no jobs directory yet:", jobs); raise SystemExit(0)
rows = []
for d in sorted(jobs.iterdir()):
    if not d.is_dir() or not (d / "result.json").exists():
        continue
    try:
        j = json.load((d / "result.json").open())
    except Exception:
        continue
    cost = (j.get("stats") or {}).get("cost_usd")
    for t in sorted(x for x in d.iterdir() if x.is_dir()):
        rw = t / "verifier" / "reward.txt"
        exc = t / "exception.txt"
        if exc.exists():
            state = "VOID: " + exc.read_text()[:50].replace("\n", " ")
        elif rw.exists():
            state = rw.read_text().strip()
        else:
            state = "?"
        rows.append((d.name, t.name.split("__")[-1], state))
if not rows:
    print("no completed trials under", jobs); raise SystemExit(0)
w = max(len(r[0]) for r in rows)
print(f"{'job'.ljust(w)}  trial    reward")
for j, t, s in rows:
    print(f"{j.ljust(w)}  {t:8} {s}")
scored = [s for _, _, s in rows if s in ("0", "1")]
print(f"\n{len(scored)} scored trials: {scored.count('0')} at reward 0, {scored.count('1')} at reward 1; "
      f"{len(rows) - len(scored)} void/unscored")
