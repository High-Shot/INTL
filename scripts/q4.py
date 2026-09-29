#!/usr/bin/env python3
"""
Cerakote Q4 Inventory Projection: raw -> data/q4/<WEEK>.json

Reads data/raw/<WEEK>/q4/
  h10_inventory.json   Helium10 get_inventory_values, FBA, all five sellers           REQUIRED
  daily_*.json         Helium10 get_sales_velocity, day buckets, FBA + FBM, last 120 complete days   REQUIRED
  ly_weekly_*.json     Helium10 get_sales_velocity, week buckets, 2025-08-25..2026-01-04, NA sellers
                       (static; lives in data/q4_ref/, a copy in the week folder overrides it)
  pnl90.json           Helium10 get_product_profit_and_loss_summary, asin level, 90d, USD          REQUIRED
Plus data/snapshots/*.json (tracker) to date when a product went out of stock.

Rules (owner-set 2026-09-28):
  Floor   = 8 weeks (56 days) of forecast demand available at Amazon, every product, every market.
  Target  = floor x 1.2 = 67.2 days (20% buffer for slow check-in). Shipments are sized to the target.
  Demand  = in-stock velocity (last 30 in-stock days, FBA + FBM units) x last-year weekly seasonal index.
            Index = LY units in the matching week (364 days back) / LY average week 2025-09-01..09-22.
            Fallback order when an ASIN has no clean LY history: same ASIN's US index, then brand index.
            Prime Big Deal Days (Oct 6-7), BF/CM and the holiday run are carried by the LY curve.
  Lost    = running total since the product went out: base velocity x days out - units still sold (FBM).
  Capacity limits are ignored by design.
Usage: python3 scripts/q4.py 2026-W40 [--as-of 2026-09-28]
"""
import json, sys, os, glob, math, statistics, datetime as dt
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize import ACCOUNTS, ACC, BY_SELLER_MKT, POOL_LEAD, BRANDS, short_name  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOOR_DAYS = 56.0
BUFFER = 1.20
TARGET_DAYS = FLOOR_DAYS * BUFFER          # 67.2
BASE_DAYS = 30                              # in-stock days used for base velocity
LOOKBACK_MAX = 90                           # how far back to look for 30 in-stock days
ZERO_RUN_SIGMA = 5.0                        # a zero-sale run is a stockout when mean x run length >= 5 (P < 1%)
LY_OFFSET = 364                             # 2026 week <-> 2025 week, keeps weekdays and PBDD/BF/CM aligned
LY_BASE_WEEKS = ['2025-09-01', '2025-09-08', '2025-09-15', '2025-09-22']
LY_MIN_BASE_WEEKLY = 7                      # same bar as the tracker's event-lift rule
INDEX_CLIP = (0.5, 3.0)
LY_STOCKOUT_RATIO = 0.30                    # a LY week under 30% of its Sept base = LY stockout / no data: use the fallback curve
Q4_END = dt.date(2026, 12, 31)
EVENTS = [
    {'key': 'pbdd', 'name': 'Prime Big Deal Days', 'start': '2026-10-06', 'end': '2026-10-07'},
    {'key': 'bf', 'name': 'Black Friday', 'start': '2026-11-27', 'end': '2026-11-27'},
    {'key': 'cm', 'name': 'Cyber Monday', 'start': '2026-11-30', 'end': '2026-11-30'},
    {'key': 'hol', 'name': 'Holiday shopping', 'start': '2026-12-01', 'end': '2026-12-24'},
]
IN_SCOPE = {a['code'] for a in ACCOUNTS}


def load_rows(q4dir, pattern):
    rows = []
    for fp in sorted(glob.glob(os.path.join(q4dir, pattern))):
        with open(fp) as f:
            rows += json.load(f)['data']['rows']
    return rows


def monday(d):
    return d - dt.timedelta(days=d.weekday())


def zero_runs(series, eps=0.0):
    """[(start_idx, length)] of consecutive days at or below eps (stray sales inside an outage count as zero)."""
    runs, i = [], 0
    while i < len(series):
        if series[i] <= eps:
            j = i
            while j < len(series) and series[j] <= eps:
                j += 1
            runs.append((i, j - i))
            i = j
        else:
            i += 1
    return runs


def in_stock_velocity(days, fba, tot, end_idx):
    """Base velocity from the last BASE_DAYS in-stock days ending at end_idx (inclusive).
    A day is 'out' when it sits in a run of zero FBA sales long enough to be a stockout, not noise."""
    start = max(0, end_idx - LOOKBACK_MAX + 1)
    win_fba = fba[start:end_idx + 1]
    if not win_fba:
        return 0.0, 0, 0
    mean = sum(win_fba) / len(win_fba)
    out = set()
    if mean > 0:
        for s, L in zero_runs(win_fba, 0.1 * mean if mean >= 2 else 0.0):
            if mean * L >= ZERO_RUN_SIGMA:
                out.update(range(start + s, start + s + L))
        kept = [i for i in range(start, end_idx + 1) if i not in out]
        mean2 = sum(fba[i] for i in kept) / len(kept) if kept else 0
        if mean2 > mean * 1.05:  # second pass with the cleaner mean
            out = set()
            for s, L in zero_runs(win_fba, 0.1 * mean2 if mean2 >= 2 else 0.0):
                if mean2 * L >= ZERO_RUN_SIGMA:
                    out.update(range(start + s, start + s + L))
    picked = []
    i = end_idx
    while i >= start and len(picked) < BASE_DAYS:
        if i not in out:
            picked.append(i)
        i -= 1
    if not picked:
        return 0.0, 0, len(out)
    return sum(tot[i] for i in picked) / len(picked), len(picked), len(out)


def main():
    week = sys.argv[1]
    as_of = dt.date.fromisocalendar(int(week[:4]), int(week.split('-W')[1]), 1)
    if '--as-of' in sys.argv:
        as_of = dt.date.fromisoformat(sys.argv[sys.argv.index('--as-of') + 1])
    q4dir = os.path.join(ROOT, 'data', 'raw', week, 'q4')

    # ---------- inventory (FBA) ----------
    inv, inv_skus = {}, defaultdict(list)
    for r in load_rows(q4dir, 'h10_inventory*.json'):
        if r.get('fulfillment_type') != 'FBA':
            continue
        code = BY_SELLER_MKT.get((r.get('seller_id'), r['marketplace']))
        if code not in IN_SCOPE:
            continue
        i_ = r.get('inventory', {})
        keys = ['inbound_working', 'inbound_shipped', 'inbound_received']
        has_inb = any(k in i_ for k in keys)
        if has_inb:
            inbound = sum(int(i_.get(k) or 0) for k in keys)
        elif 'inbound_quantity' in i_:
            inbound = int(i_.get('inbound_quantity') or 0)
        else:
            inbound = None
        rec = {'available': int(i_.get('available') or 0), 'inbound': inbound,
               'sku': r.get('sku'), 'name': short_name(r.get('product_name')), 'image': r.get('image_url')}
        inv_skus[(code, r['asin'])].append(rec)
    # Several FBA SKUs on one ASIN. NA: separate stock, sum them. UK/EU/SA/AU: Helium10 repeats the same
    # pool-level count on every SKU, so identical counts are one number, not N copies.
    for k, recs in inv_skus.items():
        rec = dict(recs[0])
        rec['skus'] = [x['sku'] for x in recs]
        rec['has_inv'] = True
        same = len({(x['available'], x['inbound']) for x in recs}) == 1
        if len(recs) > 1 and not (same and ACC[k[0]]['market'] not in ('US', 'CA')):
            rec['available'] = sum(x['available'] for x in recs)
            ib = [x['inbound'] for x in recs if x['inbound'] is not None]
            rec['inbound'] = sum(ib) if ib else None
        inv[k] = rec

    # ---------- daily sales ----------
    daily_rows = load_rows(q4dir, 'daily_*.json')
    all_days = sorted({d for r in daily_rows for d in (r.get('sales_velocity') or {}).get('values', {})})
    data_end = dt.date.fromisoformat(all_days[-1])
    didx = {d: i for i, d in enumerate(all_days)}
    fba_d = defaultdict(lambda: [0.0] * len(all_days))
    tot_d = defaultdict(lambda: [0.0] * len(all_days))
    has_fba_sku, names = set(), {}
    for r in daily_rows:
        code = BY_SELLER_MKT.get((r.get('seller_id'), r['marketplace']))
        if code not in IN_SCOPE:
            continue
        k = (code, r['asin'])
        ft = (r.get('fulfillment_type') or '').upper()
        vals = r['sales_velocity']['values']
        for d, v in vals.items():
            v = float(v or 0)
            tot_d[k][didx[d]] += v
            if ft == 'FBA':
                fba_d[k][didx[d]] += v
        if ft == 'FBA':
            has_fba_sku.add(k)
        names.setdefault(k, (short_name(r.get('product_name')), r.get('image_url'), r.get('sku')))

    # ---------- price (USD per unit, 90d) ----------
    price = {}
    for r in load_rows(q4dir, 'pnl90*.json'):
        code = BY_SELLER_MKT.get((r.get('seller_id'), r['marketplace']))
        m = r.get('metrics') or {}
        u, s = m.get('units_sold') or 0, m.get('sales') or 0
        if code and u > 0 and s > 0:
            price[(code, r['asin'])] = s / u

    # ---------- last-year seasonal index ----------
    ly = defaultdict(lambda: defaultdict(float))   # (market, asin) -> week -> units
    ly_brand = defaultdict(lambda: defaultdict(float))
    ly_dir = q4dir if glob.glob(os.path.join(q4dir, 'ly_weekly_*.json')) else os.path.join(ROOT, 'data', 'q4_ref')
    for r in load_rows(ly_dir, 'ly_weekly_*.json'):
        code = BY_SELLER_MKT.get((r.get('seller_id'), r['marketplace']))
        if code not in IN_SCOPE:
            continue
        for w, v in r['sales_velocity']['values'].items():
            ly[(code, r['asin'])][w] += float(v or 0)
            if ACC[code]['market'] == 'US':
                ly_brand[ACC[code]['brand']][w] += float(v or 0)

    def curve(weeks):
        """week-string -> units dict  ->  {2025 week: index} or None when history is not clean."""
        base = [weeks.get(w, 0) for w in LY_BASE_WEEKS]
        avg = sum(base) / len(base)
        if min(base) <= 0 or avg < LY_MIN_BASE_WEEKLY:
            return None
        return {w: v / avg for w, v in weeks.items() if v / avg >= LY_STOCKOUT_RATIO}

    brand_curve = {b: curve(ws) for b, ws in ly_brand.items()}

    def index_for(code, asin, day):
        """Seasonal index for a 2026 calendar day, with its basis."""
        lyw = monday(day - dt.timedelta(days=LY_OFFSET)).isoformat()
        brand = ACC[code]['brand']
        for key, basis in (((code, asin), 'own LY'), (('CC_US' if brand == 'CC' else code, asin), 'US LY')):
            c = curve_cache.get(key)
            if c is None and key not in curve_cache:
                c = curve_cache[key] = curve(ly[key]) if key in ly else None
            if c and lyw in c:
                v = c[lyw]
                if v > 0:
                    return min(max(v, INDEX_CLIP[0]), INDEX_CLIP[1]), basis
        bc = brand_curve.get(brand)
        if bc and lyw in bc:
            return min(max(bc[lyw], INDEX_CLIP[0]), INDEX_CLIP[1]), 'brand LY'
        return 1.0, 'flat'
    curve_cache = {}

    # ---------- tracker snapshots: last date each item was seen with stock ----------
    seen_stock = {}
    for fp in sorted(glob.glob(os.path.join(ROOT, 'data', 'snapshots', '*.json'))):
        with open(fp) as f:
            s = json.load(f)
        d = dt.date.fromisoformat(s['generated_at'][:10])
        for code, a in s.get('accounts', {}).items():
            for row in a.get('inventory', []):
                if (row.get('available') or 0) > 0:
                    k = (code, row['asin'])
                    seen_stock[k] = max(seen_stock.get(k, d), d)

    # ---------- listings that cannot sell (removed / restricted), from the latest tracker snapshot ----------
    BLOCK_TYPES = ('Listing Removed', 'Restricted Product', 'Ip Complaint')
    blocked = {}
    snaps = sorted(glob.glob(os.path.join(ROOT, 'data', 'snapshots', '*.json')))
    if snaps:
        with open(snaps[-1]) as f:
            for it in json.load(f).get('items', []):
                if it.get('type') == 'account' and it.get('asin') and it.get('name') in BLOCK_TYPES \
                        and it.get('severity') in ('CRITICAL', 'URGENT'):
                    blocked[(it['account'], it['asin'])] = f"{it['name']}: {(it.get('reason') or '')[:120]}"

    # ---------- per market-ASIN ----------
    universe = set(inv) | {k for k in has_fba_sku if sum(fba_d[k][-LOOKBACK_MAX:]) > 0}
    horizon_days = int((Q4_END - as_of).days + TARGET_DAYS + max(POOL_LEAD.values()) + 7)
    items = {}
    for k in sorted(universe):
        code, asin = k
        # No FBA row in Helium10 = Amazon holds nothing for the SKU: SKUs with a shipment in flight keep their row
        # (0 available + inbound > 0 shows up), so a missing row is read as 0 inbound, flagged as inferred.
        i_ = inv.get(k, {'available': 0, 'inbound': 0, 'skus': [], 'sku': None, 'name': None, 'image': None, 'has_inv': False})
        nm = names.get(k, (None, None, None))
        fba, tot = fba_d[k], tot_d[k]
        avail, inbound = i_['available'], i_['inbound']
        oos = avail <= 0
        # out-of-stock start: day after last FBA sale, never earlier than the last snapshot that saw stock
        oos_start, oos_basis = None, None
        last_sale = max((i for i, v in enumerate(fba) if v > 0), default=None)
        if oos:
            cands = []
            if last_sale is not None:
                cands.append(dt.date.fromisoformat(all_days[last_sale]) + dt.timedelta(days=1))
            if k in seen_stock:
                cands.append(seen_stock[k] + dt.timedelta(days=1))
            if cands:
                oos_start = max(cands)
                oos_basis = 'last FBA sale' if last_sale is not None and oos_start == cands[0] else 'tracker snapshot'
            else:
                oos_start, oos_basis = dt.date.fromisoformat(all_days[0]), 'over 120 days'
        def base_for(start_date):
            e = len(all_days) - 1
            if oos and start_date is not None:
                e = min(e, didx.get((start_date - dt.timedelta(days=1)).isoformat(), -1))
            return in_stock_velocity(all_days, fba, tot, e) if e >= 0 else (0.0, 0, 0)
        v, instock_days, excluded = base_for(oos_start)
        # A stray sale (return resold, reserved unit freed) inside a long outage should not reset the clock:
        # for sellers of 2+/day, the outage starts where FBA sales last ran at 10%+ of base velocity.
        if oos and v >= 2 and last_sale is not None:
            i = len(all_days) - 1
            while i >= 0 and fba[i] < 0.1 * v:
                i -= 1
            alt = dt.date.fromisoformat(all_days[i]) + dt.timedelta(days=1) if i >= 0 else oos_start
            if k in seen_stock:
                alt = max(alt, seen_stock[k] + dt.timedelta(days=1))
            if alt < oos_start:
                oos_start, oos_basis = alt, 'sales collapse'
                v, instock_days, excluded = base_for(oos_start)
        v30_raw = sum(tot[-30:]) / 30.0
        if v <= 0:
            continue
        items[k] = {
            'code': code, 'asin': asin, 'sku': i_.get('sku') or nm[2], 'skus': i_.get('skus') or [nm[2]],
            'name': i_.get('name') or nm[0] or asin, 'image': i_.get('image') or nm[1],
            'available': avail, 'inbound': inbound, 'inbound_known': inbound is not None, 'has_inv': i_.get('has_inv', False),
            'base_vel': v, 'raw_vel30': v30_raw, 'instock_days': instock_days, 'excluded_days': excluded,
            'oos': oos, 'oos_start': oos_start, 'oos_basis': oos_basis, 'price': price.get(k),
            'units_30d': sum(tot[-30:]),
        }

    # ---------- EU pool: same rule as the tracker (identical available count in every member market) ----------
    pool_members = defaultdict(list)
    for a in ACCOUNTS:
        pool_members[a['pool']].append(a['code'])
    groups = defaultdict(list)
    for (code, asin), it in items.items():
        pl = ACC[code]['pool']
        if len(pool_members[pl]) > 1:
            groups[(pl, asin)].append(it)
    pooled = {}
    for (pl, asin), its in groups.items():
        rep_ = [x for x in its if x['has_inv']]
        if not rep_ and len(its) > 1:   # out everywhere, no stock row in any member: one shared pool at zero
            rep_ = [x for x in its if (x['code'], asin) not in blocked]
            if len(rep_) > 1:
                pooled[(pl, asin)] = rep_
            continue
        if len(rep_) > 1 and len({x['available'] for x in rep_}) == 1:
            extra = [x for x in its if not x['has_inv'] and (x['code'], asin) not in blocked]
            for x in extra:
                x['available'], x['inbound'], x['inbound_known'] = rep_[0]['available'], rep_[0]['inbound'], rep_[0]['inbound_known']
                x['oos'] = rep_[0]['oos']
                if not x['oos']:
                    x['oos_start'] = x['oos_basis'] = None
            pooled[(pl, asin)] = rep_ + extra

    def demand_series(its, start, n):
        """Forecast daily units for n days from start, summed over member items, plus index basis per item."""
        out = [0.0] * n
        for it in its:
            for j in range(n):
                d = start + dt.timedelta(days=j)
                ix, _ = index_for(it['code'], it['asin'], d)
                out[j] += it['base_vel'] * ix
        return out

    rows = []
    done = set()
    units_needed_by_week = defaultdict(float)

    def build_row(key_code, asin, its, pooled_flag):
        lead = POOL_LEAD.get(ACC[its[0]['code']]['pool'], 30)
        avail = its[0]['available'] if pooled_flag else sum(x['available'] for x in its)
        inb_known = all(x['inbound_known'] for x in its)
        inb_inferred = any(not x.get('has_inv') for x in its)
        inbound = (its[0]['inbound'] or 0) if pooled_flag else sum((x['inbound'] or 0) for x in its)
        base_vel = sum(x['base_vel'] for x in its)
        D = demand_series(its, as_of, horizon_days)
        cum = [0.0]
        for x in D:
            cum.append(cum[-1] + x)

        def dsum(a, b):
            """forecast units from day offset a to b (fractional b ok)."""
            a, b = max(0.0, a), min(float(horizon_days), b)
            if b <= a:
                return 0.0
            ia, ib = int(a), int(b)
            s = cum[ib] - cum[ia]
            if ib < horizon_days:
                s += D[ib] * (b - ib)
            return s

        stock = avail + inbound
        # days of cover (forward, seasonal)
        def cover(units, frm=0):
            j = frm
            while j < horizon_days and units >= D[j]:
                units -= D[j]
                j += 1
            return (j - frm) + (units / D[j] if j < horizon_days and D[j] > 0 else 0)
        cov_avail = cover(avail)
        cov_total = cover(stock)
        floor_units = dsum(0, FLOOR_DAYS)
        target_units = dsum(0, TARGET_DAYS)
        ship_now = max(0, math.ceil(dsum(0, lead + TARGET_DAYS) - stock))
        # Q4 schedule: weekly arrivals that keep TARGET_DAYS of cover at every week start through Dec 31
        schedule, arrived = [], 0.0
        wk = monday(as_of)
        while wk <= Q4_END:
            arrive_by = max(wk, as_of + dt.timedelta(days=lead))
            off = (arrive_by - as_of).days
            need = dsum(0, off) + dsum(off, off + TARGET_DAYS) - stock - arrived
            q = max(0, math.ceil(need))
            if q > 0:
                ship_by = arrive_by - dt.timedelta(days=lead)
                schedule.append({'arrive_by': arrive_by.isoformat(), 'ship_by': ship_by.isoformat(), 'units': q,
                                 'late': ship_by <= as_of})
                arrived += q
            wk += dt.timedelta(days=7)
        q4_units = int(sum(s['units'] for s in schedule))
        q4_demand = dsum(0, (Q4_END - as_of).days + 1)
        # projected shortfall if the first shipment only lands after the lead time
        runout = as_of + dt.timedelta(days=cov_total) if cov_total < horizon_days else None
        proj_short = max(0.0, dsum(0, lead) - stock)
        pr = [x['price'] for x in its if x['price']]
        unit_price = (sum(x['price'] * x['base_vel'] for x in its if x['price']) / sum(x['base_vel'] for x in its if x['price'])) if pr else None
        # running lost sales since out of stock (per member market, then summed)
        lost_units = 0.0
        oos_start = None
        oos_days = 0
        for x in its:
            if not x['oos'] or x['oos_start'] is None:
                continue
            k_ = (x['code'], x['asin'])
            days_out = (as_of - x['oos_start']).days
            sold = sum(tot_d[k_][didx[d]] for d in all_days if dt.date.fromisoformat(d) >= x['oos_start'])
            lost_units += max(0.0, x['base_vel'] * days_out - sold)
            oos_start = min(oos_start, x['oos_start']) if oos_start else x['oos_start']
            oos_days = max(oos_days, days_out)
        # index basis (first member) and event multipliers for display
        basis = index_for(its[0]['code'], asin, as_of + dt.timedelta(days=14))[1]
        ev = {}
        for e in EVENTS:
            d0 = dt.date.fromisoformat(e['start'])
            ev[e['key']] = round(sum(index_for(x['code'], asin, d0)[0] * x['base_vel'] for x in its) / base_vel, 2)
        oos_now = avail <= 0
        if oos_now:
            status = 'OUT'
        elif cov_avail < FLOOR_DAYS:
            status = 'BELOW_FLOOR'
        elif cov_avail < TARGET_DAYS:
            status = 'BELOW_TARGET'
        else:
            status = 'OK'
        late = any(s['late'] for s in schedule)
        m0 = its[0]
        code0 = key_code
        brand = ACC[m0['code']]['brand']
        blk = blocked.get((code0, asin)) or next((blocked[(x['code'], asin)] for x in its if (x['code'], asin) in blocked), None)
        if not blk:
            for s in schedule:
                units_needed_by_week[(code0, s['ship_by'])] += s['units']
        weekly = []
        wk = monday(as_of)
        while wk <= Q4_END:
            o = (wk - as_of).days
            weekly.append(round(dsum(max(0, o), o + 7)))
            wk += dt.timedelta(days=7)
        return {
            'id': f'{code0}|{asin}', 'account': code0, 'brand': brand,
            'label': (code0.split('_')[1] + ' ' + BRANDS[brand]['label']) if pooled_flag else ACC[code0]['label'],
            'markets': sorted(ACC[x['code']]['market'] for x in its), 'pooled': pooled_flag,
            'asin': asin, 'sku': m0['sku'], 'name': m0['name'], 'image': m0['image'],
            'available': avail, 'inbound': inbound, 'inbound_known': inb_known, 'inbound_inferred': inb_inferred,
            'base_vel': round(base_vel, 2), 'raw_vel30': round(sum(x['raw_vel30'] for x in its), 2),
            'instock_days': min(x['instock_days'] for x in its), 'low_sample': min(x['instock_days'] for x in its) < 14, 'excluded_days': max(x['excluded_days'] for x in its),
            'index_basis': basis, 'event_index': ev,
            'cover_avail': round(cov_avail, 1), 'cover_total': round(cov_total, 1),
            'runout': runout.isoformat() if runout else None,
            'floor_units': round(floor_units), 'target_units': round(target_units),
            'lead': lead, 'ship_now': ship_now, 'q4_units': q4_units, 'q4_demand': round(q4_demand),
            'schedule': schedule, 'late': late, 'weekly': weekly,
            'status': status, 'blocked': blk, 'oos_start': oos_start.isoformat() if oos_start else None, 'oos_days': oos_days,
            'oos_basis': next((x['oos_basis'] for x in its if x['oos_basis']), None),
            'lost_units': round(lost_units), 'price_usd': round(unit_price, 2) if unit_price else None,
            'lost_usd': round(lost_units * unit_price) if unit_price else None,
            'proj_short_units': round(proj_short),
            'proj_short_usd': round(proj_short * unit_price) if unit_price else None,
        }

    for (pl, asin), its in pooled.items():
        rows.append(build_row(pl, asin, its, True))
        for x in its:
            done.add((x['code'], asin))
    for k, it in items.items():
        if k in done:
            continue
        rows.append(build_row(it['code'], it['asin'], [it], False))

    SR = {'OUT': 0, 'BELOW_FLOOR': 1, 'BELOW_TARGET': 2, 'OK': 3}
    rows.sort(key=lambda r: (SR[r['status']], -(r['lost_usd'] or 0), r['cover_avail']))
    weeks = []
    wk = monday(as_of)
    while wk <= Q4_END:
        weeks.append(wk.isoformat())
        wk += dt.timedelta(days=7)

    def tot(key, f=lambda r: True):
        return sum((r[key] or 0) for r in rows if f(r))
    ok = lambda r: not r['blocked']
    accounts = []
    seen = []
    for r in rows:
        if r['account'] not in seen:
            seen.append(r['account'])
    order = [a['code'] for a in ACCOUNTS] + ['CC_EU']
    for code in sorted(seen, key=lambda c: order.index(c) if c in order else 99):
        rs = [r for r in rows if r['account'] == code]
        accounts.append({'code': code, 'label': rs[0]['label'], 'brand': rs[0]['brand'],
                         'lead': rs[0]['lead'], 'products': len(rs),
                         'out': sum(r['status'] == 'OUT' for r in rs),
                         'below_floor': sum(r['status'] == 'BELOW_FLOOR' for r in rs),
                         'below_target': sum(r['status'] == 'BELOW_TARGET' for r in rs),
                         'ship_now': sum(r['ship_now'] for r in rs if ok(r)), 'q4_units': sum(r['q4_units'] for r in rs if ok(r)),
                         'blocked': sum(bool(r['blocked']) for r in rs),
                         'lost_usd': sum(r['lost_usd'] or 0 for r in rs if ok(r)),
                         'lost_hold_usd': sum(r['lost_usd'] or 0 for r in rs if not ok(r)),
                         'late': sum(r['late'] for r in rs if ok(r))})
    snap = {
        'week': week, 'as_of': as_of.isoformat(), 'sales_through': data_end.isoformat(),
        'generated_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'rules': {'floor_days': FLOOR_DAYS, 'buffer': BUFFER, 'target_days': round(TARGET_DAYS, 1),
                  'base_days': BASE_DAYS, 'lead_days': POOL_LEAD, 'ly_offset_days': LY_OFFSET,
                  'ly_base_weeks': LY_BASE_WEEKS, 'index_clip': INDEX_CLIP, 'events': EVENTS,
                  'currency': 'USD'},
        'weeks': weeks,
        'totals': {'products': len(rows), 'out': sum(r['status'] == 'OUT' for r in rows),
                   'below_floor': sum(r['status'] == 'BELOW_FLOOR' for r in rows),
                   'below_target': sum(r['status'] == 'BELOW_TARGET' for r in rows),
                   'late': sum(r['late'] for r in rows if ok(r)), 'blocked': sum(bool(r['blocked']) for r in rows),
                   'ship_now': tot('ship_now', ok), 'q4_units': tot('q4_units', ok),
                   'lost_usd': tot('lost_usd', ok), 'lost_hold_usd': tot('lost_usd', lambda r: not ok(r)), 'lost_unpriced': sum(1 for r in rows if r['lost_units'] and r['lost_usd'] is None),
                   'proj_short_usd': tot('proj_short_usd', ok)},
        'accounts': accounts,
        'ship_by': sorted([{'account': a, 'ship_by': d, 'units': round(u)} for (a, d), u in units_needed_by_week.items()],
                          key=lambda x: x['ship_by']),
        'rows': rows,
    }
    out_dir = os.path.join(ROOT, 'data', 'q4')
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f'{week}.json'), 'w') as f:
        json.dump(snap, f, separators=(',', ':'))
    t = snap['totals']
    print(f"{week}: {t['products']} products | out {t['out']} | below floor {t['below_floor']} | below target {t['below_target']} "
          f"| ship now {t['ship_now']:,} u | Q4 {t['q4_units']:,} u | lost ${t['lost_usd']:,} | late {t['late']}")


if __name__ == '__main__':
    main()
