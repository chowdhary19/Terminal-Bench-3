"""Grade an implementer's output exactly as the verifier does: tokens read as a
partition through the attribution bijection, every other report compared exactly."""
import csv, sys, ast, pathlib, collections, re
TASK = pathlib.Path("/Users/yuvraj/work/klavis/klavis-tb3-worktrial/tasks/desk-position-reconcile")
ns = {}
for n in ast.parse((TASK / "tests/test_outputs.py").read_text()).body:
    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "REPORTS":
        exec(compile(ast.Module([n], []), "<x>", "exec"), ns)
R = ns["REPORTS"]
TOKEN = re.compile(r"^ent_[0-9a-f]{12}$")
got_dir, gold = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])

def read(d, name, cols):
    p = d / name
    if not p.exists(): return None
    rows = list(csv.reader(p.open()))
    if not rows: return None
    if rows[0] != cols: return ("HEADER", rows[0])
    return [r for r in rows[1:]]

fails = []
missing = [n for n in R if not (got_dir / n).exists()]
if missing:
    print(f"VERDICT: FAIL — did not write {missing}"); raise SystemExit(1)
ga = read(got_dir, "attribution.csv", R["attribution.csv"][0])
wa = read(gold, "attribution.csv", R["attribution.csv"][0])
if not isinstance(ga, list):
    print(f"VERDICT: FAIL — attribution header wrong: {ga}"); raise SystemExit(1)
g = {r[0]: r[1] for r in ga}; w = {r[0]: r[1] for r in wa}
if set(g) != set(w):
    print(f"VERDICT: FAIL — attribution covers {len(g)} fills, expected {len(w)}"); raise SystemExit(1)
badtok = [t for t in set(g.values()) if not TOKEN.match(t)]
if badtok: fails.append(f"token format: {badtok[:2]}")
m2g, g2m = collections.defaultdict(set), collections.defaultdict(set)
for fid, t in g.items():
    m2g[t].add(w[fid]); g2m[w[fid]].add(t)
merged = [t for t, s in m2g.items() if len(s) > 1]
split = [k for k, s in g2m.items() if len(s) > 1]
if merged: fails.append(f"COLLISION: {len(merged)} tokens each span >1 entity")
if split:  fails.append(f"SPLIT: {len(split)} entities each carry >1 token")
tr = {next(iter(s)): k for k, s in g2m.items() if len(s) == 1}
for name, (cols, k, tok) in sorted(R.items()):
    rows = read(got_dir, name, cols)
    if not isinstance(rows, list):
        fails.append(f"{name}: header {rows}"); continue
    rows = [list(r) for r in rows]
    for r in rows:
        for i in tok: r[i] = tr.get(r[i], "?" + r[i])
    a = {tuple(r[:k]): tuple(r[k:]) for r in rows}
    b = {tuple(r[:k]): tuple(r[k:]) for r in read(gold, name, cols)}
    d = len(set(a) ^ set(b)) + sum(1 for x in a if x in b and a[x] != b[x])
    if d: fails.append(f"{name}: {d} rows wrong of {len(b)}")
print(("VERDICT: PASS" if not fails else "VERDICT: FAIL") + ("\n  " + "\n  ".join(fails) if fails else ""))
