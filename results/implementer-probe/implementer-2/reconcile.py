#!/usr/bin/env python3
"""Clearing reconciliation.

Reads a clearing period (fills plus reference data) and writes one CSV per
report defined in the reporting policy.

    python3 reconcile.py <input_dir> --policy <policy.yaml> --output <dir>
                         --seed <seed> [--max-memory 128MB]

Every path is taken from the arguments; nothing is hard-coded to a period.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import glob
import hashlib
import json
import os
import sys
from sys import intern
from decimal import Decimal, getcontext, ROUND_HALF_EVEN, ROUND_UP, ROUND_HALF_UP, ROUND_DOWN, ROUND_FLOOR, ROUND_CEILING
from fractions import Fraction

# Products of quantity * multiplier * price * fx need ~32 significant digits;
# give the context ample head-room so those multiplications stay exact.
getcontext().prec = 80

# --------------------------------------------------------------------------
# policy loading
# --------------------------------------------------------------------------

def _scalar(text):
    t = text.strip()
    if t.startswith('[') and t.endswith(']'):
        inner = t[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p) for p in inner.split(',')]
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        return t[1:-1]
    low = t.lower()
    if low in ('true', 'yes'):
        return True
    if low in ('false', 'no'):
        return False
    if low in ('null', '~', ''):
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def _mini_yaml(text):
    """Parse the small YAML subset used by the reporting policy."""
    root = {}
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
            item = _scalar(line[2:])
            if not isinstance(parent, list):
                raise ValueError('unexpected list item: %r' % raw)
            parent.append(item)
            continue
        if ':' not in line:
            raise ValueError('unparsable policy line: %r' % raw)
        key, _, rest = line.partition(':')
        key = key.strip()
        rest = rest.strip()
        if rest == '':
            child = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _scalar(rest)
    return root


def load_policy(path):
    with open(path, 'r', encoding='utf-8') as fh:
        text = fh.read()
    try:
        import yaml  # type: ignore
    except Exception:
        return _mini_yaml(text)
    try:
        data = yaml.safe_load(text)
    except Exception:
        return _mini_yaml(text)
    if not isinstance(data, dict):
        return _mini_yaml(text)
    return data


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def read_csv(path):
    """Yield rows of a CSV as dicts; missing file yields nothing."""
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8-sig', newline='') as fh:
        for row in csv.DictReader(fh):
            yield row


def eff_pick(entries, date, default=None):
    """entries: list of (effective_from, value) sorted ascending.

    Returns the value of the last entry with effective_from <= date."""
    if not entries:
        return default
    lo, hi = 0, len(entries)
    while lo < hi:
        mid = (lo + hi) // 2
        if entries[mid][0] <= date:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return default
    return entries[lo - 1][1]


def sort_effective(mapping):
    for key in mapping:
        mapping[key].sort(key=lambda item: item[0])


def round_ratio(num, den, mode):
    """Round num/den (den > 0) to the nearest integer under `mode`."""
    if den < 0:
        num, den = -num, -den
    neg = num < 0
    n = -num if neg else num
    q, r = divmod(n, den)
    if mode == 'half_even':
        twice = r * 2
        if twice > den or (twice == den and (q & 1)):
            q += 1
    elif mode == 'up':  # away from zero
        if r:
            q += 1
    elif mode == 'down':  # toward zero
        pass
    elif mode == 'half_up':
        if r * 2 >= den:
            q += 1
    else:
        raise ValueError('unknown rounding mode %r' % (mode,))
    return -q if neg else q


def rescale(value, from_dp, to_dp, mode):
    """Move an integer figure between fixed-point scales without re-rounding
    when it can be avoided."""
    if from_dp == to_dp:
        return value
    if from_dp < to_dp:
        return value * 10 ** (to_dp - from_dp)
    return round_ratio(value, 10 ** (from_dp - to_dp), mode)


def fmt_scaled(value, places):
    """Format an integer scaled by 10**places as a fixed-point decimal."""
    if value == 0:
        return '0.' + '0' * places if places else '0'
    sign = '-' if value < 0 else ''
    a = -value if value < 0 else value
    scale = 10 ** places
    whole, frac = divmod(a, scale)
    if places == 0:
        return '%s%d' % (sign, whole)
    return '%s%d.%0*d' % (sign, whole, places, frac)


def parse_memory(text):
    if text is None:
        return None
    t = str(text).strip().upper().replace(' ', '')
    mult = 1
    for suffix, factor in (('KB', 1024), ('MB', 1024 ** 2), ('GB', 1024 ** 3),
                           ('K', 1024), ('M', 1024 ** 2), ('G', 1024 ** 3), ('B', 1)):
        if t.endswith(suffix):
            mult = factor
            t = t[: -len(suffix)]
            break
    if not t:
        return None
    try:
        return int(float(t) * mult)
    except ValueError:
        return None


def scaled_int(text, dp):
    """Exact fixed-point integer for a decimal literal at `dp` places."""
    value = Decimal(text).scaleb(dp)
    integral = value.to_integral_value(rounding=ROUND_HALF_EVEN)
    return int(integral)


def priority_key(text):
    """Financing priority sorts as a number when it is one."""
    try:
        return (0, int(str(text).strip()), '')
    except (TypeError, ValueError):
        return (1, 0, str(text))


# --------------------------------------------------------------------------
# reference data
# --------------------------------------------------------------------------

class Reference(object):
    def __init__(self, input_dir, policy, money_dp=2):
        self.dir = input_dir
        self.policy = policy
        self.money_dp = money_dp
        p = lambda name: os.path.join(input_dir, name)

        # ---- period -------------------------------------------------------
        with open(p('period.json'), 'r', encoding='utf-8') as fh:
            period = json.load(fh)
        self.period_start = period['period_start']
        self.report_date = period['report_date']
        self.snapshot_dates = sorted(period.get('snapshot_dates') or [])

        # ---- fx -----------------------------------------------------------
        fx = {}
        for row in read_csv(p('fx_rates.csv')):
            fx.setdefault(row['asset'], []).append((row['utc_date'], row['rate_usd']))
        sort_effective(fx)
        self.fx_dates = {a: [d for d, _ in v] for a, v in fx.items()}
        self.fx_rates = {a: [Decimal(r) for _, r in v] for a, v in fx.items()}
        self._fx_cache = {}

        # ---- instruments --------------------------------------------------
        sym_by_ins = {}
        self.symbol_to_instrument = {}
        for row in read_csv(p('instrument_symbols.csv')):
            sym_by_ins.setdefault(row['instrument_id'], []).append(
                (row['effective_from'], row['symbol']))
            self.symbol_to_instrument[row['symbol']] = row['instrument_id']
        sort_effective(sym_by_ins)
        self.symbols = sym_by_ins

        revisions = {}
        for row in read_csv(p('contract_revisions.csv')):
            revisions.setdefault(row['instrument_id'], []).append(
                (row['effective_from'], (int(row['contract_multiplier']), row['settlement_asset'])))
        sort_effective(revisions)
        self.revisions = revisions

        margin = {}
        for row in read_csv(p('margin_rates.csv')):
            margin.setdefault(row['instrument_id'], []).append(
                (row['effective_from'], int(row['initial_margin_bps'])))
        sort_effective(margin)
        self.margin = margin
        self.margin_missing = 0

        actions = {}
        for row in read_csv(p('corporate_actions.csv')):
            actions.setdefault(row['instrument_id'], []).append(
                (row['action_date'], int(row['split_ratio'])))
        for key in actions:
            actions[key].sort(key=lambda item: item[0])
        self.corporate_actions = actions
        self._split_cache = {}

        # ---- haircuts -----------------------------------------------------
        haircuts = {}
        for row in read_csv(p('haircuts.csv')):
            haircuts.setdefault(row['asset'], []).append(
                (row['effective_from'], int(row['haircut_bps'])))
        sort_effective(haircuts)
        self.haircuts = haircuts

        # ---- counterparties ----------------------------------------------
        reassign = {}
        for row in read_csv(p('counterparty_reassignments.csv')):
            reassign.setdefault(row['old_lei'], []).append(
                (row['effective_from'], row['new_lei']))
        sort_effective(reassign)
        self.reassign = reassign
        self._lei_cache = {}

        # ---- accounts: venue codes, mergers, links ------------------------
        venue_map = {}
        for row in read_csv(p('venue_account_map.csv')):
            venue_map.setdefault(row['venue_code'], []).append(
                (row['effective_from'], row['account_ref']))
        sort_effective(venue_map)
        self.venue_map = venue_map

        mergers = {}
        for row in read_csv(p('account_mergers.csv')):
            handle = row['venue_handle']
            eff = row['effective_from']
            source = self.resolve_venue_code(handle, eff)
            if source is None:
                continue  # handle unknown to any venue map: nothing to merge
            target = row['merged_account_ref']
            if target == source:
                continue
            mergers.setdefault(source, []).append((eff, target))
        sort_effective(mergers)
        self.mergers = mergers
        self._merge_cache = {}

        parent = {}

        def find(x):
            parent.setdefault(x, x)
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        for row in read_csv(p('account_links.csv')):
            a, b = row['account_a'], row['account_b']
            ra, rb = find(a), find(b)
            if ra != rb:
                if ra < rb:
                    parent[rb] = ra
                else:
                    parent[ra] = rb
        # canonical representative = lexicographically smallest member
        rep = {}
        for node in parent:
            root = find(node)
            cur = rep.get(root)
            if cur is None or node < cur:
                rep[root] = node
        self._link_parent = parent
        self._link_find = find
        self._link_rep = rep

        # ---- netting ------------------------------------------------------
        netting = {}
        for row in read_csv(p('netting_sets.csv')):
            netting.setdefault(row['account_ref'], []).append(
                (row['effective_from'], row['netting_set']))
        sort_effective(netting)
        self.netting = netting
        self.netting_missing = str(
            (policy.get('netting') or {}).get('missing', 'UNASSIGNED'))
        self._netting_cache = {}

        # ---- fees / rebates ------------------------------------------------
        schedule = {}
        for row in read_csv(p('fee_schedule.csv')):
            schedule.setdefault(row['venue'], {}).setdefault(row['effective_from'], []).append(
                (Decimal(row['min_notional_usd']), int(row['commission_bps'])))
        self.fee_schedule = {}
        for venue, by_eff in schedule.items():
            entries = []
            for eff in sorted(by_eff):
                bands = sorted(by_eff[eff], key=lambda b: b[0])
                cents = [(scaled_int(b[0], money_dp), b[1]) for b in bands]
                entries.append((eff, cents))
            self.fee_schedule[venue] = entries

        rebates = {}
        for row in read_csv(p('rebate_tiers.csv')):
            rebates.setdefault(row['venue'], []).append(
                (scaled_int(row['min_trailing_usd'], money_dp), int(row['rebate_bps'])))
        for venue in rebates:
            rebates[venue].sort(key=lambda t: t[0])
        self.rebate_tiers = rebates

        # ---- financing -----------------------------------------------------
        pool = 0
        for row in read_csv(p('financing_pool.csv')):
            pool += scaled_int(row['pool_usd'], money_dp)
        self.pool_cents = pool
        self.allocation = {}
        for row in read_csv(p('allocation_priority.csv')):
            self.allocation[row['account_ref']] = (
                priority_key(row['priority']), row['priority'],
                scaled_int(row['cap_usd'], money_dp))

        # ---- interest -------------------------------------------------------
        rates = []
        for row in read_csv(p('interest_rates.csv')):
            rates.append((row['effective_from'], int(row['debit_rate_bps'])))
        rates.sort(key=lambda item: item[0])
        self.interest_rates = rates

    # -- lookups ------------------------------------------------------------

    def fx_rate(self, asset, date):
        key = (asset, date)
        hit = self._fx_cache.get(key)
        if hit is not None:
            return hit
        dates = self.fx_dates.get(asset)
        if not dates:
            raise KeyError('no fx rates published for asset %r' % (asset,))
        idx = bisect.bisect_right(dates, date)
        if idx == 0:
            # nothing published on or before this date; fall back to the first
            # published rate so the run still produces a figure.
            idx = 1
        rate = self.fx_rates[asset][idx - 1]
        self._fx_cache[key] = rate
        return rate

    def instrument_for_symbol(self, symbol):
        try:
            return self.symbol_to_instrument[symbol]
        except KeyError:
            raise KeyError('unknown instrument symbol %r' % (symbol,))

    def symbol_at(self, instrument_id, date):
        entries = self.symbols.get(instrument_id)
        if not entries:
            return instrument_id
        value = eff_pick(entries, date)
        if value is None:
            value = entries[0][1]
        return value

    def contract_terms(self, instrument_id, date):
        entries = self.revisions.get(instrument_id)
        if not entries:
            raise KeyError('no contract revision for %r' % (instrument_id,))
        value = eff_pick(entries, date)
        if value is None:
            value = entries[0][1]
        return value

    def margin_bps(self, instrument_id, date):
        value = eff_pick(self.margin.get(instrument_id, []), date)
        return self.margin_missing if value is None else value

    def haircut_bps(self, asset, date):
        value = eff_pick(self.haircuts.get(asset, []), date)
        return 0 if value is None else value

    def netting_set(self, account_ref, date):
        key = (account_ref, date)
        hit = self._netting_cache.get(key)
        if hit is None:
            value = eff_pick(self.netting.get(account_ref, []), date)
            hit = self.netting_missing if value is None else value
            self._netting_cache[key] = hit
        return hit

    def resolve_venue_code(self, code, date):
        entries = self.venue_map.get(code)
        if not entries:
            return None
        value = eff_pick(entries, date)
        if value is None:
            value = entries[0][1]
        return value

    def resolve_mergers(self, account_ref, date):
        key = (account_ref, date)
        hit = self._merge_cache.get(key)
        if hit is not None:
            return hit
        seen = {account_ref}
        current = account_ref
        for _ in range(64):
            target = eff_pick(self.mergers.get(current, []), date)
            if target is None or target in seen:
                break
            seen.add(target)
            current = target
        self._merge_cache[key] = current
        return current

    def entity_of(self, account_ref):
        parent = self._link_parent
        if account_ref not in parent:
            return account_ref
        return self._link_rep[self._link_find(account_ref)]

    def resolve_lei(self, lei, date):
        key = (lei, date)
        hit = self._lei_cache.get(key)
        if hit is not None:
            return hit
        seen = {lei}
        current = lei
        for _ in range(64):
            nxt = eff_pick(self.reassign.get(current, []), date)
            if nxt is None or nxt in seen:
                break
            seen.add(nxt)
            current = nxt
        self._lei_cache[key] = current
        return current

    def split_factor(self, instrument_id, from_date, to_date):
        """Product of split ratios with from_date < action_date <= to_date."""
        key = (instrument_id, from_date, to_date)
        hit = self._split_cache.get(key)
        if hit is not None:
            return hit
        factor = 1
        for action_date, ratio in self.corporate_actions.get(instrument_id, ()):
            if from_date < action_date <= to_date:
                factor *= ratio
        self._split_cache[key] = factor
        return factor

    def debit_rate_bps(self, date):
        value = eff_pick(self.interest_rates, date)
        return 0 if value is None else value


# --------------------------------------------------------------------------
# entity tokens
# --------------------------------------------------------------------------

class TokenMinter(object):
    def __init__(self, policy, seed):
        cfg = policy.get('entity_tokens') or {}
        self.prefix = str(cfg.get('prefix', 'ent_'))
        self.length = int(cfg.get('length', 12))
        alphabet = str(cfg.get('alphabet', 'hex_lower'))
        if alphabet not in ('hex_lower', 'hex'):
            raise ValueError('unsupported token alphabet %r' % (alphabet,))
        if not cfg.get('length_counts_prefix', False):
            self.digits = self.length
        else:
            self.digits = max(0, self.length - len(self.prefix))
        self.seed = str(seed)
        self._tokens = {}
        self._used = {}

    def token(self, entity_id):
        hit = self._tokens.get(entity_id)
        if hit is not None:
            return hit
        attempt = 0
        while True:
            material = '%s|%s' % (self.seed, entity_id)
            if attempt:
                material = '%s|%d' % (material, attempt)
            digest = hashlib.sha256(material.encode('utf-8')).hexdigest()
            token = self.prefix + digest[: self.digits]
            owner = self._used.get(token)
            if owner is None:
                self._used[token] = entity_id
                self._tokens[entity_id] = token
                return token
            if owner == entity_id:
                self._tokens[entity_id] = token
                return token
            attempt += 1


# --------------------------------------------------------------------------
# fills
# --------------------------------------------------------------------------

# index positions inside the per-fill tuple
(F_ID, F_DATE, F_VENUE, F_ENTITY, F_ACCT, F_INS, F_LEI, F_ASSET, F_QTY,
 F_NOTIONAL, F_FEE, F_COMM, F_REBATE, F_MULT, F_UCN, F_UCD, F_SYMBOL) = range(17)


def base_account_ref(row, ref):
    """Normalise a venue's local account reference to a book account ref."""
    raw = row['account_ref'].strip()
    if raw.startswith('acct::'):
        return raw
    if ':' in raw:
        resolved = ref.resolve_venue_code(raw, row['trade_date'])
        if resolved is None:
            raise KeyError('unmapped venue account code %r' % (raw,))
        return resolved
    scope = (row.get('clearing_scope') or '').strip()
    book = scope.split('::')[-1] if scope else ''
    if not book:
        raise KeyError('cannot place local account code %r without a clearing scope' % (raw,))
    return 'acct::%s::%s' % (book, raw)


def load_fills(input_dir, ref, minter, rounding):
    paths = sorted(glob.glob(os.path.join(input_dir, 'fills', '*.csv')))
    if not paths:
        paths = sorted(glob.glob(os.path.join(input_dir, 'fills*.csv')))
    notional_mode, notional_dp = rounding['notional_usd']
    fee_mode, fee_dp = rounding['fee_usd']
    comm_mode, comm_dp = rounding['commission_usd']
    q_not = Decimal(1).scaleb(-notional_dp)
    q_fee = Decimal(1).scaleb(-fee_dp)
    not_scale = 10 ** notional_dp
    fee_scale = 10 ** fee_dp
    comm_scale = 10 ** comm_dp
    comm_den = not_scale * 10000

    fills = []
    append = fills.append
    for path in paths:
        with open(path, 'r', encoding='utf-8-sig', newline='') as fh:
            for row in csv.DictReader(fh):
                date = intern(row['trade_date'])
                venue = intern(row['venue'])
                symbol = intern(row['symbol'])
                instrument = ref.instrument_for_symbol(symbol)
                mult, asset = ref.contract_terms(instrument, date)
                fx = ref.fx_rate(asset, date)
                qty = int(row['quantity'])
                side = row['side'].strip().upper()
                if side in ('SELL', 'S', 'SHORT'):
                    signed_qty = -qty
                elif side in ('BUY', 'B', 'LONG'):
                    signed_qty = qty
                else:
                    raise ValueError('unknown side %r' % (row['side'],))
                price = Decimal(row['price'])
                gross = Decimal(signed_qty) * mult * price * fx
                notional = int(gross.quantize(q_not, rounding=DEC_MODE[notional_mode]) * not_scale)
                fee = rescale(
                    int((Decimal(row['fee']) * fx).quantize(
                        q_fee, rounding=DEC_MODE[fee_mode]) * fee_scale),
                    fee_dp, notional_dp, fee_mode)

                abs_notional = -notional if notional < 0 else notional
                bands = eff_pick(ref.fee_schedule.get(venue, []), date)
                bps = 0
                if bands:
                    for min_cents, band_bps in bands:
                        if min_cents <= abs_notional:
                            bps = band_bps
                        else:
                            break
                if bps:
                    commission = rescale(
                        round_ratio(abs_notional * bps * comm_scale, comm_den, comm_mode),
                        comm_dp, notional_dp, comm_mode)
                else:
                    commission = 0

                account_ref = ref.resolve_mergers(base_account_ref(row, ref), date)
                entity = ref.entity_of(account_ref)
                token = minter.token(entity)

                pn, pd = price.as_integer_ratio()
                fn, fd = fx.as_integer_ratio()
                append((row['fill_id'], date, venue, token, account_ref, instrument,
                        intern(row['counterparty_lei']), asset, signed_qty, notional, fee,
                        commission, 0, mult, pn * fn, pd * fd, symbol))
    return fills


DEC_MODE = {
    'half_even': ROUND_HALF_EVEN,
    'up': ROUND_UP,
    'half_up': ROUND_HALF_UP,
    'down': ROUND_DOWN,
    'floor': ROUND_FLOOR,
    'ceiling': ROUND_CEILING,
}


def apply_rebates(fills, ref, policy, rounding):
    cfg = policy.get('rebate') or {}
    window_days = int(cfg.get('window_days', 30))
    include_trade_date = bool(cfg.get('window_includes_trade_date', False))
    mode, dp = rounding['rebate_usd']
    scale = 10 ** dp
    not_scale = 10 ** rounding['notional_usd'][1]

    import datetime as _dt

    def ordinal(d):
        y, m, dd = d.split('-')
        return _dt.date(int(y), int(m), int(dd)).toordinal()

    per_venue = {}
    for f in fills:
        volume = per_venue.setdefault(f[F_VENUE], {})
        o = ordinal(f[F_DATE])
        n = f[F_NOTIONAL]
        volume[o] = volume.get(o, 0) + (-n if n < 0 else n)

    prefix = {}
    for venue, volume in per_venue.items():
        days = sorted(volume)
        cum = [0]
        total = 0
        for d in days:
            total += volume[d]
            cum.append(total)
        prefix[venue] = (days, cum)

    tier_cache = {}
    for idx, f in enumerate(fills):
        venue = f[F_VENUE]
        tiers = ref.rebate_tiers.get(venue)
        if not tiers:
            continue
        o = ordinal(f[F_DATE])
        hi_day = o if include_trade_date else o - 1
        lo_day = hi_day - window_days + 1
        days, cum = prefix[venue]
        hi = bisect.bisect_right(days, hi_day)
        lo = bisect.bisect_left(days, lo_day)
        basis = cum[hi] - cum[lo]
        key = (venue, basis)
        bps = tier_cache.get(key)
        if bps is None:
            bps = 0
            for min_cents, tier_bps in tiers:
                if min_cents <= basis:
                    bps = tier_bps
                else:
                    break
            tier_cache[key] = bps
        if not bps:
            continue
        n = f[F_NOTIONAL]
        abs_notional = -n if n < 0 else n
        rebate = rescale(round_ratio(abs_notional * bps * scale, not_scale * 10000, mode),
                         dp, rounding['notional_usd'][1], mode)
        fills[idx] = f[:F_REBATE] + (rebate,) + f[F_REBATE + 1:]


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

class ReportWriter(object):
    def __init__(self, path, columns):
        self.fh = open(path, 'w', encoding='utf-8', newline='')
        self.writer = csv.writer(self.fh, lineterminator='\n')
        self.writer.writerow(columns)

    def rows(self, rows):
        self.writer.writerows(rows)

    def close(self):
        self.fh.close()


# --------------------------------------------------------------------------
# lots
# --------------------------------------------------------------------------

class Lot(object):
    __slots__ = ('pos', 'avg', 'layers', 'realised', 'fifo', 'applied')

    def __init__(self):
        self.pos = 0
        self.avg = Fraction(0)
        self.layers = []          # [qty (abs), unit cost] in FIFO order
        self.realised = 0         # rounded cents
        self.fifo = 0             # rounded cents
        self.applied = 0          # count of corporate actions already applied


def parse_realise_round(policy, dp_default):
    """`lots.realise_round` is prose: 'half_even dp 2 per reducing fill, summed'."""
    text = str((policy.get('lots') or {}).get('realise_round', '')).lower()
    mode = 'half_even'
    for candidate in ('half_even', 'half_up', 'up', 'down'):
        if candidate in text:
            mode = candidate
            break
    dp = dp_default
    parts = text.replace(',', ' ').split()
    for i, part in enumerate(parts):
        if part == 'dp' and i + 1 < len(parts):
            try:
                dp = int(parts[i + 1])
            except ValueError:
                pass
    return mode, dp


def run_lots(fills, ref, dp_money, dp_cost, realise_mode='half_even'):
    money_scale = 10 ** dp_money
    cost_scale = 10 ** dp_cost
    groups = {}
    for f in fills:
        groups.setdefault((f[F_ENTITY], f[F_INS]), []).append(f)

    out = []
    for (token, instrument), members in groups.items():
        members.sort(key=lambda f: (f[F_DATE], f[F_ID]))
        actions = ref.corporate_actions.get(instrument, ())
        lot = Lot()

        def apply_actions(upto):
            while lot.applied < len(actions) and actions[lot.applied][0] <= upto:
                ratio = actions[lot.applied][1]
                lot.pos *= ratio
                if lot.avg:
                    lot.avg = lot.avg / ratio
                for layer in lot.layers:
                    layer[0] *= ratio
                    layer[1] = layer[1] / ratio
                lot.applied += 1

        for f in members:
            apply_actions(f[F_DATE])
            qty = f[F_QTY]
            if qty == 0:
                continue
            unit = Fraction(f[F_UCN], f[F_UCD])
            mult = f[F_MULT]
            pos = lot.pos
            if pos == 0:
                lot.pos = qty
                lot.avg = unit
                lot.layers = [[abs(qty), unit]]
                continue
            if (qty > 0) == (pos > 0):
                # increase
                total = abs(pos) + abs(qty)
                lot.avg = (lot.avg * abs(pos) + unit * abs(qty)) / total
                lot.pos = pos + qty
                lot.layers.append([abs(qty), unit])
                continue
            # reduce / close / cross zero
            sign = 1 if pos > 0 else -1
            closed = min(abs(pos), abs(qty))

            gain = (unit - lot.avg) * closed * mult * sign
            lot.realised += round_ratio(gain.numerator * money_scale, gain.denominator,
                                        realise_mode)

            remaining = closed
            fifo_gain = Fraction(0)
            while remaining > 0 and lot.layers:
                layer = lot.layers[0]
                take = layer[0] if layer[0] <= remaining else remaining
                fifo_gain += (unit - layer[1]) * take * mult * sign
                layer[0] -= take
                remaining -= take
                if layer[0] == 0:
                    lot.layers.pop(0)
            lot.fifo += round_ratio(fifo_gain.numerator * money_scale,
                                    fifo_gain.denominator, realise_mode)

            new_pos = pos + qty
            lot.pos = new_pos
            if new_pos == 0:
                lot.avg = Fraction(0)
                lot.layers = []
            elif (new_pos > 0) != (pos > 0):
                # crossed through zero: reopen the remainder at the fill's cost
                lot.avg = unit
                lot.layers = [[abs(new_pos), unit]]

        apply_actions(ref.report_date)
        if lot.pos == 0:
            avg_scaled = 0
        else:
            avg = lot.avg
            avg_scaled = round_ratio(avg.numerator * cost_scale, avg.denominator, 'half_even')
        out.append((token, instrument, str(lot.pos), fmt_scaled(avg_scaled, dp_cost),
                    fmt_scaled(lot.realised, dp_money), fmt_scaled(lot.fifo, dp_money)))
    return out


# --------------------------------------------------------------------------
# interest
# --------------------------------------------------------------------------

def run_interest(fills, ref, dp_money):
    import datetime as _dt

    scale = 10 ** dp_money
    start = _dt.date(*[int(x) for x in ref.period_start.split('-')])
    end = _dt.date(*[int(x) for x in ref.report_date.split('-')])

    moves = {}
    entities = {}
    for f in fills:
        token = f[F_ENTITY]
        entities[token] = True
        delta = -f[F_NOTIONAL] - f[F_FEE] - f[F_COMM] + f[F_REBATE]
        if delta:
            moves.setdefault(token, {}).setdefault(f[F_DATE], 0)
            moves[token][f[F_DATE]] += delta

    days = []
    cur = start
    while cur < end:
        days.append((cur.isoformat(), ref.debit_rate_bps(cur.isoformat())))
        cur += _dt.timedelta(days=1)

    out = []
    for token in entities:
        by_date = moves.get(token, {})
        num = 0
        den = scale  # balance == num/den dollars; den stays a multiple of scale
        total_moves = 0
        for date, bps in days:
            delta = by_date.get(date)
            if delta:
                num += delta * (den // scale)
                total_moves += delta
            if num < 0 and bps:
                num *= 3650000 + bps
                den *= 3650000
        closing = round_ratio(num * scale, den, 'half_even')
        accrued_num = num - total_moves * (den // scale)
        accrued = round_ratio(accrued_num * scale, den, 'half_even')
        out.append((token, fmt_scaled(closing, dp_money), fmt_scaled(accrued, dp_money)))
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def figure_rounding(policy):
    figs = policy.get('figures') or {}
    out = {}
    for name, spec in figs.items():
        if not isinstance(spec, dict):
            continue
        if 'round' in spec:
            out[name] = (str(spec['round']), int(spec.get('dp', 2)))
    out.setdefault('notional_usd', ('half_even', 2))
    out.setdefault('fee_usd', ('up', 2))
    out.setdefault('commission_usd', ('up', 2))
    out.setdefault('rebate_usd', ('up', 2))
    out.setdefault('haircut_usd', ('half_even', 2))
    out.setdefault('initial_margin_usd', ('half_even', 2))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description='Clearing reconciliation')
    parser.add_argument('input_dir')
    parser.add_argument('--policy', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seed', default='0')
    parser.add_argument('--max-memory', dest='max_memory', default=None)
    args = parser.parse_args(argv)

    parse_memory(args.max_memory)  # accepted and validated; the run is sized for it

    policy = load_policy(args.policy)
    reports = policy.get('reports') or {}
    rounding = figure_rounding(policy)
    dp_money = rounding['notional_usd'][1]
    money_scale = 10 ** dp_money

    ref = Reference(args.input_dir, policy, dp_money)
    minter = TokenMinter(policy, args.seed)

    fills = load_fills(args.input_dir, ref, minter, rounding)
    apply_rebates(fills, ref, policy, rounding)

    os.makedirs(args.output, exist_ok=True)

    def cols(name):
        spec = reports.get(name) or {}
        columns = spec.get('columns') or {}
        return list(columns.keys())

    def sort_keys(name):
        spec = reports.get(name) or {}
        keys = spec.get('sort') or []
        if isinstance(keys, str):
            keys = [keys]
        return [cols(name).index(k) for k in keys]

    def make_writer(name):
        return ReportWriter(os.path.join(args.output, name), cols(name))

    def emit(name, rows):
        keys = sort_keys(name)
        rows.sort(key=lambda r: tuple(r[i] for i in keys))
        writer = make_writer(name)
        writer.rows(rows)
        writer.close()

    snapshot_reports = ['positions.csv', 'fees.csv', 'exposure.csv',
                        'counterparty_exposure.csv', 'netting_exposure.csv',
                        'venue_summary.csv', 'margin.csv']
    writers = {}
    keys_for = {}
    for name in snapshot_reports:
        if name in reports:
            writers[name] = make_writer(name)
            keys_for[name] = sort_keys(name)

    def flush(name, rows):
        writer = writers.get(name)
        if writer is None:
            return
        keys = keys_for[name]
        rows.sort(key=lambda r: tuple(r[i] for i in keys))
        writer.rows(rows)

    haircut_mode, haircut_dp = rounding['haircut_usd']
    margin_mode, margin_dp = rounding['initial_margin_usd']

    for as_of in ref.snapshot_dates:
        positions = {}
        fees = {}
        exposure = {}
        cpty = {}
        netting = {}
        venues = {}
        margin = {}
        for f in fills:
            if f[F_DATE] > as_of:
                continue
            token = f[F_ENTITY]
            notional = f[F_NOTIONAL]
            abs_notional = -notional if notional < 0 else notional

            factor = ref.split_factor(f[F_INS], f[F_DATE], as_of)
            qty = f[F_QTY] * factor
            key = (token, f[F_INS])
            cur = positions.get(key)
            if cur is None:
                positions[key] = [qty, qty if qty >= 0 else -qty]
            else:
                cur[0] += qty
                cur[1] += qty if qty >= 0 else -qty

            key = (token, f[F_VENUE])
            cur = fees.get(key)
            if cur is None:
                fees[key] = [f[F_FEE], f[F_COMM], f[F_REBATE]]
            else:
                cur[0] += f[F_FEE]
                cur[1] += f[F_COMM]
                cur[2] += f[F_REBATE]

            asset = f[F_ASSET]
            key = (token, asset)
            cur = exposure.get(key)
            if cur is None:
                exposure[key] = [notional, abs_notional]
            else:
                cur[0] += notional
                cur[1] += abs_notional

            key = (ref.resolve_lei(f[F_LEI], as_of), asset)
            cur = cpty.get(key)
            if cur is None:
                cpty[key] = [notional, abs_notional]
            else:
                cur[0] += notional
                cur[1] += abs_notional

            key = (ref.netting_set(f[F_ACCT], as_of), asset)
            cur = netting.get(key)
            if cur is None:
                netting[key] = [notional, abs_notional]
            else:
                cur[0] += notional
                cur[1] += abs_notional

            cur = venues.get(f[F_VENUE])
            if cur is None:
                venues[f[F_VENUE]] = [1, abs_notional, f[F_FEE], f[F_COMM]]
            else:
                cur[0] += 1
                cur[1] += abs_notional
                cur[2] += f[F_FEE]
                cur[3] += f[F_COMM]

            key = (token, f[F_INS])
            cur = margin.get(key)
            if cur is None:
                margin[key] = abs_notional
            else:
                margin[key] = cur + abs_notional

        rows = []
        for (token, instrument), (net, gross) in positions.items():
            rows.append((as_of, token, ref.symbol_at(instrument, as_of), str(net), str(gross)))
        flush('positions.csv', rows)

        rows = []
        for (token, venue), (fee, comm, rebate) in fees.items():
            rows.append((as_of, token, venue, fmt_scaled(fee, dp_money),
                         fmt_scaled(comm, dp_money), fmt_scaled(rebate, dp_money)))
        flush('fees.csv', rows)

        rows = []
        for (token, asset), (net, gross) in exposure.items():
            bps = ref.haircut_bps(asset, as_of)
            if bps:
                haircut = rescale(
                    round_ratio(gross * bps * (10 ** haircut_dp), money_scale * 10000,
                                haircut_mode),
                    haircut_dp, dp_money, haircut_mode)
            else:
                haircut = 0
            rows.append((as_of, token, asset, fmt_scaled(net, dp_money),
                         fmt_scaled(gross, dp_money), fmt_scaled(haircut, dp_money),
                         fmt_scaled(net - haircut, dp_money)))
        flush('exposure.csv', rows)

        rows = []
        for (lei, asset), (net, gross) in cpty.items():
            rows.append((as_of, lei, asset, fmt_scaled(net, dp_money),
                         fmt_scaled(gross, dp_money)))
        flush('counterparty_exposure.csv', rows)

        rows = []
        for (nset, asset), (net, gross) in netting.items():
            rows.append((as_of, nset, asset, fmt_scaled(net, dp_money),
                         fmt_scaled(gross, dp_money)))
        flush('netting_exposure.csv', rows)

        rows = []
        for venue, (count, gross, fee, comm) in venues.items():
            rows.append((as_of, venue, str(count), fmt_scaled(gross, dp_money),
                         fmt_scaled(fee, dp_money), fmt_scaled(comm, dp_money)))
        flush('venue_summary.csv', rows)

        totals = {}
        for (token, instrument), gross in margin.items():
            bps = ref.margin_bps(instrument, as_of)
            if bps:
                amount = rescale(
                    round_ratio(gross * bps * (10 ** margin_dp), money_scale * 10000,
                                margin_mode),
                    margin_dp, dp_money, margin_mode)
            else:
                amount = 0
            totals[token] = totals.get(token, 0) + amount
        rows = [(as_of, token, fmt_scaled(amount, dp_money))
                for token, amount in totals.items()]
        flush('margin.csv', rows)

    for writer in writers.values():
        writer.close()

    # ---- attribution ------------------------------------------------------
    if 'attribution.csv' in reports:
        rows = [(f[F_ID], f[F_ENTITY], f[F_SYMBOL], ref.resolve_lei(f[F_LEI], f[F_DATE]),
                 fmt_scaled(f[F_NOTIONAL], dp_money)) for f in fills]
        emit('attribution.csv', rows)

    # ---- lots -------------------------------------------------------------
    if 'lots.csv' in reports:
        dp_cost = 6
        spec = (reports['lots.csv'].get('columns') or {})
        for col, kind in spec.items():
            if str(kind).startswith('figure_') and str(kind)[7:].endswith('dp'):
                try:
                    dp_cost = int(str(kind)[7:-2])
                except ValueError:
                    pass
        realise_mode, realise_dp = parse_realise_round(policy, dp_money)
        emit('lots.csv', run_lots(fills, ref, realise_dp, dp_cost, realise_mode))

    # ---- financing --------------------------------------------------------
    if 'financing.csv' in reports:
        carrying = set()
        for f in fills:
            carrying.add(f[F_ACCT])
        eligible = [a for a in carrying if a in ref.allocation]
        eligible.sort(key=lambda a: (ref.allocation[a][0], a))
        remaining = ref.pool_cents
        cumulative = 0
        rows = []
        for account in eligible:
            _, priority, cap = ref.allocation[account]
            take = cap if cap < remaining else remaining
            if take < 0:
                take = 0
            remaining -= take
            cumulative += take
            rows.append((account, priority, fmt_scaled(cap, dp_money),
                         fmt_scaled(take, dp_money), fmt_scaled(cumulative, dp_money)))
        emit('financing.csv', rows)

    # ---- interest ---------------------------------------------------------
    if 'interest.csv' in reports:
        emit('interest.csv', run_interest(fills, ref, dp_money))

    return 0


if __name__ == '__main__':
    sys.exit(main())
