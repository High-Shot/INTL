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
import csv, json, sys, os, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MARKETS = {
    'US': {'name': 'United States', 'cur': '$',   'region': 'NA', 'pool': 'US',  'seller': 'AOXMQPMOL1F1Y'},
    'CA': {'name': 'Canada',        'cur': 'C$',  'region': 'NA', 'pool': 'CA',  'seller': 'AOXMQPMOL1F1Y'},
    'UK': {'name': 'United Kingdom','cur': '£',   'region': 'EU', 'pool': 'UK',  'seller': 'A3BMUMIXNXIR6G'},
    'DE': {'name': 'Germany',       'cur': '€',   'region': 'EU', 'pool': 'EU',  'seller': 'A3BMUMIXNXIR6G'},
    'FR': {'name': 'France',        'cur': '€',   'region': 'EU', 'pool': 'EU',  'seller': 'A3BMUMIXNXIR6G'},
    'IT': {'name': 'Italy',         'cur': '€',   'region': 'EU', 'pool': 'EU',  'seller': 'A3BMUMIXNXIR6G'},
    'ES': {'name': 'Spain',         'cur': '€',   'region': 'EU', 'pool': 'EU',  'seller': 'A3BMUMIXNXIR6G'},
    'NL': {'name': 'Netherlands',   'cur': '€',   'region': 'EU', 'pool': 'EU',  'seller': 'A3BMUMIXNXIR6G'},
    'SE': {'name': 'Sweden',        'cur': 'kr',  'region': 'EU', 'pool': 'EU',  'seller': 'A3BMUMIXNXIR6G'},
    'SA': {'name': 'Saudi Arabia',  'cur': 'SAR', 'region': 'EU', 'pool': 'SA',  'seller': 'A3BMUMIXNXIR6G'},
    'AU': {'name': 'Australia',     'cur': 'A$',  'region': 'FE', 'pool': 'AU',  'seller': 'A22UNGVVL3ZGDF'},
}
ORDER = ['US', 'CA', 'UK', 'DE', 'FR', 'IT', 'ES', 'NL', 'SE', 'SA', 'AU']

# Thresholds (days of cover). Project rule: <14 days = URGENT, OOS = CRITICAL.
URGENT_DOC = 14
WATCH_DOC = 28
# Restock estimate (fallback when Amazon's recommendation is not loaded):
#   qty = velocity x (lead_time_days + REVIEW_COVER_DAYS) - available - inbound
# Lead times are ASSUMPTIONS as of 2026-09-01 (Barcus to correct): NA 14d, UK/EU/AU/SA 45d.
LEAD_TIME_DAYS = {'US': 14, 'CA': 14, 'UK': 45, 'EU': 45, 'SA': 45, 'AU': 45}
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
    p = os.path.join(week_dir, 'h10_inventory.json')
    with open(p) as f:
        d = json.load(f)
    rows = d['data']['rows']
    out = {}
    for r in rows:
        if r.get('fulfillment_type') != 'FBA':
            continue
        mk = r['marketplace']
        inv = r.get('inventory', {})
        key = (mk, r['asin'])
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
        # Same ASIN can appear under several SKUs in one market (merged listings). Keep max stock, first SKU.
        if key in out:
            if rec['available'] > out[key]['available']:
                out[key].update({k: rec[k] for k in ('available', 'inbound', 'unfulfillable')})
            out[key]['skus'] = sorted(set(out[key].get('skus', [out[key]['sku']]) + [rec['sku']]))
        else:
            rec['skus'] = [rec['sku']]
            out[key] = rec
    return out


def load_si(week_dir):
    p = os.path.join(week_dir, 'si_inventory.csv')
    out = {}
    if not os.path.exists(p):
        return out
    with open(p, newline='') as f:
        for r in csv.DictReader(f):
            out[(r['market'], r['asin'])] = {
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
        sev = 'URGENT'; reasons.append(f'Out of stock, {inb} inbound')
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
    si = load_si(week_dir)
    feedback = load_csv(week_dir, 'seller_feedback.csv')
    recs = {(r['market'], r['asin']): r for r in load_csv(week_dir, 'restock_recs.csv')}
    health = load_json(week_dir, 'account_health.json') or {}

    # Merge H10 + SI per market/ASIN
    items = defaultdict(dict)   # market -> asin -> item
    for (mk, asin), h in h10.items():
        if mk not in MARKETS:
            continue
        s = si.get((mk, asin), {})
        items[mk][asin] = {
            **h,
            'units30': s.get('units30', 0), 'vel': s.get('vel', 0), 'doc': s.get('doc'),
            'ad30': s.get('ad30', 0), 'si_risk': s.get('risk'), 'transfer': s.get('transfer', 0),
            'si_present': bool(s),
        }
    # SI rows with no H10 row (rare): include with SI stock
    for (mk, asin), s in si.items():
        if mk in MARKETS and asin not in items[mk]:
            items[mk][asin] = {
                'asin': asin, 'sku': s.get('sku'), 'skus': [s.get('sku')], 'name': s.get('name') or asin, 'title': s.get('name') or asin, 'image': None,
                'available': int(s.get('fba') or 0), 'inbound': int(s.get('si_inbound') or 0), 'inbound_reported': False,
                'unfulfillable': 0, 'units30': s.get('units30', 0), 'vel': s.get('vel', 0), 'doc': s.get('doc'),
                'ad30': s.get('ad30', 0), 'si_risk': s.get('risk'), 'transfer': s.get('transfer', 0), 'si_present': True,
            }

    # EU pool: DE/FR/IT/ES/NL/SE share one FBA stock. Days of cover must use the pooled velocity.
    pool_vel = defaultdict(float)
    pool_markets = defaultdict(set)
    for mk in items:
        if MARKETS[mk]['pool'] == 'EU':
            for asin, it in items[mk].items():
                pool_vel[asin] += it['vel'] or 0
                pool_markets[asin].add(mk)

    markets_out = {}
    flat = []
    for mk in ORDER:
        meta = MARKETS[mk]
        inv_rows = []
        for asin, it in sorted(items.get(mk, {}).items()):
            pv = pd = None
            if meta['pool'] == 'EU':
                pv = pool_vel[asin]
                pd = (it['available'] / pv) if pv > 0 else None
            sev, reasons = classify(it, pv, pd)
            rec = recs.get((mk, asin))
            est = None
            v = pv if pv is not None else (it['vel'] or 0)
            if sev in ('CRITICAL', 'URGENT', 'WATCH') and v > 0:
                horizon = LEAD_TIME_DAYS.get(meta['pool'], 30) + REVIEW_COVER_DAYS
                est = max(0, int(round(v * horizon)) - it['available'] - (it['inbound'] or 0))
            row = {
                'asin': asin, 'sku': it['sku'], 'skus': it.get('skus'), 'name': it['name'], 'title': it['title'], 'image': it['image'],
                'available': it['available'], 'inbound': it['inbound'], 'inbound_reported': it['inbound_reported'],
                'unfulfillable': it['unfulfillable'], 'transfer': it['transfer'],
                'units30': it['units30'], 'vel': round(it['vel'] or 0, 2), 'doc': it['doc'],
                'pool': meta['pool'], 'pool_vel': round(pv, 2) if pv is not None else None,
                'pool_doc': round(pd, 1) if pd is not None else None,
                'pool_markets': sorted(pool_markets[asin]) if pv is not None else None,
                'ad30': it['ad30'], 'si_risk': it['si_risk'], 'si_present': it['si_present'],
                'severity': sev, 'reasons': reasons,
                'restock_rec': int(rec['rec_qty']) if rec and rec.get('rec_qty') else None,
                'restock_rec_date': rec.get('rec_date') if rec else None,
                'restock_est': est,
            }
            inv_rows.append(row)
            fm = 'EU' if meta['pool'] == 'EU' else mk
            if sev in ('CRITICAL', 'URGENT', 'WATCH') and not any(f_['id'] == f'{fm}|{asin}|stock' for f_ in flat):
                flat.append({'id': f'{fm}|{asin}|stock', 'market': fm, 'type': 'stock', 'severity': sev,
                             'pool_markets': row['pool_markets'],
                             'asin': asin, 'sku': it['sku'], 'name': it['name'], 'reason': '; '.join(reasons),
                             'available': it['available'], 'inbound': it['inbound'],
                             'doc': row['pool_doc'] if row['pool_doc'] is not None else it['doc'],
                             'vel': row['pool_vel'] if row['pool_vel'] is not None else row['vel'],
                             'ad30': it['ad30'], 'restock_rec': row['restock_rec'], 'restock_est': est,
                             'owner': 'client', 'action': 'Create FBA shipment' if sev != 'WATCH' else 'Plan shipment'})
        inv_rows.sort(key=lambda r: (SEV_RANK[r['severity']], r['pool_doc'] if r['pool_doc'] is not None else (r['doc'] if r['doc'] is not None else 9e9)))

        # Seller feedback
        fb = None
        for r in feedback:
            if r['market'] == mk:
                negs = [x.strip() for x in (r.get('recent_negative') or '').split('||') if x.strip()]
                fb = {'window': r.get('window'), 'rating': fnum(r.get('rating')), 'count': int(fnum(r.get('count'), 0)),
                      'pos_pct': fnum(r.get('pos_pct')), 'neg_pct': fnum(r.get('neg_pct')), 'recent_negative': negs}
                if negs:
                    sev = 'URGENT' if (fb['neg_pct'] or 0) >= FEEDBACK_NEG_PCT_URGENT and fb['count'] >= FEEDBACK_MIN_COUNT else 'WATCH'
                    flat.append({'id': f'{mk}|seller|feedback', 'market': mk, 'type': 'account', 'severity': sev,
                                 'asin': None, 'sku': None, 'name': 'Seller feedback',
                                 'reason': f"{len(negs)} negative in {fb['window']} ({int((fb['neg_pct'] or 0)*100)}% of {fb['count']})",
                                 'owner': 'barcus', 'action': 'Review, request removal if order-related'})

        # Account health feed (Seller Central notifications, AHR)
        ah = health.get(mk) or {}
        for n in ah.get('notifications', []):
            sev = n.get('severity') or ('CRITICAL' if n.get('type') in ('policy_violation', 'atoz_claim', 'listing_removed', 'account_at_risk') else 'WATCH')
            flat.append({'id': f"{mk}|{n.get('asin') or 'acct'}|{n.get('type')}|{n.get('date')}", 'market': mk, 'type': 'account',
                         'severity': sev, 'asin': n.get('asin'), 'sku': None, 'name': n.get('type', 'notification').replace('_', ' ').title(),
                         'reason': n.get('subject') or '', 'owner': 'barcus', 'action': n.get('action') or 'Open in Seller Central'})

        counts = {k: 0 for k in SEV_RANK}
        for r in inv_rows:
            counts[r['severity']] += 1
        for f_ in flat:
            if f_['market'] == mk and f_['type'] == 'account':
                counts[f_['severity']] += 1
        stock_sev = min((r['severity'] for r in inv_rows), key=lambda s: SEV_RANK[s], default='OK')
        acct_items = [f_ for f_ in flat if f_['market'] == mk and f_['type'] == 'account']
        acct_sev = min((f_['severity'] for f_ in acct_items), key=lambda s: SEV_RANK[s], default='OK')
        markets_out[mk] = {
            **meta, 'code': mk,
            'coverage': {'h10': any(k[0] == mk for k in h10), 'si': any(k[0] == mk for k in si),
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

    MORDER = ORDER + ['EU']
    flat.sort(key=lambda f_: (SEV_RANK[f_['severity']], MORDER.index(f_['market']), f_.get('doc') if f_.get('doc') is not None else 9e9))
    totals = {k: sum(1 for f_ in flat if f_['severity'] == k) for k in ('CRITICAL', 'URGENT', 'WATCH')}
    snap = {
        'week': week, 'generated_at': generated,
        'sources': {'h10_inventory': True, 'si_inventory': bool(si), 'seller_feedback': bool(feedback),
                    'account_health': bool(health), 'restock_recs': bool(recs)},
        'thresholds': {'urgent_doc': URGENT_DOC, 'watch_doc': WATCH_DOC, 'target_doc': TARGET_DOC, 'lead_time_days': LEAD_TIME_DAYS, 'review_cover_days': REVIEW_COVER_DAYS},
        'totals': totals, 'items': flat, 'markets': markets_out, 'order': ORDER,
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
            print(f"  {it['severity']:8} {it['market']} {it['asin'] or '':10} {it['name'][:32]:32} {it['reason']}")


if __name__ == '__main__':
    main()
