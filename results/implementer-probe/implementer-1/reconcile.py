#!/usr/bin/env python3
"""Clearing reconciliation.

Reads a clearing period directory and writes one CSV per report defined in the
reporting policy.  All figures are computed with exact integer/rational
arithmetic and rounded exactly once, where and how the policy says.

Usage:
    python3 reconcile.py <input_dir> --policy <policy.yaml> --output <dir>
                         [--seed N] [--max-memory 128MB]
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as _dt
import glob
import hashlib
import json
import os
import sys
from collections import defaultdict, deque
from decimal import Decimal
from fractions import Fraction

# --------------------------------------------------------------------------
# tiny YAML reader (the policy is a plain nested mapping; PyYAML when present)
# --------------------------------------------------------------------------


def _scalar(tok: str):
    tok = tok.strip()
    if tok.startswith('[') and tok.endswith(']'):
        inner = tok[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p) for p in inner.split(',')]
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"):
        return tok[1:-1]
    low = tok.lower()
    if low in ('true', 'yes'):
        return True
    if low in ('false', 'no'):
        return False
    if low in ('null', '~', ''):
        return None
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return tok


def _mini_yaml(text: str):
    """Parse the subset of YAML used by the reporting policy."""
    root: dict = {}
    # stack of (indent, container)
    stack = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith('- '):
            if not isinstance(parent, list):
                # convert the pending mapping slot into a list
                raise ValueError('unexpected sequence in policy')
            parent.append(_scalar(line[2:]))
            continue
        if ':' not in line:
            continue
        key, _, rest = line.partition(':')
        key = key.strip()
        rest = rest.strip()
        if rest == '':
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _scalar(rest)
    return root


def load_policy(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as fh:
        text = fh.read()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except Exception:
        return _mini_yaml(text)


# --------------------------------------------------------------------------
# exact arithmetic helpers
# --------------------------------------------------------------------------


def ratio_of(text: str):
    """Exact (numerator, denominator) of a decimal literal."""
    return Decimal(text).as_integer_ratio()


def round_ratio(num: int, den: int, mode: str) -> int:
    """Round num/den (den > 0) to an integer.

    mode 'half_even' -> ties to even, 'up' -> away from zero.
    """
    if den < 0:
        num, den = -num, -den
    neg = num < 0
    n = -num if neg else num
    q, r = divmod(n, den)
    if r:
        if mode == 'up':
            q += 1
        elif mode == 'half_even':
            twice = r << 1
            if twice > den or (twice == den and (q & 1)):
                q += 1
        else:  # pragma: no cover - defensive
            raise ValueError('unknown rounding mode %r' % mode)
    return -q if neg else q


def cents(text) -> int:
    """Exact cents of a decimal literal (half-even on sub-cent inputs)."""
    num, den = ratio_of(str(text))
    return round_ratio(num * 100, den, 'half_even')


def round_fraction(fr: Fraction, dp: int, mode: str) -> int:
    """Round a Fraction to a scaled integer with `dp` decimals."""
    return round_ratio(fr.numerator * (10 ** dp), fr.denominator, mode)


def fmt_scaled(value: int, dp: int) -> str:
    if value == 0:
        return '0.' + '0' * dp if dp else '0'
    neg = value < 0
    a = -value if neg else value
    scale = 10 ** dp
    whole, frac = divmod(a, scale)
    body = str(whole) + '.' + str(frac).rjust(dp, '0')
    return ('-' + body) if neg else body


def fmt_cents(value: int) -> str:
    return fmt_scaled(value, 2)


def fmt_micro(value: int) -> str:
    return fmt_scaled(value, 6)


# --------------------------------------------------------------------------
# effective-dated lookup helpers
# --------------------------------------------------------------------------


class Timeline:
    """Sorted (effective_from, value) pairs with as-of lookup."""

    __slots__ = ('dates', 'values')

    def __init__(self, pairs):
        pairs = sorted(pairs, key=lambda p: p[0])
        self.dates = [p[0] for p in pairs]
        self.values = [p[1] for p in pairs]

    def asof(self, date: str, default=None):
        i = bisect.bisect_right(self.dates, date) - 1
        if i < 0:
            return default
        return self.values[i]

    def first(self, default=None):
        return self.values[0] if self.values else default


def build_timelines(rows, key_fn, date_fn, value_fn):
    tmp = defaultdict(list)
    for row in rows:
        tmp[key_fn(row)].append((date_fn(row), value_fn(row)))
    return {k: Timeline(v) for k, v in tmp.items()}


def read_csv(path, required=True):
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(path)
        return
    with open(path, 'r', encoding='utf-8-sig', newline='') as fh:
        for row in csv.DictReader(fh):
            yield row


def read_csv_list(path, required=True):
    return list(read_csv(path, required))


# --------------------------------------------------------------------------
# reference data
# --------------------------------------------------------------------------


class Reference:
    def __init__(self, indir: str):
        j = os.path.join
        self.indir = indir

        with open(j(indir, 'period.json'), 'r', encoding='utf-8') as fh:
            period = json.load(fh)
        self.period_start = period['period_start']
        self.report_date = period['report_date']
        self.snapshots = list(period['snapshot_dates'])

        # venue local code -> account, effective dated
        self.venue_map = build_timelines(
            read_csv_list(j(indir, 'venue_account_map.csv')),
            lambda r: r['venue_code'], lambda r: r['effective_from'],
            lambda r: r['account_ref'])

        # mergers: the account behind the venue handle at the merger's
        # effective date is absorbed into merged_account_ref from that date.
        merger_pairs = defaultdict(list)
        for r in read_csv(j(indir, 'account_mergers.csv'), False):
            handle = r['venue_handle']
            eff = r['effective_from']
            if handle.startswith('acct::'):
                source = handle
            else:
                tl = self.venue_map.get(handle)
                source = tl.asof(eff) if tl is not None else None
            if source is None:
                continue                      # handle unknown at that date
            target = r['merged_account_ref']
            if target == source:
                continue
            merger_pairs[source].append((eff, target))
        self.mergers = {k: Timeline(v) for k, v in merger_pairs.items()}

        # cross-book equivalences
        parent: dict = {}

        def find(x):
            parent.setdefault(x, x)
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        for r in read_csv(j(indir, 'account_links.csv'), False):
            a, b = r['account_a'], r['account_b']
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        members = defaultdict(list)
        for node in list(parent):
            members[find(node)].append(node)
        self.entity_of_account = {}
        for root, group in members.items():
            rep = min(group)
            for node in group:
                self.entity_of_account[node] = rep

        # counterparty successor chain
        self.reassign = build_timelines(
            read_csv_list(j(indir, 'counterparty_reassignments.csv'), False),
            lambda r: r['old_lei'], lambda r: r['effective_from'],
            lambda r: r['new_lei'])

        # netting sets
        self.netting = build_timelines(
            read_csv_list(j(indir, 'netting_sets.csv'), False),
            lambda r: r['account_ref'], lambda r: r['effective_from'],
            lambda r: r['netting_set'])

        # instruments / symbols
        sym_rows = read_csv_list(j(indir, 'instrument_symbols.csv'))
        self.symbol_to_instrument = build_timelines(
            sym_rows, lambda r: r['symbol'], lambda r: r['effective_from'],
            lambda r: r['instrument_id'])
        self.instrument_symbol = build_timelines(
            sym_rows, lambda r: r['instrument_id'], lambda r: r['effective_from'],
            lambda r: r['symbol'])

        # contract revisions
        self.contract = build_timelines(
            read_csv_list(j(indir, 'contract_revisions.csv')),
            lambda r: r['instrument_id'], lambda r: r['effective_from'],
            lambda r: (int(r['contract_multiplier']), r['settlement_asset']))

        # fx (previous published)
        fx_tmp = defaultdict(list)
        for r in read_csv(j(indir, 'fx_rates.csv')):
            fx_tmp[r['asset']].append((r['utc_date'], ratio_of(r['rate_usd'])))
        self.fx = {k: Timeline(v) for k, v in fx_tmp.items()}

        # fee schedule: venue -> effective_from -> ordered bands
        fee_tmp = defaultdict(lambda: defaultdict(list))
        for r in read_csv(j(indir, 'fee_schedule.csv'), False):
            fee_tmp[r['venue']][r['effective_from']].append(
                (cents(r['min_notional_usd']), int(r['commission_bps'])))
        self.fee_schedule = {}
        for venue, by_eff in fee_tmp.items():
            pairs = []
            for eff, bands in by_eff.items():
                bands.sort(key=lambda b: b[0])
                pairs.append((eff, bands))
            self.fee_schedule[venue] = Timeline(pairs)

        # rebate tiers (static)
        reb_tmp = defaultdict(list)
        for r in read_csv(j(indir, 'rebate_tiers.csv'), False):
            reb_tmp[r['venue']].append(
                (cents(r['min_trailing_usd']), int(r['rebate_bps'])))
        self.rebate_tiers = {k: sorted(v) for k, v in reb_tmp.items()}

        # haircuts / margin / interest
        self.haircuts = build_timelines(
            read_csv_list(j(indir, 'haircuts.csv'), False),
            lambda r: r['asset'], lambda r: r['effective_from'],
            lambda r: int(r['haircut_bps']))
        self.margin = build_timelines(
            read_csv_list(j(indir, 'margin_rates.csv'), False),
            lambda r: r['instrument_id'], lambda r: r['effective_from'],
            lambda r: int(r['initial_margin_bps']))
        self.interest_rate = Timeline(
            [(r['effective_from'], int(r['debit_rate_bps']))
             for r in read_csv(j(indir, 'interest_rates.csv'), False)])

        # corporate actions: instrument -> sorted [(action_date, ratio)]
        ca = defaultdict(list)
        for r in read_csv(j(indir, 'corporate_actions.csv'), False):
            ca[r['instrument_id']].append((r['action_date'], int(r['split_ratio'])))
        self.corporate_actions = {k: sorted(v) for k, v in ca.items()}

        # financing
        self.allocation = {}
        for r in read_csv(j(indir, 'allocation_priority.csv'), False):
            self.allocation[r['account_ref']] = (
                r['priority'], cents(r['cap_usd']))
        pool_rows = read_csv_list(j(indir, 'financing_pool.csv'), False)
        self.pool_cents = cents(pool_rows[0]['pool_usd']) if pool_rows else 0

    # -- resolution -------------------------------------------------------

    def resolve_account(self, account_ref: str, scope: str, date: str) -> str:
        """Venue-local reference -> book-scoped account ref, at `date`."""
        if account_ref.startswith('acct::'):
            base = account_ref
        elif ':' in account_ref:
            tl = self.venue_map.get(account_ref)
            base = tl.asof(date) if tl is not None else None
            if base is None:
                base = account_ref            # opaque but stable
        else:
            book = scope.split('::')[-1] if scope else ''
            base = 'acct::%s::%s' % (book, account_ref)
        return self.apply_mergers(base, date)

    def apply_mergers(self, account_ref: str, date: str) -> str:
        seen = set()
        cur = account_ref
        while True:
            tl = self.mergers.get(cur)
            if tl is None:
                return cur
            nxt = tl.asof(date)
            if nxt is None or nxt == cur or nxt in seen:
                return cur
            seen.add(cur)
            cur = nxt

    def entity(self, account_ref: str) -> str:
        return self.entity_of_account.get(account_ref, account_ref)

    def resolve_lei(self, lei: str, date: str) -> str:
        seen = set()
        cur = lei
        while True:
            tl = self.reassign.get(cur)
            if tl is None:
                return cur
            nxt = tl.asof(date)
            if nxt is None or nxt == cur or nxt in seen:
                return cur
            seen.add(cur)
            cur = nxt

    def fx_ratio(self, asset: str, date: str):
        tl = self.fx.get(asset)
        if tl is None:
            return (1, 1)
        val = tl.asof(date)
        if val is None:
            val = tl.first((1, 1))
        return val

    def symbol_at(self, instrument_id: str, date: str) -> str:
        tl = self.instrument_symbol.get(instrument_id)
        if tl is None:
            return instrument_id
        val = tl.asof(date)
        if val is None:
            val = tl.first(instrument_id)
        return val

    def instrument_for_symbol(self, symbol: str, date: str) -> str:
        tl = self.symbol_to_instrument.get(symbol)
        if tl is None:
            return symbol
        val = tl.asof(date)
        if val is None:
            val = tl.first(symbol)
        return val

    def contract_at(self, instrument_id: str, date: str):
        tl = self.contract.get(instrument_id)
        if tl is None:
            return (1, '')
        val = tl.asof(date)
        if val is None:
            val = tl.first((1, ''))
        return val

    def split_factor(self, instrument_id: str, after: str, upto: str) -> int:
        """Product of split ratios strictly after `after` and up to `upto`."""
        factor = 1
        for action_date, ratio in self.corporate_actions.get(instrument_id, ()):
            if after < action_date <= upto:
                factor *= ratio
        return factor


# --------------------------------------------------------------------------
# fills
# --------------------------------------------------------------------------


class Fill:
    __slots__ = ('fill_id', 'trade_date', 'venue', 'account_ref', 'entity',
                 'instrument_id', 'symbol', 'lei', 'netting_set', 'asset',
                 'multiplier', 'signed_qty', 'unit_cost', 'notional',
                 'fee', 'commission', 'rebate')


def load_fills(ref: Reference, indir: str, policy: dict):
    fills_dir = os.path.join(indir, 'fills')
    paths = sorted(glob.glob(os.path.join(fills_dir, '*.csv')))
    if not paths and os.path.isfile(fills_dir + '.csv'):
        paths = [fills_dir + '.csv']

    fills = []
    for path in paths:
        for r in read_csv(path):
            f = Fill()
            f.fill_id = r['fill_id']
            f.trade_date = r['trade_date']
            f.venue = r['venue']
            scope = r.get('clearing_scope', '')
            f.account_ref = ref.resolve_account(r['account_ref'], scope, f.trade_date)
            f.entity = ref.entity(f.account_ref)
            f.symbol = r['symbol']
            f.instrument_id = ref.instrument_for_symbol(f.symbol, f.trade_date)
            f.lei = ref.resolve_lei(r['counterparty_lei'], f.trade_date)
            tl = ref.netting.get(f.account_ref)
            ns = tl.asof(f.trade_date) if tl is not None else None
            f.netting_set = ns if ns is not None else str(
                (policy.get('netting') or {}).get('missing', 'UNASSIGNED'))

            mult, asset = ref.contract_at(f.instrument_id, f.trade_date)
            f.multiplier = mult
            f.asset = asset
            fx_num, fx_den = ref.fx_ratio(asset, f.trade_date)

            qty = int(r['quantity'])
            side = r['side'].strip().upper()
            f.signed_qty = qty if side in ('BUY', 'B', 'BOT') else -qty

            p_num, p_den = ratio_of(r['price'])
            # notional_usd = quantity * multiplier * price * fx, rounded once
            f.notional = round_ratio(
                f.signed_qty * mult * p_num * fx_num * 100, p_den * fx_den,
                'half_even')
            f.unit_cost = Fraction(p_num * fx_num, p_den * fx_den)

            fee_num, fee_den = ratio_of(r['fee'])
            f.fee = round_ratio(fee_num * fx_num * 100, fee_den * fx_den, 'up')

            # commission band on the venue's schedule in force at trade date
            bps = 0
            sched = ref.fee_schedule.get(f.venue)
            if sched is not None:
                bands = sched.asof(f.trade_date)
                if bands is None:
                    bands = sched.first(None)
                if bands:
                    target = abs(f.notional)
                    chosen = None
                    for min_notional, band_bps in bands:
                        if min_notional <= target:
                            chosen = band_bps
                        else:
                            break
                    if chosen is not None:
                        bps = chosen
            f.commission = round_ratio(abs(f.notional) * bps, 10000, 'up')
            f.rebate = 0                      # filled in by a second pass
            fills.append(f)

    _apply_rebates(ref, fills, policy)
    return fills


def _apply_rebates(ref: Reference, fills, policy: dict):
    cfg = policy.get('rebate') or {}
    window_days = int(cfg.get('window_days', 30))
    include_trade_date = bool(cfg.get('window_includes_trade_date', False))

    per_venue = defaultdict(lambda: defaultdict(int))
    for f in fills:
        per_venue[f.venue][f.trade_date] += abs(f.notional)

    cumulative = {}
    for venue, by_date in per_venue.items():
        dates = sorted(by_date)
        cum = [0]
        total = 0
        for d in dates:
            total += by_date[d]
            cum.append(total)
        cumulative[venue] = (dates, cum)

    day = _dt.timedelta(days=1)
    cache = {}
    for f in fills:
        key = (f.venue, f.trade_date)
        basis = cache.get(key)
        if basis is None:
            trade = _dt.date.fromisoformat(f.trade_date)
            hi = trade if include_trade_date else trade - day
            lo = hi - day * (window_days - 1)
            dates, cum = cumulative[f.venue]
            hi_s, lo_s = hi.isoformat(), lo.isoformat()
            basis = (cum[bisect.bisect_right(dates, hi_s)]
                     - cum[bisect.bisect_left(dates, lo_s)])
            cache[key] = basis
        bps = 0
        for min_trailing, tier_bps in ref.rebate_tiers.get(f.venue, ()):
            if min_trailing <= basis:
                bps = tier_bps
            else:
                break
        f.rebate = round_ratio(abs(f.notional) * bps, 10000, 'up')


# --------------------------------------------------------------------------
# entity tokens
# --------------------------------------------------------------------------

_ALPHABETS = {
    'hex_lower': '0123456789abcdef',
    'hex_upper': '0123456789ABCDEF',
}


def build_tokens(entity_keys, seed, policy: dict):
    cfg = policy.get('entity_tokens') or {}
    prefix = str(cfg.get('prefix', 'ent_'))
    length = int(cfg.get('length', 12))
    alphabet = _ALPHABETS.get(str(cfg.get('alphabet', 'hex_lower')),
                              _ALPHABETS['hex_lower'])
    if not cfg.get('length_counts_prefix', False):
        body_len = length
    else:
        body_len = max(0, length - len(prefix))

    keys = sorted(entity_keys)
    base = ('%s' % seed).encode('utf-8')
    tokens = set()
    counter = 0
    radix = len(alphabet)
    while len(tokens) < len(keys):
        digest = hashlib.blake2b(base + b'|' + str(counter).encode('ascii'),
                                 digest_size=32).digest()
        value = int.from_bytes(digest, 'big')
        chars = []
        for _ in range(body_len):
            value, rem = divmod(value, radix)
            chars.append(alphabet[rem])
        tokens.add(''.join(reversed(chars)))
        counter += 1
    # rank-preserving assignment keeps report ordering independent of the seed
    ordered = sorted(tokens)
    return {k: prefix + t for k, t in zip(keys, ordered)}


# --------------------------------------------------------------------------
# lot engines
# --------------------------------------------------------------------------


def run_lots(ref: Reference, instrument_id: str, group, report_date: str):
    """Weighted-average and FIFO books for one (entity, instrument)."""
    actions = ref.corporate_actions.get(instrument_id, ())
    ai = 0

    wa_qty = 0
    wa_avg = Fraction(0)
    wa_realised = 0

    lots = deque()          # [abs_qty, unit_cost]
    fifo_pos = 0
    fifo_realised = 0

    def apply_split(ratio):
        nonlocal wa_qty, wa_avg, fifo_pos
        wa_qty *= ratio
        if wa_avg:
            wa_avg = wa_avg / ratio
        fifo_pos *= ratio
        for lot in lots:
            lot[0] *= ratio
            lot[1] = lot[1] / ratio

    for f in group:
        while ai < len(actions) and actions[ai][0] <= f.trade_date:
            apply_split(actions[ai][1])
            ai += 1

        dq = f.signed_qty
        u = f.unit_cost
        mult = f.multiplier

        # --- weighted average -------------------------------------------
        if wa_qty == 0:
            wa_qty = dq
            wa_avg = u
        elif (wa_qty > 0) == (dq > 0):
            newq = wa_qty + dq
            wa_avg = (wa_avg * wa_qty + u * dq) / newq
            wa_qty = newq
        else:
            closed = min(abs(wa_qty), abs(dq))
            sign = 1 if wa_qty > 0 else -1
            realised = (u - wa_avg) * closed * mult * sign
            wa_realised += round_fraction(realised, 2, 'half_even')
            newq = wa_qty + dq
            if newq == 0:
                wa_qty = 0
                wa_avg = Fraction(0)
            elif (newq > 0) == (wa_qty > 0):
                wa_qty = newq
            else:
                wa_qty = newq
                wa_avg = u

        # --- fifo --------------------------------------------------------
        if fifo_pos == 0 or (fifo_pos > 0) == (dq > 0):
            lots.append([abs(dq), u])
            fifo_pos += dq
        else:
            sign = 1 if fifo_pos > 0 else -1
            remaining = abs(dq)
            realised = Fraction(0)
            while remaining and lots:
                lot = lots[0]
                take = lot[0] if lot[0] <= remaining else remaining
                realised += (u - lot[1]) * take * mult * sign
                lot[0] -= take
                remaining -= take
                if lot[0] == 0:
                    lots.popleft()
            fifo_realised += round_fraction(realised, 2, 'half_even')
            fifo_pos += dq
            if remaining:
                lots.append([remaining, u])

    while ai < len(actions) and actions[ai][0] <= report_date:
        apply_split(actions[ai][1])
        ai += 1

    avg_micro = 0 if wa_qty == 0 else round_fraction(wa_avg, 6, 'half_even')
    return wa_qty, avg_micro, wa_realised, fifo_realised


# --------------------------------------------------------------------------
# interest
# --------------------------------------------------------------------------

_DAY_BASIS = 365
_BPS = 10000
_DEN_STEP = _BPS * _DAY_BASIS


def accrue_interest(ref: Reference, moves_by_date):
    """Return (closing_balance_cents, interest_accrued_cents) for one book."""
    start = _dt.date.fromisoformat(ref.period_start)
    end = _dt.date.fromisoformat(ref.report_date)
    balance_num = 0          # balance (cents) == balance_num / den
    interest_num = 0
    den = 1
    day = _dt.timedelta(days=1)
    cur = start
    while cur < end:
        iso = cur.isoformat()
        move = moves_by_date.get(iso)
        if move:
            balance_num += move * den
        if balance_num < 0:
            rate = ref.interest_rate.asof(iso)
            if rate is None:
                rate = ref.interest_rate.first(0) or 0
            interest_num = interest_num * _DEN_STEP + balance_num * rate
            balance_num = balance_num * _DEN_STEP + balance_num * rate
            den *= _DEN_STEP
        cur += day
    return (round_ratio(balance_num, den, 'half_even'),
            round_ratio(interest_num, den, 'half_even'))


# --------------------------------------------------------------------------
# report construction
# --------------------------------------------------------------------------


class Reports:
    def __init__(self, ref: Reference, fills, tokens, policy):
        self.ref = ref
        self.fills = fills
        self.tokens = tokens
        self.policy = policy
        self.snapshots = ref.snapshots

    # -- helpers ---------------------------------------------------------

    def _token(self, entity):
        return self.tokens[entity]

    def _haircut_bps(self, asset, as_of):
        tl = self.ref.haircuts.get(asset)
        if tl is None:
            return int((self.policy.get('haircut') or {}).get('missing', 0))
        val = tl.asof(as_of)
        if val is None:
            return int((self.policy.get('haircut') or {}).get('missing', 0))
        return val

    def _margin_bps(self, instrument_id, as_of):
        tl = self.ref.margin.get(instrument_id)
        if tl is None:
            return int((self.policy.get('initial_margin') or {}).get('missing', 0))
        val = tl.asof(as_of)
        if val is None:
            return int((self.policy.get('initial_margin') or {}).get('missing', 0))
        return val

    # -- reports ---------------------------------------------------------

    def attribution(self):
        rows = []
        for f in self.fills:
            rows.append({
                'fill_id': f.fill_id,
                'account': self._token(f.entity),
                'instrument': self.ref.symbol_at(f.instrument_id, f.trade_date),
                'counterparty': f.lei,
                'notional_usd': fmt_cents(f.notional),
            })
        return rows

    def positions(self):
        rows = []
        for as_of in self.snapshots:
            agg = defaultdict(lambda: [0, 0])
            for f in self.fills:
                if f.trade_date > as_of:
                    continue
                factor = self.ref.split_factor(f.instrument_id, f.trade_date, as_of)
                cell = agg[(f.entity, f.instrument_id)]
                cell[0] += f.signed_qty * factor
                cell[1] += abs(f.signed_qty) * factor
            for (entity, instrument_id), (net, gross) in sorted(agg.items()):
                rows.append({
                    'as_of': as_of,
                    'account': self._token(entity),
                    'instrument': self.ref.symbol_at(instrument_id, as_of),
                    'net_quantity': str(net),
                    'gross_quantity': str(gross),
                })
        return rows

    def fees(self):
        rows = []
        for as_of in self.snapshots:
            agg = defaultdict(lambda: [0, 0, 0])
            for f in self.fills:
                if f.trade_date > as_of:
                    continue
                cell = agg[(f.entity, f.venue)]
                cell[0] += f.fee
                cell[1] += f.commission
                cell[2] += f.rebate
            for (entity, venue), (fee, comm, reb) in sorted(agg.items()):
                rows.append({
                    'as_of': as_of,
                    'account': self._token(entity),
                    'venue': venue,
                    'fee_usd': fmt_cents(fee),
                    'commission_usd': fmt_cents(comm),
                    'rebate_usd': fmt_cents(reb),
                })
        return rows

    def exposure(self):
        rows = []
        for as_of in self.snapshots:
            agg = defaultdict(lambda: [0, 0])
            for f in self.fills:
                if f.trade_date > as_of:
                    continue
                cell = agg[(f.entity, f.asset)]
                cell[0] += f.notional
                cell[1] += abs(f.notional)
            for (entity, asset), (net, gross) in sorted(agg.items()):
                haircut = round_ratio(gross * self._haircut_bps(asset, as_of),
                                      10000, 'half_even')
                rows.append({
                    'as_of': as_of,
                    'account': self._token(entity),
                    'settlement_asset': asset,
                    'net_exposure_usd': fmt_cents(net),
                    'gross_exposure_usd': fmt_cents(gross),
                    'haircut_usd': fmt_cents(haircut),
                    'net_after_haircut_usd': fmt_cents(net - haircut),
                })
        return rows

    def counterparty_exposure(self):
        rows = []
        for as_of in self.snapshots:
            agg = defaultdict(lambda: [0, 0])
            for f in self.fills:
                if f.trade_date > as_of:
                    continue
                cell = agg[(f.lei, f.asset)]
                cell[0] += f.notional
                cell[1] += abs(f.notional)
            for (lei, asset), (net, gross) in sorted(agg.items()):
                rows.append({
                    'as_of': as_of,
                    'counterparty': lei,
                    'settlement_asset': asset,
                    'net_exposure_usd': fmt_cents(net),
                    'gross_exposure_usd': fmt_cents(gross),
                })
        return rows

    def netting_exposure(self):
        rows = []
        for as_of in self.snapshots:
            agg = defaultdict(lambda: [0, 0])
            for f in self.fills:
                if f.trade_date > as_of:
                    continue
                cell = agg[(f.netting_set, f.asset)]
                cell[0] += f.notional
                cell[1] += abs(f.notional)
            for (netting_set, asset), (net, gross) in sorted(agg.items()):
                rows.append({
                    'as_of': as_of,
                    'netting_set': netting_set,
                    'settlement_asset': asset,
                    'net_exposure_usd': fmt_cents(net),
                    'gross_exposure_usd': fmt_cents(gross),
                })
        return rows

    def venue_summary(self):
        rows = []
        for as_of in self.snapshots:
            agg = defaultdict(lambda: [0, 0, 0, 0])
            for f in self.fills:
                if f.trade_date > as_of:
                    continue
                cell = agg[f.venue]
                cell[0] += 1
                cell[1] += abs(f.notional)
                cell[2] += f.fee
                cell[3] += f.commission
            for venue, (count, gross, fee, comm) in sorted(agg.items()):
                rows.append({
                    'as_of': as_of,
                    'venue': venue,
                    'fill_count': str(count),
                    'gross_notional_usd': fmt_cents(gross),
                    'fee_usd': fmt_cents(fee),
                    'commission_usd': fmt_cents(comm),
                })
        return rows

    def margin(self):
        rows = []
        for as_of in self.snapshots:
            agg = defaultdict(lambda: defaultdict(int))
            for f in self.fills:
                if f.trade_date > as_of:
                    continue
                agg[f.entity][f.instrument_id] += abs(f.notional)
            for entity, by_instrument in sorted(agg.items()):
                total = 0
                for instrument_id, gross in sorted(by_instrument.items()):
                    total += round_ratio(gross * self._margin_bps(instrument_id, as_of),
                                         10000, 'half_even')
                rows.append({
                    'as_of': as_of,
                    'account': self._token(entity),
                    'initial_margin_usd': fmt_cents(total),
                })
        return rows

    def lots(self):
        groups = defaultdict(list)
        for f in self.fills:
            groups[(f.entity, f.instrument_id)].append(f)
        rows = []
        for (entity, instrument_id), group in sorted(groups.items()):
            group.sort(key=lambda f: (f.trade_date, f.fill_id))
            qty, avg_micro, realised, fifo = run_lots(
                self.ref, instrument_id, group, self.ref.report_date)
            rows.append({
                'account': self._token(entity),
                'instrument_id': instrument_id,
                'open_quantity': str(qty),
                'average_cost_usd': fmt_micro(avg_micro),
                'realised_pnl_usd': fmt_cents(realised),
                'fifo_realised_pnl_usd': fmt_cents(fifo),
            })
        return rows

    def financing(self):
        eligible = sorted({f.account_ref for f in self.fills})

        def order_key(account_ref):
            # "order: priority asc, account_ref asc" - priority is an integer
            # field, so it orders numerically; unparseable values sort last.
            priority = self.ref.allocation.get(account_ref, ('', 0))[0]
            try:
                return (0, int(priority), '', account_ref)
            except (TypeError, ValueError):
                return (1, 0, str(priority), account_ref)

        remaining = self.ref.pool_cents
        cumulative = 0
        result = {}
        for account_ref in sorted(eligible, key=order_key):
            priority, cap = self.ref.allocation.get(account_ref, ('', 0))
            take = cap if cap < remaining else remaining
            if take < 0:
                take = 0
            remaining -= take
            cumulative += take
            result[account_ref] = (priority, cap, take, cumulative)
        rows = []
        for account_ref in eligible:
            priority, cap, take, cum = result[account_ref]
            rows.append({
                'account_ref': account_ref,
                'priority': str(priority),
                'cap_usd': fmt_cents(cap),
                'allocated_usd': fmt_cents(take),
                'cumulative_usd': fmt_cents(cum),
            })
        return rows

    def interest(self):
        moves = defaultdict(lambda: defaultdict(int))
        for f in self.fills:
            moves[f.entity][f.trade_date] += (-f.notional - f.fee
                                              - f.commission + f.rebate)
        rows = []
        for entity in sorted(moves):
            closing, accrued = accrue_interest(self.ref, moves[entity])
            rows.append({
                'account': self._token(entity),
                'closing_balance_usd': fmt_cents(closing),
                'interest_accrued_usd': fmt_cents(accrued),
            })
        return rows


_BUILDERS = {
    'attribution.csv': 'attribution',
    'positions.csv': 'positions',
    'fees.csv': 'fees',
    'exposure.csv': 'exposure',
    'counterparty_exposure.csv': 'counterparty_exposure',
    'netting_exposure.csv': 'netting_exposure',
    'venue_summary.csv': 'venue_summary',
    'margin.csv': 'margin',
    'lots.csv': 'lots',
    'financing.csv': 'financing',
    'interest.csv': 'interest',
}


def write_report(path, columns, rows, sort_keys):
    if sort_keys:
        rows = sorted(rows, key=lambda r: tuple(r[k] for k in sort_keys))
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        writer = csv.writer(fh, lineterminator='\n')
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[c] for c in columns])


def parse_memory(text):
    if not text:
        return None
    text = str(text).strip().upper()
    units = {'B': 1, 'KB': 1024, 'MB': 1024 ** 2, 'GB': 1024 ** 3,
             'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}
    for suffix in ('KB', 'MB', 'GB', 'B', 'K', 'M', 'G'):
        if text.endswith(suffix):
            head = text[:-len(suffix)].strip()
            try:
                return int(float(head) * units[suffix])
            except ValueError:
                return None
    try:
        return int(text)
    except ValueError:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description='Clearing reconciliation')
    parser.add_argument('input_dir')
    parser.add_argument('--policy', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seed', default='0')
    parser.add_argument('--max-memory', dest='max_memory', default=None)
    args = parser.parse_args(argv)

    parse_memory(args.max_memory)      # accepted; the tool is streaming-light

    policy = load_policy(args.policy)
    ref = Reference(args.input_dir)
    fills = load_fills(ref, args.input_dir, policy)

    entities = {f.entity for f in fills}
    tokens = build_tokens(entities, args.seed, policy)

    reports = Reports(ref, fills, tokens, policy)
    os.makedirs(args.output, exist_ok=True)

    spec = policy.get('reports') or {}
    for name in sorted(spec):
        definition = spec[name] or {}
        columns = list((definition.get('columns') or {}).keys())
        sort_keys = definition.get('sort') or []
        if isinstance(sort_keys, str):
            sort_keys = [sort_keys]
        builder = _BUILDERS.get(name)
        if builder is None:
            print('warning: no builder for report %s' % name, file=sys.stderr)
            continue
        rows = getattr(reports, builder)()
        write_report(os.path.join(args.output, name), columns, rows, sort_keys)
    return 0


if __name__ == '__main__':
    sys.exit(main())
