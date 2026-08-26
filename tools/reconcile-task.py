#!/usr/bin/env python3
"""Mechanically reconcile the task's sources of truth against each other, and
guard the leak surface.

policy <-> reference (what it writes) <-> verifier (what it grades)
policy <-> generator (what it reads)  <-> instruction (what it documents)
shipped artifacts <-> the concealment discipline (what they must NOT say)

Run before every gate. Usage: reconcile-task.py [out_dir] [gold_dir] [in_dir]
"""
import ast, csv, os, pathlib, re, subprocess, sys, tomllib
import yaml

T = pathlib.Path(__file__).resolve().parent.parent / "tasks/desk-position-reconcile"
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v2f_out")
GOLD = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/v2f_gold")
IN = pathlib.Path(sys.argv[3] if len(sys.argv) > 3 else "/tmp/v2f")

bad, note = [], []
def check(cond, msg):
    (note if cond else bad).append(msg)

pol_text = (T / "environment/data/reporting_policy.yaml").read_text()
pol = yaml.safe_load(pol_text)
ref_src = (T / "solution/files/reconcile.py").read_text()
ver_src = (T / "tests/test_outputs.py").read_text()
instr = (T / "instruction.md").read_text()
task = tomllib.load((T / "task.toml").open("rb"))

# ---- 1. reports agree three ways ------------------------------------------
p_reports = set(pol["reports"])
r_reports = set(re.findall(r'\{O\}/([a-z_]+\.csv)', ref_src))
ns = {}
for n in ast.parse(ver_src).body:
    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "REPORTS":
        exec(compile(ast.Module([n], []), "<x>", "exec"), ns)
V = ns["REPORTS"]
check(p_reports == r_reports, f"policy defines the reports the reference writes ({len(p_reports)})")
if p_reports != r_reports:
    bad.append(f"   policy-only {sorted(p_reports - r_reports)}  reference-only {sorted(r_reports - p_reports)}")
check(p_reports == set(V), f"every report the policy defines is graded ({len(V)})")
if p_reports != set(V):
    bad.append(f"   UNGRADED {sorted(p_reports - set(V))}  graded-but-undefined {sorted(set(V) - p_reports)}")

# ---- 2. columns, grain and token columns agree policy <-> verifier <-> output
for name, spec in sorted(pol["reports"].items()):
    cols = list(spec["columns"])
    vcols, k, tok = V.get(name, (None, None, None))
    check(vcols == cols, f"{name}: verifier columns match the policy")
    if vcols != cols:
        bad.append(f"   policy {cols}\n   verifier {vcols}")
    check(list(spec["sort"]) == cols[:k], f"{name}: verifier grain {cols[:k]} is the policy's sort key")
    ptok = [i for i, c in enumerate(cols) if spec["columns"][c] == "entity_token"]
    check(ptok == tok, f"{name}: verifier token columns {tok} match the policy's entity_token columns {ptok}")
    fp = OUT / name
    if fp.exists():
        check(next(csv.reader(fp.open())) == cols, f"{name}: emitted header matches the policy")
    else:
        bad.append(f"{name}: not present in {OUT}")

# ---- 3. every source the policy names is generated -------------------------
sources = set()
for blk in pol.values():
    if isinstance(blk, dict):
        s = blk.get("source")
        for x in (s if isinstance(s, list) else [s] if s else []):
            sources.add(x)
present = {p.name for p in IN.iterdir()} if IN.exists() else set()
missing = sorted(s for s in sources if s.rstrip("/") not in present)
check(not missing, f"every file the policy cites as a source is generated ({len(sources)} cited)")
if missing:
    bad.append(f"   missing: {missing}")

# ---- 4. the reference reproduces the golden, exactly ----------------------
if GOLD.exists():
    diffs = []
    for name, (cols, k, tok) in sorted(V.items()):
        a = {tuple(r[:k]): tuple(r[k:]) for r in list(csv.reader((OUT / name).open()))[1:]}
        b = {tuple(r[:k]): tuple(r[k:]) for r in list(csv.reader((GOLD / name).open()))[1:]}
        if a != b:
            diffs.append(name)
    check(not diffs, f"the reference reproduces the golden on all {len(V)} reports")
    if diffs:
        bad.append(f"   disagrees on {diffs}")

# ---- 5. emitted structure: grain unique, sorted, tokens well formed --------
TOKEN = re.compile(r"^ent_[0-9a-f]{12}$")
for name, (cols, k, tok) in sorted(V.items()):
    rows = list(csv.reader((OUT / name).open()))[1:]
    keys = [tuple(r[:k]) for r in rows]
    check(len(keys) == len(set(keys)), f"{name}: grain key unique across {len(rows):,} rows")
    check(keys == sorted(keys), f"{name}: emitted in ascending grain order")
    if tok:
        check(all(TOKEN.match(r[i]) for r in rows for i in tok), f"{name}: every token is ent_ + 12 hex")
    spec = pol["reports"][name]["columns"]
    for ci, c in enumerate(cols):
        kind = spec[c]
        vals = [r[ci] for r in rows[:20000]]
        if kind == "figure":
            rx = re.compile(r"^-?\d+\.\d\d$")
            check(all(rx.match(v) for v in vals), f"{name}.{c}: plain 2dp decimals")
            check("-0.00" not in vals, f"{name}.{c}: no negative zero")
        elif kind == "figure_6dp":
            check(all(re.match(r"^-?\d+\.\d{6}$", v) for v in vals), f"{name}.{c}: plain 6dp decimals")
        elif kind in ("quantity", "count"):
            check(all(re.match(r"^-?\d+$", v) for v in vals), f"{name}.{c}: exact integers")

# ---- 6. the leak surface ---------------------------------------------------
comments = [l for l in pol_text.splitlines() if l.strip().startswith("#")]
check(len(comments) <= 1, f"policy carries {len(comments)} comment line(s); the canary is the only one allowed")
BANNED = ["symmetric", "undated", "not before", "does not apply again", "half-open", "strictly",
          "deliberate", "so a ", "this is not", "sorts first", "union", "fold", "two-stage", "as at",
          "as_at", "in force", "chain", "directional", "donor", "survivor", "must not", "never",
          "worked example", "e.g.", "i.e.", "note", "beware", "trap", "carefully"]
low = pol_text.lower()
hits = [b for b in BANNED if b in low]
check(not hits, f"policy contains none of the answer-shaped phrases ({len(BANNED)} checked)")
if hits:
    bad.append(f"   found: {hits}")
ilow = instr.lower()
ihits = [b for b in BANNED if b in ilow and b not in ("never",)]
check(not ihits, "instruction contains none of the answer-shaped phrases")
if ihits:
    bad.append(f"   found: {ihits}")
body = [l for l in instr.splitlines() if l.strip() and not l.startswith("<!--")]
check(len(body) <= 12, f"instruction body is {len(body)} non-empty lines (<= 12)")
named = re.findall(r"/app/inputs/([a-z_]+\.(?:csv|yaml|json))", instr)
check(set(named) <= {"reporting_policy.yaml", "account_links.csv"},
      f"instruction names only the policy and the one file the token sentence needs ({sorted(set(named))})")
check("fills/" not in instr and "effective_from" not in instr, "instruction does not describe the input schema")
check(not re.search(r"reference implementation|takes about|seconds\b(?!.*complete this task)", instr.replace(
    "You have 3600 seconds to complete this task.", "")), "instruction carries no runtime hint")
for phrase in ("effective-dated account mergers", "account_links.csv", "same token", "deterministic",
               "change when the seed", "--max-memory"):
    check(phrase in instr, f"instruction states the tested property: {phrase!r}")
check(instr.rstrip().endswith("Do not cheat by using online solutions or hints specific to this task."),
      "instruction carries the required suffix")
check(f"{int(task['agent']['timeout_sec'])} seconds" in instr, "instruction states the agent budget")
mm = re.search(r"MAX_MEMORY_MB = (\d+)", ver_src)
check(f"--max-memory {mm.group(1)}MB" in instr, f"instruction's command line carries the verifier's cap ({mm.group(1)}MB)")

# ---- 7. the data is armed -------------------------------------------------
if IN.exists():
    fills = []
    for f in sorted((IN / "fills").glob("*.csv")):
        fills.extend(csv.DictReader(f.open()))
    forms = {"structured": 0, "handle": 0, "local": 0}
    for r in fills:
        ref = r["account_ref"]
        forms["structured" if ref.startswith("acct::") else "handle" if ":acct:" in ref else "local"] += 1
    check(all(v > 0.15 * len(fills) for v in forms.values()), f"all three reference forms are common {forms}")
    locals_ = {}
    for r in fills:
        if not r["account_ref"].startswith("acct::") and ":acct:" not in r["account_ref"]:
            locals_.setdefault(r["account_ref"], set()).add(r["clearing_scope"])
    multi = sum(1 for v in locals_.values() if len(v) > 1)
    check(multi >= 0.5 * len(locals_), f"bare local ids collide across books ({multi} of {len(locals_)})")
    codes = {}
    for r in csv.DictReader((IN / "venue_account_map.csv").open()):
        codes.setdefault((r["venue"], r["venue_code"]), set()).add(r["account_ref"])
    re_ = sum(1 for v in codes.values() if len(v) > 1)
    check(re_ > 100, f"venue codes are reassigned over time ({re_} codes with more than one account)")
    mg = list(csv.DictReader((IN / "account_mergers.csv").open()))
    check(all(":acct:" in m["venue_handle"] for m in mg), "every merger names its donor by a venue handle")
    rd = yaml.safe_load(open(IN / "period.json")) if (IN / "period.json").exists() else {}
    late = sum(1 for m in mg if m["effective_from"] > rd.get("report_date", "9999"))
    check(late > 0, f"some mergers are effective after the report date ({late}), never in force")
    accs = list(csv.DictReader((IN / "accounts.csv").open()))
    check("account_local_id" in accs[0] and "book_code" in accs[0], "accounts are book-scoped local ids")
    fp = list(csv.DictReader((IN / "allocation_priority.csv").open()))
    fin_rows = list(csv.DictReader((OUT / "financing.csv").open())) if (OUT / "financing.csv").exists() else []
    check(len(fp) > 5 * max(1, len(fin_rows)), f"allocation roster ({len(fp)}) is far larger than the eligible set ({len(fin_rows)})")

# ---- 7b. the policy's stated fallbacks are honoured by the reference --------
# The golden is built from the reference, so a reference that disagrees with the
# policy makes the documented rule the WRONG answer. Check behaviour, not intent.
check('"UNASSIGNED"' in ref_src, "reference emits the policy's netting fallback (UNASSIGNED)")
if (OUT / "netting_exposure.csv").exists():
    vals = {r[1] for r in list(csv.reader((OUT / "netting_exposure.csv").open()))[1:]}
    check(not any(v.startswith("acct::") for v in vals),
          "no raw account ref leaks into netting_set (the documented fallback is live)")
    check("UNASSIGNED" in vals, "the UNASSIGNED fallback actually fires in the period")
check("period:" in pol_text and "period.json" in pol_text,
      "policy names period.json as the source of the reporting dates")

# ---- 8. images: nothing reaches the agent that hints, nothing missing at scoring
env_df = (T / "environment/Dockerfile").read_text()
check("sqlite3" not in env_df and "procps" not in env_df, "environment image installs no sanctioned-approach tools")
check("rm -f /root/generate_inputs.py" in env_df, "the generator is deleted from the environment image")
check("chmod -R a-w /app/inputs" in env_df, "the period is read-only to the agent")
tests_df = (T / "tests/Dockerfile").read_text()
check("chmod 0700" in tests_df and "chmod 0600" in tests_df, "the golden is root-only in the verifier image")
check("rm -rf /tests/verifier_env" in tests_df, "the reference does not survive into the scoring image")
check("PyYAML" in tests_df, "the verifier image carries what the environment image offers (PyYAML)")
check("chmod 700 /logs/verifier" in (T / "tests/test.sh").read_text(), "the reward channel is root-only")
check("nobody_uid" in ver_src, "peak memory is charged per uid")
if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
    def bins(image):
        r = subprocess.run(["docker", "run", "--rm", "--entrypoint", "sh", image, "-c",
                            "ls /usr/bin /bin 2>/dev/null | sort -u"], capture_output=True, text=True)
        return set(r.stdout.split()) if r.returncode == 0 else None
    e, v = bins("dpr-env:latest"), bins("dpr-tests:latest")
    if e and v:
        gap = sorted(e - v)
        check(not gap, f"every tool in the environment image is in the verifier image ({len(e)})")
        if gap:
            bad.append(f"   missing at scoring time: {gap}")
    else:
        note.append("images not built; parity not checked")

# ---- 9. sync ----------------------------------------------------------------
for a, b in (("tests/verifier_env/reconcile_ref.py", "solution/files/reconcile.py"),
             ("tests/verifier_env/generate_inputs.py", "environment/data/generate_inputs.py"),
             ("tests/verifier_env/reporting_policy.yaml", "environment/data/reporting_policy.yaml")):
    check((T / a).read_text() == (T / b).read_text(), f"{a} in sync with {b}")

print("\n".join(f"  ok   {m}" for m in note))
print()
if bad:
    print("\n".join(f"  FAIL {m}" for m in bad))
    print(f"\n{len(note)} checks passed, {len(bad)} FAILED")
    raise SystemExit(1)
print(f"  all {len(note)} reconciliation checks passed")
