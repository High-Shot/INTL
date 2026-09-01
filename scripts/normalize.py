#!/usr/bin/env python3
"""
Cerakote INTL Health Tracker: raw -> weekly snapshot.

Reads  data/raw/<WEEK>/
  h10_inventory.json    Helium10 get_inventory_values (FBA rows, all sellers)   REQUIRED
  si_inventory.csv      Scale Insights get_inventory_data, one row per market x ASIN  REQUIRED
  seller_feedback.csv   Helium10 get_seller_feedback, one row per seller x market      optional
  account_health.json   Seller Central notifications / AHR per market                   optional
  restock_recs.csv      Amazon FBA restock recommendations (market,asin,sku,rec_qty,rec_date) optional
Writes data/snapshots/<WEEK>.json

Usage: python3 scripts/normalize.py 2026-W36 [--generated 2026-09-01T12:00:00Z]
"""
import csv, json, sys, os, glob, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BRANDS = {
    'CC': {'name': 'Cerakote Auto',     'label': 'AUTO',   'site': 'https://cerakoteceramics.com/'},
    'CL': {'name': 'Cerakote Legacy',   'label': 'LEGACY', 'site': 'https://www.cerakote.com/'},
    'PP': {'name': 'Prismatic Powders', 'label': 'PRIS',   'site': 'https://www.prismaticpowders.com/'},
}
MKT = {
    'US': ('United States', '$'), 'CA': ('Canada', 'C$'), 'MX': ('Mexico', 'MX$'), 'UK': ('United Kingdom', '£'),
    'DE': ('Germany', '€'), 'FR': ('France', '€'), 'IT': ('Italy', '€'), 'ES': ('Spain', '€'), 'NL': ('Netherlands', '€'),
    'AE': ('UAE', 'AED'), 'SA': ('Saudi Arabia', 'SAR'), 'AU': ('Australia', 'A$'),
}
# One row per account tab. pool = FBA inventory pool (EU markets share one). launching = suppress stock alerts until first sales.
ACCOUNTS = [
    {'code': 'CC_US', 'brand': 'CC', 'market': 'US', 'seller': 'AOXMQPMOL1F1Y', 'pool': 'CC_US'},
    {'code': 'CC_CA', 'brand': 'CC', 'market': 'CA', 'seller': 'AOXMQPMOL1F1Y', 'pool': 'CC_CA'},
    {'code': 'CC_UK', 'brand': 'CC', 'market': 'UK', 'seller': 'A3BMUMIXNXIR6G', 'pool': 'CC_UK'},
    {'code': 'CC_DE', 'brand': 'CC', 'market': 'DE', 'seller': 'A3BMUMIXNXIR6G', 'pool': 'CC_EU'},
    {'code': 'CC_FR', 'brand': 'CC', 'market': 'FR', 'seller': 'A3BMUMIXNXIR6G', 'pool': 'CC_EU'},
    {'code': 'CC_IT', 'brand': 'CC', 'market': 'IT', 'seller': 'A3BMUMIXNXIR6G', 'pool': 'CC_EU'},
    {'code': 'CC_ES', 'brand': 'CC', 'market': 'ES', 'seller': 'A3BMUMIXNXIR6G', 'pool': 'CC_EU'},
    {'code': 'CC_NL', 'brand': 'CC', 'market': 'NL', 'seller': 'A3BMUMIXNXIR6G', 'pool': 'CC_EU'},
    {'code': 'CC_AE', 'brand': 'CC', 'market': 'AE', 'seller': 'A3BMUMIXNXIR6G', 'pool': 'CC_AE', 'launching': True},
    {'code': 'CC_SA', 'brand': 'CC', 'market': 'SA', 'seller': 'A3BMUMIXNXIR6G', 'pool': 'CC_SA'},
    {'code': 'CC_AU', 'brand': 'CC', 'market': 'AU', 'seller': 'A22UNGVVL3ZGDF', 'pool': 'CC_AU'},
    {'code': 'CL_US', 'brand': 'CL', 'market': 'US', 'seller': 'A1KUYEQ8RRQVVI', 'pool': 'CL_US'},
    {'code': 'PP_US', 'brand': 'PP', 'market': 'US', 'seller': 'A21D21T8B6U09C', 'pool': 'PP_US'},
]
for a in ACCOUNTS:
    a['name'], a['cur'] = MKT[a['market']]
    a['brand_name'] = BRANDS[a['brand']]['name']
    a['label'] = f"{a['market']} {BRANDS[a['brand']]['label']}"
    a.setdefault('launching', False)
ACC = {a['code']: a for a in ACCOUNTS}
BY_SELLER_MKT = {(a['seller'], a['market']): a['code'] for a in ACCOUNTS}
ORDER = [a['code'] for a in ACCOUNTS]
POOL_LEAD = {'CC_US': 14, 'CC_CA': 14, 'CL_US': 14, 'PP_US': 14,
             'CC_UK': 45, 'CC_EU': 45, 'CC_AE': 45, 'CC_SA': 45, 'CC_AU': 45}

# Thresholds (days of cover). Project rule: <14 days = URGENT, OOS = CRITICAL.
URGENT_DOC = 14
WATCH_DOC = 28
# Restock estimate (fallback when Amazon's recommendation is not loaded):
#   qty = velocity x (lead_time_days + REVIEW_COVER_DAYS) - available - inbound
# Lead times are ASSUMPTIONS as of 2026-09-01 (Barcus to correct): NA 14d, UK/EU/AU/SA 45d.
LEAD_TIME_DAYS = POOL_LEAD
REVIEW_COVER_DAYS = 30
TARGET_DOC = 60          # kept for reference; est now uses LEAD_TIME_DAYS + REVIEW_COVER_DAYS
UNFULFILLABLE_WATCH = 10
FEEDBACK_NEG_PCT_URGENT = 0.15
FEEDBACK_MIN_COUNT = 5

SEV_RANK = {'CRITICAL': 0, 'URGENT': 1, 'WATCH': 2, 'INFO': 3, 'OK': 4}


def fnum(v, default=None):
    if v is None or v == '':
        return default
    try:
        return float(v)
    except ValueError:
        return default


def short_name(title):
    """Compress an Amazon title to a readable product name."""
    t = (title or '').replace('CERAKOTE®', '').replace('CERAKOTE', '').replace('Cerakote®', '').replace('Cerakote', '')
    t = t.strip(' -–:,')
    for sep in [' - ', ' – ', ' — ', ', ', ' | ']:
        if sep in t:
            t = t.split(sep)[0]
    return t.strip()[:60] or title[:60]


def load_h10(week_dir):
    """H10 inventory rows keyed by (account_code, asin)."""
    p = os.path.join(week_dir, 'h10_inventory.json')
    out = {}
    files = [p] + sorted(glob.glob(os.path.join(week_dir, 'h10_inventory_*.json')))
    for fp in files:
        if not os.path.exists(fp):
            continue
        with open(fp) as f:
            d = json.load(f)
        for r in d['data']['rows']:
            if r.get('fulfillment_type') != 'FBA':
                continue
            code = BY_SELLER_MKT.get((r.get('seller_id'), r['marketplace']))
            if not code:
                continue
            inv = r.get('inventory', {})
            key = (code, r['asin'])
            inbound_keys = ['inbound_working', 'inbound_shipped', 'inbound_received']
            has_inbound = any(k in inv for k in inbound_keys)
            inbound = sum(int(inv.get(k) or 0) for k in inbound_keys) if has_inbound else None
            rec = {
                'asin': r['asin'], 'sku': r.get('sku'), 'name': short_name(r.get('product_name')),
                'title': r.get('product_name'), 'image': r.get('image_url'),
                'available': int(inv.get('available') or 0), 'inbound': inbound,
                'inbound_reported': has_inbound,
                'unfulfillable': int(inv.get('unfulfillable') or 0),
            }
            if key in out:
                if rec['available'] > out[key]['available']:
                    out[key].update({k: rec[k] for k in ('available', 'inbound', 'unfulfillable')})
                out[key]['skus'] = sorted(set(out[key].get('skus', [out[key]['sku']]) + [rec['sku']]))
            else:
                rec['skus'] = [rec['sku']]
                out[key] = rec
    return out


def load_h10_velocity(week_dir):
    """H10 get_sales_velocity (FBA, one month bucket) -> units in window keyed by (account_code, asin)."""
    p = os.path.join(week_dir, 'h10_velocity.json')
    out = {}
    if not os.path.exists(p):
        return out
    with open(p) as f:
        d = json.load(f)
    for r in d['data']['rows']:
        code = BY_SELLER_MKT.get((r.get('seller_id'), r['marketplace']))
        if not code:
            continue
        vals = (r.get('sales_velocity') or {}).get('values') or {}
        units = sum(float(v or 0) for v in vals.values())
        out[(code, r['asin'])] = out.get((code, r['asin']), 0) + units
    return out


def load_si(week_dir):
    p = os.path.join(week_dir, 'si_inventory.csv')
    out = {}
    if not os.path.exists(p):
        return out
    with open(p, newline='') as f:
        for r in csv.DictReader(f):
            code = r['market'] if '_' in r['market'] else 'CC_' + r['market']
            out[(code, r['asin'])] = {
                'name': (r.get('name') or '').strip() or None,
                'fba': fnum(r.get('fba')), 'transfer': fnum(r.get('transfer'), 0), 'si_inbound': fnum(r.get('inbound'), 0),
                'units30': fnum(r.get('units30'), 0), 'vel': fnum(r.get('vel'), 0), 'doc': fnum(r.get('doc')),
                'ad30': fnum(r.get('ad30'), 0), 'risk': r.get('risk'),
                'spending_low': str(r.get('spending_low', '')).lower() == 'true',
            }
    return out


def load_csv(week_dir, name):
    p = os.path.join(week_dir, name)
    if not os.path.exists(p):
        return []
    with open(p, newline='') as f:
        return list(csv.DictReader(f))


def load_json(week_dir, name):
    p = os.path.join(week_dir, name)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def classify(item, pool_vel, pool_doc):
    """Return (severity, reasons[]). Uses pooled velocity for EU pool markets."""
    reasons = []
    avail = item['available']
    inbound = item['inbound']
    inbound_known = item['inbound_reported']
    inb = inbound or 0
    vel = pool_vel if pool_vel is not None else item['vel']
    doc = pool_doc if pool_doc is not None else item['doc']
    selling = (item['units30'] or 0) > 0 or (vel or 0) > 0

    sev = 'OK'
    if avail <= 0 and inb == 0:
        if selling:
            sev = 'CRITICAL'; reasons.append('Out of stock, nothing inbound')
        else:
            sev = 'INFO'; reasons.append('Out of stock, no sales in 30d (dormant)')
    elif avail <= 0 and inb > 0:
        if selling:
            sev = 'URGENT'; reasons.append(f'Out of stock, {inb} inbound')
        else:
            sev = 'INFO'; reasons.append(f'Out of stock, {inb} inbound, no sales in 30d')
    elif doc is not None and doc < URGENT_DOC and inb == 0:
        sev = 'URGENT'; reasons.append(f'{doc:.1f} days of cover, nothing inbound')
    elif doc is not None and doc < URGENT_DOC and inb > 0:
        sev = 'WATCH'; reasons.append(f'{doc:.1f} days of cover, {inb} inbound')
    elif doc is not None and doc < WATCH_DOC and inb == 0:
        sev = 'WATCH'; reasons.append(f'{doc:.1f} days of cover, nothing inbound')

    if item['unfulfillable'] >= UNFULFILLABLE_WATCH:
        reasons.append(f"{item['unfulfillable']} unfulfillable units")
        if sev in ('OK', 'INFO'):
            sev = 'WATCH'
    if sev in ('CRITICAL', 'URGENT') and (item['ad30'] or 0) > 0:
        reasons.append('Ads still running')
    if not inbound_known and sev != 'OK':
        reasons.append('Inbound unknown (not reported), treated as 0')
    return sev, reasons


def build_snapshot(week, generated):
    week_dir = os.path.join(ROOT, 'data', 'raw', week)
    h10 = load_h10(week_dir)
    h10v = load_h10_velocity(week_dir)
    si = load_si(week_dir)
    feedback = load_csv(week_dir, 'seller_feedback.csv')
    recs = {}
    for r in load_csv(week_dir, 'restock_recs.csv'):
        code = r['market'] if '_' in r['market'] else 'CC_' + r['market']
        recs[(code, r['asin'])] = r
    health_raw = load_json(week_dir, 'account_health.json') or {}
    health = {(k if '_' in k else 'CC_' + k): v for k, v in health_raw.items()}

    VEL_DAYS = 30.0
    items = defaultdict(dict)   # code -> asin -> item
    for (code, asin), h in h10.items():
        s_ = si.get((code, asin), {})
        units_h10 = h10v.get((code, asin))
        units30 = s_.get('units30') if s_ else units_h10
        vel = s_.get('vel') if s_ else ((units_h10 or 0) / VEL_DAYS)
        doc = s_.get('doc') if s_ else ((h['available'] / vel) if vel and vel > 0 else None)
        items[code][asin] = {
            **h, 'units30': units30 or 0, 'vel': vel or 0, 'doc': doc,
            'ad30': s_.get('ad30', 0) if s_ else 0, 'si_risk': s_.get('risk') if s_ else None,
            'transfer': s_.get('transfer', 0) if s_ else 0,
            'si_present': bool(s_), 'vel_source': 'si' if s_ else ('h10' if units_h10 is not None else 'none'),
        }
    for (code, asin), s_ in si.items():
        if code in ACC and asin not in items[code]:
            items[code][asin] = {
                'asin': asin, 'sku': s_.get('sku'), 'skus': [s_.get('sku')], 'name': s_.get('name') or asin, 'title': s_.get('name') or asin, 'image': None,
                'available': int(s_.get('fba') or 0), 'inbound': int(s_.get('si_inbound') or 0), 'inbound_reported': False,
                'unfulfillable': 0, 'units30': s_.get('units30', 0), 'vel': s_.get('vel', 0), 'doc': s_.get('doc'),
                'ad30': s_.get('ad30', 0), 'si_risk': s_.get('risk'), 'transfer': s_.get('transfer', 0), 'si_present': True, 'vel_source': 'si',
            }

    # Shared FBA pools (EU): pooled velocity per ASIN across the pool's markets
    pool_members = defaultdict(list)
    for a in ACCOUNTS:
        pool_members[a['pool']].append(a['code'])
    shared_pools = {pl for pl, mem in pool_members.items() if len(mem) > 1}
    pool_vel = defaultdict(float)      # (pool, asin) -> summed velocity
    pool_mkts = defaultdict(set)
    pool_avail = defaultdict(set)
    for code in items:
        pl = ACC[code]['pool']
        if pl in shared_pools:
            for asin, it in items[code].items():
                pool_vel[(pl, asin)] += it['vel'] or 0
                pool_mkts[(pl, asin)].add(ACC[code]['market'])
                pool_avail[(pl, asin)].add(it['available'])
    # An ASIN is treated as pooled only when every member market reports the SAME available count
    # (true shared FBA stock, e.g. Pan-EU or NA remote fulfilment). Otherwise each market stands alone.
    pooled = {k for k, av in pool_avail.items() if len(av) == 1 and len(pool_mkts[k]) > 1}

    accounts_out = {}
    flat = []
    for code in ORDER:
        meta = ACC[code]
        pl = meta['pool']
        inv_rows = []
        for asin, it in sorted(items.get(code, {}).items()):
            pv = pd = None
            is_pooled = (pl, asin) in pooled
            if is_pooled:
                pv = pool_vel[(pl, asin)]
                pd = (it['available'] / pv) if pv > 0 else None
            sev, reasons = classify(it, pv, pd)
            if meta['launching'] and (it['units30'] or 0) == 0:
                sev = 'INFO'
                reasons = [f"Launching: {it['available']} available, {it['inbound'] if it['inbound'] is not None else '?'} inbound"]
            rec = recs.get((code, asin))
            est = None
            v = pv if pv is not None else (it['vel'] or 0)
            if sev in ('CRITICAL', 'URGENT', 'WATCH') and v > 0:
                horizon = LEAD_TIME_DAYS.get(pl, 30) + REVIEW_COVER_DAYS
                est = max(0, int(round(v * horizon)) - it['available'] - (it['inbound'] or 0))
            row = {
                'asin': asin, 'sku': it['sku'], 'skus': it.get('skus'), 'name': it['name'], 'title': it['title'], 'image': it['image'],
                'available': it['available'], 'inbound': it['inbound'], 'inbound_reported': it['inbound_reported'],
                'unfulfillable': it['unfulfillable'], 'transfer': it['transfer'],
                'units30': it['units30'], 'vel': round(it['vel'] or 0, 2), 'doc': round(it['doc'], 1) if it['doc'] is not None else None,
                'pool': pl, 'pool_vel': round(pv, 2) if pv is not None else None,
                'pool_doc': round(pd, 1) if pd is not None else None,
                'pool_markets': sorted(pool_mkts[(pl, asin)]) if pv is not None else None,
                'ad30': it['ad30'], 'si_risk': it['si_risk'], 'vel_source': it['vel_source'],
                'severity': sev, 'reasons': reasons,
                'restock_rec': int(rec['rec_qty']) if rec and rec.get('rec_qty') else None,
                'restock_rec_date': rec.get('rec_date') if rec else None,
                'restock_est': est,
            }
            inv_rows.append(row)
            fm = pl if is_pooled else code
            if sev in ('CRITICAL', 'URGENT', 'WATCH') and not any(f_['id'] == f'{fm}|{asin}|stock' for f_ in flat):
                flat.append({'id': f'{fm}|{asin}|stock', 'account': fm, 'brand': meta['brand'], 'market': pl.split('_')[1] if is_pooled else meta['market'],
                             'label': (pl.split('_')[1] + ' ' + BRANDS[meta['brand']]['label']) if is_pooled else meta['label'],
                             'pool_markets': row['pool_markets'], 'type': 'stock', 'severity': sev,
                             'asin': asin, 'sku': it['sku'], 'name': it['name'], 'reason': '; '.join(reasons),
                             'available': it['available'], 'inbound': it['inbound'],
                             'doc': row['pool_doc'] if row['pool_doc'] is not None else row['doc'],
                             'vel': row['pool_vel'] if row['pool_vel'] is not None else row['vel'],
                             'ad30': it['ad30'], 'restock_rec': row['restock_rec'], 'restock_est': est,
                             'owner': 'client', 'action': 'Create FBA shipment' if sev != 'WATCH' else 'Plan shipment'})
        inv_rows.sort(key=lambda r: (SEV_RANK[r['severity']], r['pool_doc'] if r['pool_doc'] is not None else (r['doc'] if r['doc'] is not None else 9e9)))

        fb = None
        for r in feedback:
            if BY_SELLER_MKT.get((r.get('seller_id'), r.get('market'))) == code or r.get('market') == code:
                negs = [x.strip() for x in (r.get('recent_negative') or '').split('||') if x.strip()]
                fb = {'window': r.get('window'), 'rating': fnum(r.get('rating')), 'count': int(fnum(r.get('count'), 0)),
                      'pos_pct': fnum(r.get('pos_pct')), 'neg_pct': fnum(r.get('neg_pct')), 'recent_negative': negs}
                if negs:
                    sev = 'URGENT' if (fb['neg_pct'] or 0) >= FEEDBACK_NEG_PCT_URGENT and fb['count'] >= FEEDBACK_MIN_COUNT else 'WATCH'
                    flat.append({'id': f'{code}|seller|feedback', 'account': code, 'brand': meta['brand'], 'market': meta['market'], 'label': meta['label'],
                                 'type': 'account', 'severity': sev, 'asin': None, 'sku': None, 'name': 'Seller feedback',
                                 'reason': f"{len(negs)} negative in {fb['window']} ({int((fb['neg_pct'] or 0)*100)}% of {fb['count']})",
                                 'owner': 'barcus', 'action': 'Review, request removal if order-related'})

        ah = health.get(code) or {}
        for n in ah.get('notifications', []):
            sev = n.get('severity') or ('CRITICAL' if n.get('type') in ('policy_violation', 'atoz_claim', 'listing_removed', 'account_at_risk') else 'WATCH')
            flat.append({'id': f"{code}|{n.get('asin') or 'acct'}|{n.get('type')}|{n.get('date')}", 'account': code, 'brand': meta['brand'],
                         'market': meta['market'], 'label': meta['label'], 'type': 'account',
                         'severity': sev, 'asin': n.get('asin'), 'sku': None, 'name': n.get('type', 'notification').replace('_', ' ').title(),
                         'reason': n.get('subject') or '', 'opened': n.get('date'), 'status': n.get('status'),
                         'owner': 'barcus', 'action': n.get('action') or 'Open in Seller Central'})

        counts = {k: 0 for k in SEV_RANK}
        for r in inv_rows:
            counts[r['severity']] += 1
        for f_ in flat:
            if f_['account'] == code and f_['type'] == 'account':
                counts[f_['severity']] += 1
        stock_sev = min((r['severity'] for r in inv_rows), key=lambda s_: SEV_RANK[s_], default='OK')
        acct_items = [f_ for f_ in flat if f_['account'] == code and f_['type'] == 'account']
        acct_sev = min((f_['severity'] for f_ in acct_items), key=lambda s_: SEV_RANK[s_], default='OK')
        accounts_out[code] = {
            **{k: meta[k] for k in ('code', 'brand', 'brand_name', 'market', 'name', 'cur', 'seller', 'pool', 'launching', 'label')},
            'pool_shared': pl in shared_pools,
            'coverage': {'h10': any(k[0] == code for k in h10), 'si': any(k[0] == code for k in si),
                         'velocity': any(k[0] == code for k in h10v) or any(k[0] == code for k in si),
                         'feedback': fb is not None, 'account_feed': bool(ah)},
            'inventory': inv_rows, 'feedback': fb,
            'account': {'ahr_status': ah.get('ahr_status'), 'ahr_score': ah.get('ahr_score'), 'odr': ah.get('odr'),
                        'policy_violations': ah.get('policy_violations'), 'atoz_claims': ah.get('atoz_claims'),
                        'notifications': ah.get('notifications', []), 'as_of': ah.get('as_of')},
            'status': {'stock': stock_sev, 'account': acct_sev,
                       'listings': 'OK' if not ah.get('listing_issues') else 'CRITICAL'},
            'counts': counts,
            'fba_units': sum(r['available'] for r in inv_rows),
            'units30': sum(r['units30'] or 0 for r in inv_rows),
            'asin_count': len(inv_rows),
        }

    # Region-wide account items (e.g. EU VAT, GPSR) live under the pool key (CC_EU) and appear once.
    for pl in sorted(shared_pools):
        ah = health.get(pl) or {}
        if not ah:
            continue
        brand = pl.split('_')[0]
        mkts = [ACC[c]['market'] for c in pool_members[pl]]
        for n in ah.get('notifications', []):
            sev = n.get('severity') or 'WATCH'
            flat.append({'id': f"{pl}|{n.get('asin') or 'acct'}|{n.get('type')}|{n.get('date')}", 'account': pl, 'brand': brand,
                         'market': pl.split('_')[1], 'label': pl.split('_')[1] + ' ' + BRANDS[brand]['label'], 'pool_markets': mkts, 'type': 'account',
                         'severity': sev, 'asin': n.get('asin'), 'sku': None, 'name': n.get('type', 'notification').replace('_', ' ').title(),
                         'reason': n.get('subject') or '', 'opened': n.get('date'), 'status': n.get('status'),
                         'owner': 'barcus', 'action': n.get('action') or 'Open in Seller Central'})
            for c in pool_members[pl]:
                accounts_out[c]['counts'][sev] = accounts_out[c]['counts'].get(sev, 0) + 1
                if SEV_RANK[sev] < SEV_RANK[accounts_out[c]['status']['account']]:
                    accounts_out[c]['status']['account'] = sev
                accounts_out[c]['coverage']['account_feed'] = True
    AORDER = ORDER + sorted(shared_pools)
    flat.sort(key=lambda f_: (SEV_RANK[f_['severity']], AORDER.index(f_['account']) if f_['account'] in AORDER else 99, f_.get('doc') if f_.get('doc') is not None else 9e9))
    totals = {k: sum(1 for f_ in flat if f_['severity'] == k) for k in ('CRITICAL', 'URGENT', 'WATCH')}
    snap = {
        'week': week, 'generated_at': generated,
        'sources': {'h10_inventory': bool(h10), 'h10_velocity': bool(h10v), 'si_inventory': bool(si), 'seller_feedback': bool(feedback),
                    'account_health': bool(health), 'restock_recs': bool(recs)},
        'thresholds': {'urgent_doc': URGENT_DOC, 'watch_doc': WATCH_DOC, 'lead_time_days': LEAD_TIME_DAYS, 'review_cover_days': REVIEW_COVER_DAYS},
        'totals': totals, 'items': flat, 'accounts': accounts_out, 'order': ORDER,
        'brands': BRANDS, 'pools': {pl: [ACC[c]['market'] for c in mem] for pl, mem in pool_members.items() if len(mem) > 1},
    }
    return snap


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    week = sys.argv[1]
    generated = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if '--generated' in sys.argv:
        generated = sys.argv[sys.argv.index('--generated') + 1]
    snap = build_snapshot(week, generated)
    out_dir = os.path.join(ROOT, 'data', 'snapshots')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f'{week}.json')
    with open(out, 'w') as f:
        json.dump(snap, f, indent=1, ensure_ascii=False)
    t = snap['totals']
    print(f"{week}: CRITICAL {t['CRITICAL']}  URGENT {t['URGENT']}  WATCH {t['WATCH']}  -> {out}")
    for it in snap['items']:
        if it['severity'] in ('CRITICAL', 'URGENT'):
            print(f"  {it['severity']:8} {it['label']:10} {it['asin'] or '':10} {it['name'][:32]:32} {it['reason']}")


if __name__ == '__main__':
    main()
