#!/usr/bin/env python3
"""Clearing reconciliation.

Reads a clearing period (venue fills plus reference data) and writes one CSV
per report defined in the reporting policy.

    python3 reconcile.py <input_dir> --policy <policy.yaml> --output <dir>
                         [--seed N] [--max-memory 128MB]

Design notes
------------
* Every path is taken from the arguments / the policy; nothing is hard coded.
* All arithmetic is exact.  Money is carried as an integer scaled by 10**dp
  (cents) so that sums never lose precision; ratios that are not exactly
  representable are carried as ``Fraction`` and rounded only at the end.
* Reference tables are streamed into compact indexes and report rows are
  flushed to disk as soon as a sort block is complete, which keeps peak
  resident memory a long way inside the budget.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as _dt
import hashlib
import json
import os
import re
import sys
from collections import defaultdict, deque
from decimal import Decimal
from fractions import Fraction

# --------------------------------------------------------------------------
# policy loading -- PyYAML when present, otherwise a small subset reader
# --------------------------------------------------------------------------


def _scalar(text):
    text = text.strip()
    if not text:
        return None
    if len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0]:
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        body = text[1:-1].strip()
        return [_scalar(p) for p in body.split(",")] if body else []
    low = text.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _strip_comment(line):
    out = []
    quote = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def _parse_yaml(text):
    lines = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))
    pos = [0]

    def block(indent):
        if pos[0] < len(lines) and lines[pos[0]][1].startswith("- "):
            seq = []
            while pos[0] < len(lines):
                ind, content = lines[pos[0]]
                if ind < indent or not content.startswith("- "):
                    break
                pos[0] += 1
                seq.append(_scalar(content[2:]))
            return seq
        mapping = {}
        while pos[0] < len(lines):
            ind, content = lines[pos[0]]
            if ind < indent:
                break
            pos[0] += 1
            key, _, rest = content.partition(":")
            key, rest = key.strip(), rest.strip()
            if rest:
                mapping[key] = _scalar(rest)
            elif pos[0] < len(lines) and lines[pos[0]][0] > ind:
                mapping[key] = block(lines[pos[0]][0])
            else:
                mapping[key] = {}
        return mapping

    return block(lines[0][0] if lines else 0)


def load_policy(path):
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _parse_yaml(text)


# --------------------------------------------------------------------------
# exact arithmetic
# --------------------------------------------------------------------------

ZERO = Fraction(0)
ONE = Fraction(1)


def frac(value):
    """Exact Fraction from a CSV numeric string."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    text = str(value).strip()
    if not text:
        return ZERO
    return Fraction(Decimal(text))


def round_half_even(value, dp):
    """Round an exact Fraction to `dp` places, ties to even -> scaled int."""
    num = value.numerator * (10 ** dp)
    den = value.denominator
    q, r = divmod(num, den)  # floor division, 0 <= r < den
    twice = 2 * r
    if twice > den or (twice == den and q % 2 != 0):
        q += 1
    return q


def round_away(value, dp):
    """Round an exact Fraction to `dp` places, away from zero -> scaled int."""
    num = value.numerator * (10 ** dp)
    den = value.denominator
    negative = num < 0
    q, r = divmod(abs(num), den)
    if r:
        q += 1
    return -q if negative else q


ROUNDERS = {"half_even": round_half_even, "up": round_away}


def as_int(value):
    if isinstance(value, int):
        return value
    value = frac(value)
    if value.denominator == 1:
        return value.numerator
    return round_half_even(value, 0)


def apply_bps(scaled_value, bps, in_dp, out_dp, rounder):
    """``value * bps / 10000`` where the input is scaled by ``10**in_dp``.

    The result is returned scaled by ``10**out_dp``.
    """
    return rounder(Fraction(scaled_value * bps, 10000 * (10 ** in_dp)), out_dp)


def rescale(scaled_value, from_dp, to_dp):
    if from_dp == to_dp:
        return scaled_value
    if to_dp > from_dp:
        return scaled_value * (10 ** (to_dp - from_dp))
    return round_half_even(Fraction(scaled_value, 10 ** (from_dp - to_dp)), 0)


def fmt_scaled(scaled, dp):
    """Fixed point text: leading minus only when non zero, no separators."""
    if scaled == 0:
        return "0." + "0" * dp if dp else "0"
    sign = "-" if scaled < 0 else ""
    whole, part = divmod(abs(scaled), 10 ** dp)
    if dp == 0:
        return sign + str(whole)
    return "%s%d.%0*d" % (sign, whole, dp, part)


# --------------------------------------------------------------------------
# effective dated lookups
# --------------------------------------------------------------------------


class Timeline:
    """Records of the form (effective_from -> value), queried at a date."""

    __slots__ = ("dates", "values")

    def __init__(self, pairs):
        pairs.sort(key=lambda p: p[0])
        self.dates = [p[0] for p in pairs]
        self.values = [p[1] for p in pairs]

    def at(self, date, default=None, fallback_first=False):
        i = bisect.bisect_right(self.dates, date)
        if i:
            return self.values[i - 1]
        if fallback_first and self.values:
            return self.values[0]
        return default


def timelines(pairs_by_key):
    return {key: Timeline(pairs) for key, pairs in pairs_by_key.items()}


# --------------------------------------------------------------------------
# csv input
# --------------------------------------------------------------------------


def iter_csv(path):
    """Stream a CSV as dicts of stripped strings (missing file -> nothing)."""
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = [h.strip() for h in next(reader)]
        except StopIteration:
            return
        width = len(header)
        for record in reader:
            if not record:
                continue
            row = {}
            for i in range(width):
                row[header[i]] = record[i].strip() if i < len(record) else ""
            yield row


def fill_paths(input_dir):
    """Fills live either in a `fills/` directory of parts or a single csv."""
    paths = []
    folder = os.path.join(input_dir, "fills")
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".csv"):
                paths.append(os.path.join(folder, name))
    single = os.path.join(input_dir, "fills.csv")
    if os.path.exists(single):
        paths.append(single)
    return paths


# --------------------------------------------------------------------------
# account identity resolution
# --------------------------------------------------------------------------

CANON_RE = re.compile(r"^acct::[^:]*::.+$")
VENUE_CODE_RE = re.compile(r"^[^:]+:acct:[^:]*:.+$")


class AccountResolver:
    """Maps every reference a venue uses onto one clearing entity.

    Three alias mechanisms are composed:

    1. venue local codes -> canonical account_ref (effective dated),
    2. account mergers   -> the surviving account_ref (chased transitively),
    3. account links     -> cross book equivalence (transitive closure).

    ``account_ref`` (the surviving canonical reference) is what account keyed
    reference data such as netting sets and financing caps is looked up by;
    ``entity_key`` is the identity that the reported entity token stands for.
    """

    def __init__(self, venue_map_path, merger_path, link_path):
        by_venue_code = defaultdict(list)
        by_code = defaultdict(list)
        for row in iter_csv(venue_map_path):
            code = row.get("venue_code")
            ref = row.get("account_ref")
            if not code or not ref:
                continue
            eff = row.get("effective_from") or ""
            ref = sys.intern(ref)
            by_venue_code[(sys.intern(row.get("venue") or ""), code)].append((eff, ref))
            by_code[code].append((eff, ref))
        self.by_venue_code = timelines(by_venue_code)
        self.by_code = timelines(by_code)
        del by_venue_code, by_code

        # ---- mergers: source account_ref -> surviving account_ref
        merger_rows = []
        for row in iter_csv(merger_path):
            handle = row.get("venue_handle") or ""
            target = row.get("merged_account_ref") or ""
            if handle and target:
                merger_rows.append((row.get("effective_from") or "",
                                    row.get("merge_id") or "",
                                    handle, sys.intern(target)))
        merger_rows.sort()
        self.merge_to = {}
        for eff, _merge_id, handle, target in merger_rows:
            source = self.code_to_ref(handle, eff)
            if source and source != target:
                self.merge_to[source] = target  # the latest merger wins
        del merger_rows
        self._survivor = {}

        # ---- links: undirected equivalence over surviving references
        self.parent = {}
        for row in iter_csv(link_path):
            a, b = row.get("account_a"), row.get("account_b")
            if a and b:
                self.union(self.survivor(sys.intern(a)), self.survivor(sys.intern(b)))
        for source, target in self.merge_to.items():
            self.union(self.survivor(source), self.survivor(target))

    # -- venue codes ---------------------------------------------------------
    def code_to_ref(self, code, date):
        tl = self.by_venue_code.get((code.split(":", 1)[0], code))
        if tl is None:
            tl = self.by_code.get(code)
        if tl is None:
            return None
        return tl.at(date, fallback_first=True)

    def normalise(self, account_ref, venue, clearing_scope, trade_date):
        """A venue local reference -> the canonical book scoped account_ref."""
        ref = (account_ref or "").strip()
        if CANON_RE.match(ref):
            return ref
        if VENUE_CODE_RE.match(ref):
            return self.code_to_ref(ref, trade_date) or ref
        scope = (clearing_scope or "").strip()
        book = scope.split("::")[-1] if scope else ""
        return "acct::%s::%s" % (book, ref) if book else ref

    # -- merger chains -------------------------------------------------------
    def survivor(self, ref):
        found = self._survivor.get(ref)
        if found is not None:
            return found
        chain = []
        current = ref
        guard = set()
        while current in self.merge_to and current not in guard:
            guard.add(current)
            chain.append(current)
            current = self.merge_to[current]
        for node in chain:
            self._survivor[node] = current
        self._survivor[ref] = current
        return current

    # -- union find ----------------------------------------------------------
    def find(self, x):
        parent = self.parent
        if x not in parent:
            parent[x] = x
            return x
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if rb < ra:  # keep the lexicographically smallest reference as root
            ra, rb = rb, ra
        self.parent[rb] = ra

    def entity_key(self, account_ref):
        """Seed independent canonical identity of the clearing entity."""
        return self.find(account_ref)


# --------------------------------------------------------------------------
# entity tokens
# --------------------------------------------------------------------------

ALPHABETS = {
    "hex_lower": "0123456789abcdef",
    "hex_upper": "0123456789ABCDEF",
    "base36": "0123456789abcdefghijklmnopqrstuvwxyz",
    "alnum_lower": "0123456789abcdefghijklmnopqrstuvwxyz",
}


def mint_tokens(entity_keys, seed, cfg):
    """Deterministic, collision free entity tokens.

    Tokens are minted from the seed alone and then handed out in canonical
    entity order.  Because the assignment is order preserving, every report
    stays sorted ascending on its token column while a change of seed changes
    the tokens and nothing else.
    """
    prefix = str(cfg.get("prefix", "ent_") or "")
    length = int(cfg.get("length", 12) or 12)
    alphabet = ALPHABETS.get(str(cfg.get("alphabet", "hex_lower")),
                             ALPHABETS["hex_lower"])
    if cfg.get("length_counts_prefix"):
        length -= len(prefix)
    length = max(length, 1)
    base = len(alphabet)

    ordered = sorted(set(entity_keys))
    minted, used = [], set()
    for index in range(len(ordered)):
        nonce = 0
        while True:
            digest = hashlib.sha256(
                ("%s|entity|%d|%d" % (seed, index, nonce)).encode("utf-8")).digest()
            value = int.from_bytes(digest, "big")
            chars = []
            for _ in range(length):
                value, rem = divmod(value, base)
                chars.append(alphabet[rem])
            token = "".join(reversed(chars))
            if token not in used:
                used.add(token)
                minted.append(token)
                break
            nonce += 1
    minted.sort()
    return {key: sys.intern(prefix + token)
            for key, token in zip(ordered, minted)}


# --------------------------------------------------------------------------
# report emission
# --------------------------------------------------------------------------


class ReportWriter:
    """Writes one report; rows are tuples in policy column order."""

    def __init__(self, path, columns, sort_keys, block_key=None):
        self.fh = open(path, "w", encoding="utf-8", newline="")
        self.writer = csv.writer(self.fh, lineterminator="\n")
        self.writer.writerow(columns)
        self.index = [columns.index(k) for k in sort_keys if k in columns]
        # When the leading sort key is the block key the caller feeds blocks in
        # ascending order, so each block can be sorted and flushed on its own.
        self.streaming = (bool(block_key) and bool(sort_keys)
                          and sort_keys[0] == block_key)
        self.buffer = None if self.streaming else []

    def _key(self, row):
        return tuple(row[i] for i in self.index)

    def add_block(self, rows):
        if self.streaming:
            rows.sort(key=self._key)
            self.writer.writerows(rows)
        else:
            self.buffer.extend(rows)

    def close(self):
        if self.buffer is not None:
            self.buffer.sort(key=self._key)
            self.writer.writerows(self.buffer)
            self.buffer = None
        self.fh.close()


# --------------------------------------------------------------------------
# fills
# --------------------------------------------------------------------------


class Fill(object):
    __slots__ = (
        "fill_id", "trade_date", "venue", "symbol", "raw_lei", "buy", "quantity",
        "account_ref", "entity", "instrument_id", "multiplier", "settlement_asset",
        "unit_cost", "notional", "fee_usd", "commission", "rebate",
    )


def parse_memory(text):
    """Understands 128MB / 128MiB / 134217728 and returns bytes."""
    if not text:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?)i?B?\s*", str(text), re.I)
    if not m:
        return None
    scale = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
    return int(float(m.group(1)) * scale[m.group(2).upper()])


def peak_rss_bytes():
    """Peak resident set size of this process tree, in bytes."""
    try:
        import resource
    except ImportError:
        return None
    unit = 1 if sys.platform == "darwin" else 1024  # Linux reports kilobytes
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak += resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return peak * unit


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description="Desk clearing reconciliation")
    ap.add_argument("input_dir")
    ap.add_argument("--policy", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--seed", default="0")
    ap.add_argument("--max-memory", default=None)
    args = ap.parse_args(argv)

    policy = load_policy(args.policy)
    inp = args.input_dir
    os.makedirs(args.output, exist_ok=True)

    with open(os.path.join(inp, "period.json"), "r", encoding="utf-8") as fh:
        period = json.load(fh)
    period_start = period["period_start"]
    report_date = period["report_date"]
    snapshots = sorted(period.get("snapshot_dates") or [])

    def section(name):
        node = policy.get(name)
        return node if isinstance(node, dict) else {}

    def source(name, default):
        value = section(name).get("source", default)
        if isinstance(value, list):
            return [os.path.join(inp, v) for v in value]
        return os.path.join(inp, value)

    # ---------------- identity ----------------
    resolver = AccountResolver(
        os.path.join(inp, "venue_account_map.csv"),
        os.path.join(inp, "account_mergers.csv"),
        os.path.join(inp, "account_links.csv"))

    # ---------------- reference data ----------------
    buckets = defaultdict(list)
    for row in iter_csv(source("fx", "fx_rates.csv")):
        buckets[sys.intern(row["asset"])].append(
            (row["utc_date"], frac(row["rate_usd"])))
    fx = timelines(buckets)

    buckets = defaultdict(list)
    symbol_index = defaultdict(list)
    for row in iter_csv(os.path.join(inp, "instrument_symbols.csv")):
        instrument_id = sys.intern(row["instrument_id"])
        symbol = sys.intern(row["symbol"])
        buckets[instrument_id].append((row["effective_from"], symbol))
        symbol_index[symbol].append((row["effective_from"], instrument_id))
    sym_tl = timelines(buckets)
    for key in symbol_index:
        symbol_index[key].sort()

    buckets = defaultdict(list)
    for row in iter_csv(os.path.join(inp, "contract_revisions.csv")):
        buckets[sys.intern(row["instrument_id"])].append(
            (row["effective_from"], (as_int(row["contract_multiplier"]),
                                     sys.intern(row["settlement_asset"]))))
    rev_tl = timelines(buckets)

    corp = defaultdict(list)
    for row in iter_csv(source("corporate_actions", "corporate_actions.csv")):
        corp[sys.intern(row["instrument_id"])].append(
            (row["action_date"], frac(row["split_ratio"])))
    for key in corp:
        corp[key].sort()

    reassign = {}
    for row in iter_csv(os.path.join(inp, "counterparty_reassignments.csv")):
        reassign.setdefault(sys.intern(row["old_lei"]), []).append(
            (row["effective_from"], sys.intern(row["new_lei"])))
    for key in reassign:
        reassign[key].sort()

    versions = defaultdict(lambda: defaultdict(list))
    for row in iter_csv(source("commission", "fee_schedule.csv")):
        versions[sys.intern(row["venue"])][row["effective_from"]].append(
            (frac(row["min_notional_usd"]), as_int(row["commission_bps"])))
    fee_schedule = {venue: Timeline([(eff, sorted(bands))
                                     for eff, bands in by_eff.items()])
                    for venue, by_eff in versions.items()}
    del versions

    rebate_tiers = defaultdict(list)
    for row in iter_csv(source("rebate", "rebate_tiers.csv")):
        rebate_tiers[sys.intern(row["venue"])].append(
            (frac(row["min_trailing_usd"]), as_int(row["rebate_bps"])))
    for venue in rebate_tiers:
        rebate_tiers[venue].sort()

    buckets = defaultdict(list)
    for row in iter_csv(source("haircut", "haircuts.csv")):
        buckets[sys.intern(row["asset"])].append(
            (row["effective_from"], as_int(row["haircut_bps"])))
    haircut_tl = timelines(buckets)

    buckets = defaultdict(list)
    for row in iter_csv(source("initial_margin", "margin_rates.csv")):
        buckets[sys.intern(row["instrument_id"])].append(
            (row["effective_from"], as_int(row["initial_margin_bps"])))
    margin_tl = timelines(buckets)

    buckets = defaultdict(list)
    for row in iter_csv(source("netting", "netting_sets.csv")):
        buckets[sys.intern(row["account_ref"])].append(
            (row["effective_from"], sys.intern(row["netting_set"])))
    netting_tl = timelines(buckets)
    netting_missing = sys.intern(str(section("netting").get("missing")
                                     or "UNASSIGNED"))
    del buckets

    interest_tl = Timeline(
        [(row["effective_from"], as_int(row["debit_rate_bps"]))
         for row in iter_csv(source("interest", "interest_rates.csv"))])

    fin_sources = source("financing",
                         ["financing_pool.csv", "allocation_priority.csv"])
    if not isinstance(fin_sources, list):
        fin_sources = [fin_sources]
    pool_cents = 0
    for row in list(iter_csv(fin_sources[0]))[:1]:
        pool_cents = round_half_even(frac(row["pool_usd"]), 2)
    allocation = {}
    if len(fin_sources) > 1:
        for row in iter_csv(fin_sources[1]):
            allocation[sys.intern(row["account_ref"])] = (
                row["priority"], round_half_even(frac(row["cap_usd"]), 2))

    comm_missing = as_int(section("commission").get("missing", 0) or 0)
    haircut_missing = as_int(section("haircut").get("missing", 0) or 0)
    margin_missing = as_int(section("initial_margin").get("missing", 0) or 0)

    # ---------------- derived lookups ----------------
    def instrument_for(symbol, date):
        """The instrument quoted under `symbol` on `date`."""
        candidates = symbol_index.get(symbol) or ()
        best = None
        for eff, instrument_id in candidates:
            if eff <= date and sym_tl[instrument_id].at(date) == symbol:
                best = instrument_id
        if best is None:
            for eff, instrument_id in candidates:
                if eff <= date:
                    best = instrument_id
        if best is None and candidates:
            best = candidates[0][1]
        return best if best is not None else symbol

    def split_factor(instrument_id, date):
        factor = ONE
        for action_date, ratio in corp.get(instrument_id, ()):
            if action_date <= date:
                factor *= ratio
        return factor

    factor_cache = {}

    def restate(instrument_id, quantity, trade_date, as_of):
        """A quantity booked on `trade_date` restated into `as_of` units."""
        key = (instrument_id, trade_date, as_of)
        ratio = factor_cache.get(key)
        if ratio is None:
            ratio = (split_factor(instrument_id, as_of)
                     / split_factor(instrument_id, trade_date))
            factor_cache[key] = ratio
        return as_int(quantity * ratio)

    lei_cache = {}

    def resolve_lei(lei, date):
        """Follow counterparty reassignments in force on `date`."""
        key = (lei, date)
        found = lei_cache.get(key)
        if found is not None:
            return found
        current = lei
        guard = set()
        while current in reassign and current not in guard:
            guard.add(current)
            successor = None
            for eff, new_lei in reassign[current]:
                if eff <= date:
                    successor = new_lei
            if successor is None:
                break
            current = successor
        lei_cache[key] = current
        return current

    # ---------------- figure rounding, from the policy ----------------
    figures = policy.get("figures") or {}

    def rounding_for(name, default_mode="half_even", default_dp=2):
        spec = figures.get(name) or {}
        return (ROUNDERS.get(str(spec.get("round", default_mode)), round_half_even),
                int(spec.get("dp", default_dp)))

    round_notional, dp_notional = rounding_for("notional_usd")
    round_fee, dp_fee = rounding_for("fee_usd", "up")
    round_comm, dp_comm = rounding_for("commission_usd", "up")
    round_reb, dp_reb = rounding_for("rebate_usd", "up")
    round_hair, dp_hair = rounding_for("haircut_usd")
    round_margin, dp_margin = rounding_for("initial_margin_usd")

    # ---------------- load and price the fills ----------------
    fills = []
    for path in fill_paths(inp):
        for row in iter_csv(path):
            f = Fill()
            f.fill_id = row["fill_id"]
            f.trade_date = sys.intern(row["trade_date"])
            f.venue = sys.intern(row["venue"])
            f.symbol = sys.intern(row["symbol"])
            f.raw_lei = sys.intern(row["counterparty_lei"])
            f.buy = (row["side"] or "").strip().upper() == "BUY"
            f.quantity = as_int(row["quantity"])

            account_ref = resolver.normalise(
                row["account_ref"], f.venue, row.get("clearing_scope", ""),
                f.trade_date)
            f.account_ref = resolver.survivor(account_ref)
            f.entity = resolver.entity_key(f.account_ref)

            instrument_id = instrument_for(f.symbol, f.trade_date)
            f.instrument_id = instrument_id
            revision = rev_tl.get(instrument_id)
            multiplier, asset = 1, "USD"
            if revision is not None:
                got = revision.at(f.trade_date, fallback_first=True)
                if got:
                    multiplier, asset = got
            f.multiplier = multiplier
            f.settlement_asset = asset

            rate_tl = fx.get(asset)
            rate = ONE
            if rate_tl is not None:
                got = rate_tl.at(f.trade_date, fallback_first=True)
                if got is not None:
                    rate = got

            price = frac(row["price"])
            signed = f.quantity if f.buy else -f.quantity
            f.notional = round_notional(
                Fraction(signed * multiplier) * price * rate, dp_notional)
            f.fee_usd = round_fee(frac(row["fee"]) * rate, dp_fee)
            f.unit_cost = price * rate
            fills.append(f)

    # ---- commission: per fill, banded on |notional|
    for f in fills:
        schedule = fee_schedule.get(f.venue)
        bands = schedule.at(f.trade_date) if schedule is not None else None
        bps = comm_missing
        if bands:
            abs_notional = Fraction(abs(f.notional), 10 ** dp_notional)
            for min_notional, band_bps in bands:
                if min_notional <= abs_notional:
                    bps = band_bps
        f.commission = apply_bps(abs(f.notional), bps, dp_notional, dp_comm,
                                 round_comm)

    # ---- rebate: tiered on the desk's trailing traded volume per venue
    rebate_cfg = section("rebate")
    window_days = int(rebate_cfg.get("window_days", 30) or 30)
    include_trade_date = bool(rebate_cfg.get("window_includes_trade_date", False))
    venue_day = defaultdict(lambda: defaultdict(int))
    for f in fills:
        day = _dt.date.fromisoformat(f.trade_date).toordinal()
        venue_day[f.venue][day] += abs(f.notional)
    basis_cache = {}
    for f in fills:
        day = _dt.date.fromisoformat(f.trade_date).toordinal()
        key = (f.venue, day)
        basis = basis_cache.get(key)
        if basis is None:
            table = venue_day[f.venue]
            last = day if include_trade_date else day - 1
            basis = 0
            for d in range(last - window_days + 1, last + 1):
                basis += table.get(d, 0)
            basis_cache[key] = basis
        basis_usd = Fraction(basis, 10 ** dp_notional)
        bps = 0
        for min_trailing, tier_bps in rebate_tiers.get(f.venue, ()):
            if min_trailing <= basis_usd:
                bps = tier_bps
        f.rebate = apply_bps(abs(f.notional), bps, dp_notional, dp_reb, round_reb)
    del venue_day, basis_cache

    # ---------------- entity tokens ----------------
    tokens = mint_tokens({f.entity for f in fills}, args.seed,
                         section("entity_tokens"))

    # ---------------- writers ----------------
    reports = policy.get("reports") or {}

    def columns_of(name):
        return list(((reports.get(name) or {}).get("columns") or {}).keys())

    def sort_of(name):
        keys = (reports.get(name) or {}).get("sort") or []
        return [keys] if isinstance(keys, str) else list(keys)

    writers = {}
    for name in reports:
        writers[name] = ReportWriter(os.path.join(args.output, name),
                                     columns_of(name), sort_of(name),
                                     block_key="as_of")

    def emit(name, rows):
        writer = writers.get(name)
        if writer is not None:
            writer.add_block(rows)

    # ---------------- attribution.csv ----------------
    rows = []
    for f in fills:
        tl = sym_tl.get(f.instrument_id)
        symbol = tl.at(f.trade_date, fallback_first=True) if tl is not None else f.symbol
        rows.append((f.fill_id, tokens[f.entity], symbol,
                     resolve_lei(f.raw_lei, f.trade_date),
                     fmt_scaled(f.notional, dp_notional)))
    emit("attribution.csv", rows)
    del rows

    # ---------------- snapshot reports ----------------
    for as_of in snapshots:
        positions = defaultdict(lambda: [0, 0])
        fees = defaultdict(lambda: [0, 0, 0])
        exposure = defaultdict(lambda: [0, 0])
        cp_exposure = defaultdict(lambda: [0, 0])
        net_sets = defaultdict(lambda: [0, 0])
        venues = defaultdict(lambda: [0, 0, 0, 0])
        margin = defaultdict(lambda: defaultdict(int))

        for f in fills:
            if f.trade_date > as_of:
                continue
            token = tokens[f.entity]
            notional = f.notional
            gross = abs(notional)

            tl = sym_tl.get(f.instrument_id)
            symbol = tl.at(as_of, fallback_first=True) if tl is not None else f.symbol
            quantity = restate(f.instrument_id, f.quantity, f.trade_date, as_of)
            bucket = positions[(token, symbol)]
            bucket[0] += quantity if f.buy else -quantity
            bucket[1] += abs(quantity)

            bucket = fees[(token, f.venue)]
            bucket[0] += f.fee_usd
            bucket[1] += f.commission
            bucket[2] += f.rebate

            bucket = exposure[(token, f.settlement_asset)]
            bucket[0] += notional
            bucket[1] += gross

            bucket = cp_exposure[(resolve_lei(f.raw_lei, as_of), f.settlement_asset)]
            bucket[0] += notional
            bucket[1] += gross

            tl = netting_tl.get(f.account_ref)
            netting_set = (tl.at(as_of) if tl is not None else None) or netting_missing
            bucket = net_sets[(netting_set, f.settlement_asset)]
            bucket[0] += notional
            bucket[1] += gross

            bucket = venues[f.venue]
            bucket[0] += 1
            bucket[1] += gross
            bucket[2] += f.fee_usd
            bucket[3] += f.commission

            margin[token][f.instrument_id] += gross

        emit("positions.csv", [
            (as_of, token, symbol, str(net_q), str(gross_q))
            for (token, symbol), (net_q, gross_q) in positions.items()])

        emit("fees.csv", [
            (as_of, token, venue, fmt_scaled(fee, dp_fee),
             fmt_scaled(commission, dp_comm), fmt_scaled(rebate, dp_reb))
            for (token, venue), (fee, commission, rebate) in fees.items()])

        rows = []
        for (token, asset), (net_v, gross_v) in exposure.items():
            tl = haircut_tl.get(asset)
            bps = tl.at(as_of) if tl is not None else None
            if bps is None:
                bps = haircut_missing
            haircut = apply_bps(gross_v, bps, dp_notional, dp_hair, round_hair)
            after = net_v - rescale(haircut, dp_hair, dp_notional)
            rows.append((as_of, token, asset,
                         fmt_scaled(net_v, dp_notional),
                         fmt_scaled(gross_v, dp_notional),
                         fmt_scaled(haircut, dp_hair),
                         fmt_scaled(after, dp_notional)))
        emit("exposure.csv", rows)

        emit("counterparty_exposure.csv", [
            (as_of, lei, asset, fmt_scaled(net_v, dp_notional),
             fmt_scaled(gross_v, dp_notional))
            for (lei, asset), (net_v, gross_v) in cp_exposure.items()])

        emit("netting_exposure.csv", [
            (as_of, netting_set, asset, fmt_scaled(net_v, dp_notional),
             fmt_scaled(gross_v, dp_notional))
            for (netting_set, asset), (net_v, gross_v) in net_sets.items()])

        emit("venue_summary.csv", [
            (as_of, venue, str(count), fmt_scaled(gross_v, dp_notional),
             fmt_scaled(fee, dp_fee), fmt_scaled(commission, dp_comm))
            for venue, (count, gross_v, fee, commission) in venues.items()])

        rows = []
        for token, per_instrument in margin.items():
            total = 0
            for instrument_id, gross_v in per_instrument.items():
                tl = margin_tl.get(instrument_id)
                bps = tl.at(as_of) if tl is not None else None
                if bps is None:
                    bps = margin_missing
                total += apply_bps(gross_v, bps, dp_notional, dp_margin, round_margin)
            rows.append((as_of, token, fmt_scaled(total, dp_margin)))
        emit("margin.csv", rows)
        del positions, fees, exposure, cp_exposure, net_sets, venues, margin, rows

    # ---------------- lots.csv ----------------
    emit("lots.csv", build_lots(fills, tokens, corp, report_date, policy))

    # ---------------- financing.csv ----------------
    entries = []
    for ref in sorted({f.account_ref for f in fills}):
        priority, cap = allocation.get(ref, ("", 0))
        try:
            order = (0, as_int(priority))
        except Exception:
            order = (1, 0)
        entries.append((order, ref, priority, cap))
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    remaining, running, rows = pool_cents, 0, []
    for _order, ref, priority, cap in entries:
        take = max(min(cap, remaining), 0)
        remaining -= take
        running += take
        rows.append((ref, str(priority), fmt_scaled(cap, 2),
                     fmt_scaled(take, 2), fmt_scaled(running, 2)))
    emit("financing.csv", rows)
    del entries, rows

    # ---------------- interest.csv ----------------
    emit("interest.csv", build_interest(fills, tokens, interest_tl,
                                        period_start, report_date, policy))

    for writer in writers.values():
        writer.close()

    budget = parse_memory(args.max_memory)
    peak = peak_rss_bytes()
    if budget and peak and peak > budget:
        sys.stderr.write(
            "warning: peak resident memory %d bytes exceeded the %d byte budget\n"
            % (peak, budget))
    return 0


# --------------------------------------------------------------------------
# lot engine: weighted average alongside fifo
# --------------------------------------------------------------------------


def build_lots(fills, tokens, corp, report_date, policy):
    lots_cfg = policy.get("lots") or {}
    realise_dp = 2
    m = re.search(r"dp\s*(\d+)", str(lots_cfg.get("realise_round", "")))
    if m:
        realise_dp = int(m.group(1))

    groups = defaultdict(list)
    for f in fills:
        groups[(tokens[f.entity], f.instrument_id)].append(f)

    out = []
    for (token, instrument_id), items in groups.items():
        items.sort(key=lambda f: (f.trade_date, f.fill_id))
        actions = [a for a in corp.get(instrument_id, ()) if a[0] <= report_date]

        position = 0        # signed, in the units current at the time
        avg_cost = ZERO     # per unit, USD, carried exactly
        realised = 0        # scaled integer
        lots = deque()      # fifo queue of [signed_quantity, unit_cost]
        fifo_position = 0
        fifo_realised = 0
        pending = 0

        def apply_actions(upto):
            nonlocal position, avg_cost, pending, fifo_position
            while pending < len(actions) and actions[pending][0] <= upto:
                ratio = actions[pending][1]
                if ratio and ratio != 1:
                    position = as_int(position * ratio)
                    avg_cost = avg_cost / ratio
                    fifo_position = 0
                    for lot in lots:
                        lot[0] = as_int(lot[0] * ratio)
                        lot[1] = lot[1] / ratio
                        fifo_position += lot[0]
                pending += 1

        for f in items:
            apply_actions(f.trade_date)
            unit_cost = f.unit_cost
            signed = f.quantity if f.buy else -f.quantity
            multiplier = f.multiplier

            # ---- weighted average
            if position == 0:
                position = signed
                avg_cost = unit_cost
            elif (position > 0) == (signed > 0):
                open_qty, add_qty = abs(position), abs(signed)
                avg_cost = ((avg_cost * open_qty + unit_cost * add_qty)
                            / (open_qty + add_qty))
                position += signed
            else:
                closed = min(abs(position), abs(signed))
                sign = 1 if position > 0 else -1
                realised += round_half_even(
                    (unit_cost - avg_cost) * closed * multiplier * sign, realise_dp)
                remainder = abs(signed) - abs(position)
                if remainder > 0:                    # crossed through zero
                    position = remainder if signed > 0 else -remainder
                    avg_cost = unit_cost
                else:
                    position += signed
                    if position == 0:
                        avg_cost = ZERO

            # ---- fifo
            if fifo_position == 0 or (fifo_position > 0) == (signed > 0):
                lots.append([signed, unit_cost])
                fifo_position += signed
            else:
                sign = 1 if fifo_position > 0 else -1
                need = abs(signed)
                pnl = ZERO
                while need > 0 and lots:
                    lot = lots[0]
                    have = abs(lot[0])
                    take = have if have <= need else need
                    pnl += (unit_cost - lot[1]) * take * multiplier * sign
                    if take == have:
                        lots.popleft()
                    else:
                        lot[0] -= take if lot[0] > 0 else -take
                    need -= take
                    fifo_position -= take * sign
                fifo_realised += round_half_even(pnl, realise_dp)
                if need > 0:                         # crossed through zero
                    opened = need if signed > 0 else -need
                    lots.append([opened, unit_cost])
                    fifo_position += opened

        apply_actions(report_date)

        out.append((token, instrument_id, str(position),
                    fmt_scaled(round_half_even(avg_cost, 6), 6),
                    fmt_scaled(realised, realise_dp),
                    fmt_scaled(fifo_realised, realise_dp)))
    return out


# --------------------------------------------------------------------------
# interest accrual
# --------------------------------------------------------------------------


def build_interest(fills, tokens, interest_tl, period_start, report_date, policy):
    cfg = policy.get("interest") or {}
    day_basis = 365
    m = re.search(r"/\s*(\d+)\s*per\b", str(cfg.get("accrue", "")))
    if m:
        day_basis = int(m.group(1))

    moves = defaultdict(dict)
    for f in fills:
        table = moves[tokens[f.entity]]
        move = -f.notional - f.fee_usd - f.commission + f.rebate
        table[f.trade_date] = table.get(f.trade_date, 0) + move

    start = _dt.date.fromisoformat(period_start)
    end = _dt.date.fromisoformat(report_date)
    days, rates = [], []
    day = start
    while day < end:                    # period_start .. report_date, exclusive
        text = day.isoformat()
        days.append(text)
        bps = interest_tl.at(text)
        rates.append(Fraction(bps, 10000 * day_basis) if bps else ZERO)
        day += _dt.timedelta(days=1)

    out = []
    for token in sorted(moves):
        balance = ZERO
        accrued = ZERO
        table = moves[token]
        for text, rate in zip(days, rates):
            move = table.get(text)
            if move:
                balance += Fraction(move, 100)
            if rate and balance < 0:
                interest = balance * rate
                accrued += interest
                balance += interest
        out.append((token,
                    fmt_scaled(round_half_even(balance, 2), 2),
                    fmt_scaled(round_half_even(accrued, 2), 2)))
    return out


if __name__ == "__main__":
    sys.exit(main())
