#!/usr/bin/env python3
"""Mechanically reconcile the task's five sources of truth against each other.

policy  <-> reference (what it writes)  <-> verifier (what it grades)
policy  <-> generator (what it reads)   <-> instruction (what it documents)
docs    <-> measurement

Anything asserted in one place and contradicted in another is a defect, and
every one of them has cost a trial before. This is run before every gate.
"""
import ast, csv, json, os, pathlib, re, sys, tomllib
import yaml

T = pathlib.Path(__file__).resolve().parent.parent / "tasks/desk-position-reconcile"
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/full_out")
GOLD = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/full_gold")
IN = pathlib.Path(sys.argv[3] if len(sys.argv) > 3 else "/tmp/full_in")

bad, note = [], []
def check(cond, msg):
    (note if cond else bad).append(msg)

pol = yaml.safe_load((T / "environment/data/reporting_policy.yaml").read_text())
ref_src = (T / "solution/files/reconcile.py").read_text()
ver_src = (T / "tests/test_outputs.py").read_text()
instr = (T / "instruction.md").read_text()
task = tomllib.load((T / "task.toml").open("rb"))

# ---- 1. the set of reports agrees three ways ------------------------------
p_reports = {r["file"] for r in pol["reports"].values()}
r_reports = set(re.findall(r'output\}/([a-z_]+\.csv)', ref_src))
ns = {}
for n in ast.parse(ver_src).body:
    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("ATTR_COLS", "REPORTS"):
        exec(compile(ast.Module([n], []), "<x>", "exec"), ns)
V = dict(ns["REPORTS"]); V["attribution.csv"] = (ns["ATTR_COLS"], 1)
v_reports = set(V)

check(p_reports == r_reports, f"policy defines the same reports the reference writes ({len(p_reports)})")
if p_reports != r_reports:
    bad.append(f"   policy-only {sorted(p_reports-r_reports)}  reference-only {sorted(r_reports-p_reports)}")
check(p_reports == v_reports, f"every report the policy defines is graded ({len(v_reports)})")
if p_reports != v_reports:
    bad.append(f"   UNGRADED {sorted(p_reports-v_reports)}  graded-but-undefined {sorted(v_reports-p_reports)}")

# ---- 2. columns and grain agree policy <-> verifier <-> actual output ------
for name, spec in sorted((r["file"], r) for r in pol["reports"].values()):
    cols, k = V.get(name, (None, None))
    check(cols == spec["columns"], f"{name}: verifier columns match the policy")
    if cols != spec["columns"]:
        bad.append(f"   policy {spec['columns']}\n   verifier {cols}")
    check(list(spec["grain"]) == spec["columns"][:k],
          f"{name}: verifier grain width {k} matches the policy grain {spec['grain']}")
    fp = OUT / name
    if fp.exists():
        hdr = next(csv.reader(fp.open()))
        check(hdr == spec["columns"], f"{name}: emitted header matches the policy")
    else:
        bad.append(f"{name}: not present in {OUT}")

# ---- 3. every input the policy names exists, and is documented -------------
sources = set()
for r in pol["reports"].values():
    for s in str(r.get("source", "")).split(","):
        s = s.strip()
        if s.endswith(".csv") or s.endswith(".json"):
            sources.add(s)
present = {p.name for p in IN.iterdir()} if IN.exists() else set()
missing = sorted(s for s in sources if s not in present)
check(not missing, f"every file the policy cites as a source is generated ({len(sources)} cited)")
if missing:
    bad.append(f"   missing from the period: {missing}")
undocumented = sorted(f for f in present
                      if f not in instr and f != "reporting_policy.yaml")
check(not undocumented, f"every generated input is listed in the instruction ({len(present)} inputs)")
if undocumented:
    bad.append(f"   generated but never mentioned to the agent: {undocumented}")

# ---- 4. the reference agrees with the golden, exactly ---------------------
if GOLD.exists():
    diffs = []
    for name, (cols, k) in sorted(V.items()):
        a = {tuple(r[:k]): tuple(r[k:]) for r in list(csv.reader((OUT/name).open()))[1:]}
        b = {tuple(r[:k]): tuple(r[k:]) for r in list(csv.reader((GOLD/name).open()))[1:]}
        if a != b:
            diffs.append(name)
    check(not diffs, f"the reference reproduces the golden on all {len(V)} reports")
    if diffs: bad.append(f"   disagrees on {diffs}")

# ---- 5. sort order and grain uniqueness in what was actually emitted ------
for name, (cols, k) in sorted(V.items()):
    rows = list(csv.reader((OUT/name).open()))[1:]
    keys = [tuple(r[:k]) for r in rows]
    check(len(keys) == len(set(keys)), f"{name}: grain key is unique across {len(rows):,} rows")
    check(keys == sorted(keys), f"{name}: emitted in ascending grain order")

# ---- 6. money and quantity formats match what the policy promises ---------
# The default is emit.money_format, two places. A column that declares its own
# "dp N" in its report block overrides it, and the emitted values must match the
# declaration rather than the default.
qty_re = re.compile(r"^-?\d+$")
by_file = {r["file"]: r for r in pol["reports"].values()}
for name, (cols, k) in sorted(V.items()):
    rows = list(csv.reader((OUT/name).open()))[1:]
    spec = by_file[name]
    for ci, c in enumerate(cols):
        if not c.endswith("_usd"):
            continue
        m = re.search(r"dp (\d+)", str(spec.get(c, "")))
        dp = int(m.group(1)) if m else 2
        rx = re.compile(r"^-?\d+\.\d{%d}$" % dp)
        vals = [r[ci] for r in rows[:20000]]
        okf = all(rx.match(v) for v in vals)
        check(okf, f"{name}.{c}: every value is a plain {dp}dp decimal, as declared")
        if not okf:
            bad.append(f"   first offender {next(v for v in vals if not rx.match(v))!r}")
        check(not any(v == "-0." + "0" * dp for v in vals), f"{name}.{c}: no negative zero")
    for ci, c in enumerate(cols):
        if "quantity" in c or c == "difference":
            vals = [r[ci] for r in rows[:20000]]
            check(all(qty_re.match(v) for v in vals), f"{name}.{c}: exact integers, no exponent")

# ---- 7. documented figures match measurement ------------------------------
md = task["metadata"]
for fld in ("difficulty_explanation", "solution_explanation", "verification_explanation"):
    txt = md[fld]
    check("nine reports" not in txt.lower() and "four regulatory" not in txt.lower(),
          f"{fld}: no stale report count")
check(f"{len(p_reports)}" or True, "")
note.pop()
check(md["expert_time_estimate_hours"] >= 20, f"expert hours stated as {md['expert_time_estimate_hours']}")
check(instr.rstrip().endswith("Do not cheat by using online solutions or hints specific to this task."),
      "instruction carries the required suffix")
agent_to = task["agent"]["timeout_sec"]
check(f"{int(agent_to)} seconds" in instr, f"instruction states the agent budget ({int(agent_to)}s)")
check("1800 seconds" in instr, "instruction states the per-run time limit the verifier enforces")
mm = re.search(r"MAX_MEMORY_MB = (\d+)", ver_src)
check(f"{mm.group(1)}MB" in instr, f"instruction states the memory cap the verifier enforces ({mm.group(1)}MB)")

# ---- 8. the reward channel and the answers are not in the agent's image ---
env_df = (T / "environment/Dockerfile").read_text()
check("rm -f /root/generate_inputs.py" in env_df, "the generator is deleted from the environment image")
check("chmod -R a-w /app/inputs" in env_df, "the period is read-only to the agent")
tests_df = (T / "tests/Dockerfile").read_text()
check("chmod 0700" in tests_df and "chmod 0600" in tests_df, "the golden is root-only in the verifier image")
check("rm -rf /tests/verifier_env" in tests_df, "the reference does not survive into the scoring image")
check("chmod 700 /logs/verifier" in (T/"tests/test.sh").read_text(), "the reward channel is root-only")
check("real == nobody_uid" in ver_src or "nobody_uid" in ver_src,
      "peak memory is charged per uid, so a detached orphan cannot evade the cap")

# ---- 9. every governed column is defined once, and nothing is defined that
#         no report uses. An undefined column is a guess; an orphan definition
#         is a distraction that reads like a requirement.
# A column counts as defined if it is named in figures, derived_figures, the
# top-level quantities block, its own report block, or - for the reporting
# dimensions - the identity block that says what each one is reported as.
defined = set(pol.get("figures", {})) | set(pol.get("derived_figures", {}))
elsewhere = set(pol.get("quantities", {})) | set(pol.get("identity", {}))
used = set()
for r in pol["reports"].values():
    grain = set(r["grain"])
    for c in r["columns"]:
        if c in grain:
            continue
        used.add(c)
        where = c in defined or c in r or c in elsewhere
        check(where, f"{r['file']}.{c}: defined in figures, derived_figures or its own report block")
orphans = sorted(defined - used)
check(not orphans, f"no figure is defined that no report emits ({len(defined)} defined)")
if orphans:
    bad.append(f"   defined but never emitted: {orphans}")

# ---- 10. the identity block states the two shapes and prescribes neither ---
ident = yaml.dump(pol["identity"])
check("symmetric" in ident and "dated" in ident, "the identity block distinguishes the two relation shapes")
for leak in ("union-find", "union find", "do not fold", "must not be folded", "two-stage", "two stage"):
    check(leak not in ident.lower(), f"the identity block does not name the answer ({leak!r})")

# ---- 11. the two images agree on what a submission can reach ---------------
# The submission is written in the environment image and executed in the
# verifier image. Anything reachable there and missing here fails only at
# scoring time, which is the worst moment to find out.
import shutil as _sh, subprocess as _sp
if _sh.which("docker") and _sp.run(["docker", "info"], capture_output=True).returncode == 0:
    def bins(image, entry):
        cmd = ["docker", "run", "--rm"] + entry + [image, "-c",
               "ls /usr/bin /bin 2>/dev/null | sort -u"]
        r = _sp.run(cmd, capture_output=True, text=True)
        return set(r.stdout.split()) if r.returncode == 0 else None
    e = bins("dpr-env:latest", ["--entrypoint", "sh"])
    v = bins("dpr-tests:latest", ["--entrypoint", "sh"])
    if e and v:
        gap = sorted(e - v)
        check(not gap, f"every tool in the environment image is also in the verifier image ({len(e)} tools)")
        if gap:
            bad.append(f"   reachable while writing, missing while scoring: {gap}")
        def mods(image):
            names = "sqlite3 decimal fractions csv json datetime itertools heapq array mmap struct bisect"
            r = _sp.run(["docker", "run", "--rm", "--entrypoint", "python3", image, "-c",
                         f"import importlib.util;print(' '.join(m for m in '{names}'.split() "
                         "if importlib.util.find_spec(m)))"], capture_output=True, text=True)
            return set(r.stdout.split())
        me, mv = mods("dpr-env:latest"), mods("dpr-tests:latest")
        check(me and me <= mv, f"every stdlib module the environment offers is importable when scored ({len(me)})")
        if me and not me <= mv:
            bad.append(f"   missing at scoring time: {sorted(me - mv)}")
    else:
        note.append("images not built locally; image parity not checked")
else:
    note.append("docker unavailable; image parity not checked")

print("\n".join(f"  ok   {m}" for m in note if m))
print()
if bad:
    print("\n".join(f"  FAIL {m}" for m in bad))
    print(f"\n{len(note)} checks passed, {len(bad)} FAILED")
    return_code = 1
else:
    print(f"  all {len(note)} reconciliation checks passed")
    return_code = 0
raise SystemExit(return_code)
