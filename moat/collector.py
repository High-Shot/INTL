#!/usr/bin/env python3
"""MOAT collector: turn raw tool dumps into one shared input snapshot.

Usage:
  python3 collector.py <raw_dir> --out <snapshots_dir> [--run-date YYYY-MM-DD]
                       [--bulk <export.xlsx> ...]

<raw_dir> holds the tool-result files saved verbatim by the moat-collector skill:
  si_ads_<CC>_<N>d[_pK].json   Scale Insights get_ads_performance, mode raw, Cerakote Auto
  h10_velocity_day[_K].json    Helium 10 get_sales_velocity, granularity day, last 60 complete days
  h10_rank_<CC>[_K].json       Helium 10 list_tracked_keywords, one marketplace per file

Writes <snapshots_dir>/<run_date>/{manifest,ads_si,ads_bulk,sales_h10,rank_h10}.json
and <snapshots_dir>/latest (a one-line pointer file). Never calls a tool, never derives
ROAS or cover beyond what the source gives. A source with no input is written as failed,
never as zeros.
"""
import argparse, datetime as dt, glob, json, os, re, sys, zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

SCHEMA_VERSION = 1
SELLERS = {  # seller id -> brand
    'AOXMQPMOL1F1Y': 'Cerakote Auto', 'A3BMUMIXNXIR6G': 'Cerakote Auto', 'A22UNGVVL3ZGDF': 'Cerakote Auto',
    'A1KUYEQ8RRQVVI': 'Cerakote Legacy', 'A21D21T8B6U09C': 'Prismatic Powders',
}
BULK_ACCOUNTS = {  # bulk export account id -> (brand, marketplace, currency)
    'a1kuyeq8rrqvvi': ('Cerakote Legacy', 'US', 'USD'),
    'a21d21t8b6u09c': ('Prismatic Powders', 'US', 'USD'),
    'a3bmumixnxir6g': ('Cerakote Auto', 'SA', 'SAR'),
}
CURRENCY = {'US': 'USD', 'CA': 'CAD', 'UK': 'GBP', 'DE': 'EUR', 'FR': 'EUR', 'IT': 'EUR', 'ES': 'EUR',
            'NL': 'EUR', 'AE': 'AED', 'SA': 'SAR', 'AU': 'AUD'}
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


# ---------------- helpers ----------------
def load_tool_json(path):
    """Tool-result files can carry a trailing gateway-meta line or a second JSON object; take the first object."""
    txt = open(path, encoding='utf-8').read().strip()
    obj, _ = json.JSONDecoder().raw_decode(txt)
    # some saved results wrap the payload as [{"type":"text","text":"{...}"}]
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and 'text' in obj[0]:
        obj, _ = json.JSONDecoder().raw_decode(obj[0]['text'].strip())
    return obj


def money(v):
    """'$13,216.04' or '13216.04' or 13216.04 -> float. Unparseable -> None (never 0)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r'[^\d.\-]', '', str(v))
    try:
        return float(s)
    except ValueError:
        return None


def pct(v):
    m = money(v)
    return None if m is None else round(m / 100, 4)


def key(brand, mkt, asin):
    return f'{brand}|{mkt}|{asin.upper()}'


def envelope(source, run_date, status, coverage, rows, notes):
    return {'schema_version': SCHEMA_VERSION, 'source': source, 'run_date': run_date,
            'generated_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
            'status': status, 'coverage': coverage, 'notes': notes, 'rows': rows}


def status_of(coverage):
    if not coverage:
        return 'failed'
    st = {c['status'] for c in coverage}
    return 'ok' if st == {'ok'} else ('failed' if st <= {'failed', 'no_data'} else 'partial')


# ---------------- Scale Insights ads ----------------
def build_ads_si(raw, run_date):
    files = sorted(glob.glob(os.path.join(raw, 'si_ads_*.json')))
    by = defaultdict(dict)       # (mkt) -> {asin: row}
    cov, notes = {}, []
    for f in files:
        m = re.match(r'si_ads_([A-Z]{2})_(\d+)d(?:_p\d+)?\.json$', os.path.basename(f))
        if not m:
            notes.append(f'skipped unrecognised file {os.path.basename(f)}'); continue
        mkt, win = m.group(1), int(m.group(2))
        try:
            d = load_tool_json(f)
        except Exception as e:
            cov.setdefault((mkt, win), {'status': 'failed'}); notes.append(f'{mkt} {win}d unreadable: {e}'); continue
        agg = d.get('agg') or {}
        opps = d.get('opps') or []
        c = cov.setdefault((mkt, win), {'status': 'ok', 'rows': 0, 'expected': None,
                                         'date_range': None, 'currency': agg.get('Currency') or CURRENCY.get(mkt)})
        c['expected'] = (d.get('oppMeta') or {}).get('total_count', c['expected'])
        if agg.get('TotalSpend') is not None:
            c['agg_spend'] = money(agg.get('TotalSpend'))
        if agg.get('StartDate'):
            c['date_range'] = f"{agg['StartDate']}..{agg['EndDate']}"
        if not opps and not c['rows']:
            c['status'] = 'no_data'
        for o in opps:
            if o.get('entityType') != 'ASIN':
                continue
            mt = o.get('metrics') or {}
            asin = o['entity'].upper()
            r = by[mkt].setdefault(asin, {'key': key('Cerakote Auto', mkt, asin)})
            spend, sales = money(mt.get('TotalSpend')), money(mt.get('TotalAdSales'))
            r[f'w{win}'] = {
                'ppc_cost': spend, 'ppc_sales': sales,
                'orders': int(money(mt.get('TotalOrders')) or 0) if mt.get('TotalOrders') is not None else None,
                'acos': pct(mt.get('ACOS')), 'roas': money(mt.get('ROAS')),
                'sp_cost': money(mt.get('SP_Spend')), 'sp_sales': money(mt.get('SP_Sales')),
                'sb_cost': money(mt.get('SB_Spend')), 'sb_sales': money(mt.get('SB_Sales')),
                'sd_cost': money(mt.get('SD_Spend')), 'sd_sales': money(mt.get('SD_Sales')),
            }
            c['rows'] += 1
    for (mkt, win), c in cov.items():
        if c['status'] == 'ok' and c['expected'] is not None and c['rows'] < c['expected']:
            c['status'] = 'partial'; notes.append(f'{mkt} {win}d: {c["rows"]} of {c["expected"]} rows (missing pages)')
        # cross-check: rows must add up to the account total SI reported (catches a mistyped or dropped row)
        tot = c.pop('agg_spend', None)
        if tot and c['status'] == 'ok':
            s = sum((by[mkt][a].get(f'w{win}') or {}).get('ppc_cost') or 0 for a in by[mkt])
            if abs(s - tot) > max(1.0, 0.005 * tot):
                c['status'] = 'partial'
                notes.append(f'{mkt} {win}d: rows sum to {s:,.2f} but SI total is {tot:,.2f}')
    coverage = [{'brand': 'Cerakote Auto', 'marketplace': mkt, 'window_days': win, **c}
                for (mkt, win), c in sorted(cov.items())]
    rows = [r for mkt in sorted(by) for r in sorted(by[mkt].values(), key=lambda x: x['key'])]
    notes.append('Per-ASIN, SP+SB+SD, Scale Insights attribution. Cerakote Auto only; AE, SA return no SI data.')
    return envelope('ads_si', run_date, status_of(coverage), coverage, rows, notes)


# ---------------- bulk exports (Legacy, Prismatic, SA) ----------------
def _col(ref):
    n = 0
    for ch in re.match(r'[A-Z]+', ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_sheet(z, name):
    wb = z.read('xl/workbook.xml').decode()
    rels = z.read('xl/_rels/workbook.xml.rels').decode()
    m = re.search(r'<sheet [^>]*name="%s"[^>]*r:id="([^"]+)"' % re.escape(name), wb)
    if not m:
        return []
    t = re.search(r'<Relationship [^>]*Id="%s"[^>]*>' % m.group(1), rels).group(0)
    sheet = 'xl/' + re.search(r'Target="([^"]+)"', t).group(1).lstrip('/').replace('xl/', '')
    ss = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).iter(NS + 'si'):
            ss.append(''.join(x.text or '' for x in si.iter(NS + 't')))
    header, rows = None, []
    for _, el in ET.iterparse(z.open(sheet)):
        if el.tag != NS + 'row':
            continue
        r = {}
        for c in el.iter(NS + 'c'):
            ty, v = c.get('t'), c.find(NS + 'v')
            if ty == 'inlineStr':
                val = ''.join(x.text or '' for x in c.iter(NS + 't'))
            elif ty == 's':
                val = ss[int(v.text)]
            else:
                val = v.text if v is not None else None
            r[_col(c.get('r'))] = val
        el.clear()
        if not r:
            continue
        if header is None:
            header = [r.get(i) for i in range(max(r) + 1)]
        else:
            rows.append({header[k]: v for k, v in r.items() if k < len(header)})
    return rows


def build_ads_bulk(paths, run_date):
    cov, rows, notes = [], [], []
    seen = {}
    for p in paths:
        base = os.path.basename(p)
        m = re.search(r'bulk-([a-z0-9]+)-(\d{8})-(\d{8})', base, re.I)
        code = re.match(r'(CL_US|PP_US|CC_SA)_', base, re.I)
        if m and m.group(1).lower() in BULK_ACCOUNTS:
            brand, mkt, cur = BULK_ACCOUNTS[m.group(1).lower()]
            d0 = dt.datetime.strptime(m.group(2), '%Y%m%d').date()
            d1 = dt.datetime.strptime(m.group(3), '%Y%m%d').date()
        elif code:
            # renamed export (e.g. PP_US_0922.xlsx): account is known, window is not
            brand, mkt, cur = {'CL_US': BULK_ACCOUNTS['a1kuyeq8rrqvvi'], 'PP_US': BULK_ACCOUNTS['a21d21t8b6u09c'],
                               'CC_SA': BULK_ACCOUNTS['a3bmumixnxir6g']}[code.group(1).upper()]
            d0 = None
            d1 = dt.date.fromtimestamp(os.path.getmtime(p))
            notes.append(f'{base}: renamed export, date range unknown; dated by file time {d1}')
        else:
            notes.append(f'skipped {base}: not a Legacy, Prismatic or SA bulk export'); continue
        if (brand, mkt) in seen and seen[(brand, mkt)] >= d1:
            notes.append(f'skipped older export {base}'); continue
        seen[(brand, mkt)] = d1
        try:
            sp = read_sheet(zipfile.ZipFile(p), 'Sponsored Products Campaigns')
        except Exception as e:
            cov.append({'brand': brand, 'marketplace': mkt, 'status': 'failed', 'file': base}); notes.append(f'{base}: {e}'); continue
        agg = defaultdict(lambda: {'ppc_cost': 0.0, 'ppc_sales': 0.0, 'orders': 0, 'clicks': 0, 'impressions': 0,
                                   'campaigns': set()})
        camp_spend = sum(money(r.get('Spend')) or 0.0 for r in sp if r.get('Entity') == 'Campaign')
        for r in sp:
            if r.get('Entity') != 'Product Ad':
                continue
            asin = (r.get('ASIN (Informational only)') or '').upper()
            if not asin:
                continue
            a = agg[asin]
            a['ppc_cost'] += money(r.get('Spend')) or 0.0
            a['ppc_sales'] += money(r.get('Sales')) or 0.0
            a['orders'] += int(money(r.get('Orders')) or 0)
            a['clicks'] += int(money(r.get('Clicks')) or 0)
            a['impressions'] += int(money(r.get('Impressions')) or 0)
            if r.get('Campaign ID'):
                a['campaigns'].add(r['Campaign ID'])
        pa_spend = sum(a['ppc_cost'] for a in agg.values())
        if camp_spend and abs(pa_spend - camp_spend) > 0.01 * camp_spend:
            notes.append(f'{base}: product-ad spend {pa_spend:,.2f} vs campaign spend {camp_spend:,.2f} differ by more than 1%')
        rows = [x for x in rows if not x['key'].startswith(f'{brand}|{mkt}|')]
        for asin, a in sorted(agg.items()):
            if not (a['ppc_cost'] or a['ppc_sales'] or a['clicks']):
                continue
            rows.append({'key': key(brand, mkt, asin), 'window_days': (d1 - d0).days + 1 if d0 else None,
                         'ppc_cost': round(a['ppc_cost'], 2), 'ppc_sales': round(a['ppc_sales'], 2),
                         'orders': a['orders'], 'clicks': a['clicks'], 'impressions': a['impressions'],
                         'acos': round(a['ppc_cost'] / a['ppc_sales'], 4) if a['ppc_sales'] else None,
                         'roas': round(a['ppc_sales'] / a['ppc_cost'], 2) if a['ppc_cost'] else None,
                         'campaigns': sorted(a['campaigns'])})
        cov = [c for c in cov if (c['brand'], c['marketplace']) != (brand, mkt)]
        cov.append({'brand': brand, 'marketplace': mkt, 'status': 'ok', 'currency': cur,
                    'date_range': f'{d0}..{d1}' if d0 else None, 'window_days': (d1 - d0).days + 1 if d0 else None,
                    'file': base,
                    'age_days': (dt.date.fromisoformat(run_date) - d1).days})
    for b, mk, _ in BULK_ACCOUNTS.values():
        if not any((c['brand'], c['marketplace']) == (b, mk) for c in cov):
            cov.append({'brand': b, 'marketplace': mk, 'status': 'no_data', 'note': 'no bulk export found'})
    notes.append('Sponsored Products only, per ASIN, from the Thursday bulk export. Window ends 4 days before export day.')
    return envelope('ads_bulk', run_date, status_of(cov), cov, rows, notes)


# ---------------- Helium 10 sales velocity ----------------
def build_sales(raw, run_date):
    files = sorted(glob.glob(os.path.join(raw, 'h10_velocity_day*.json')))
    rd = dt.date.fromisoformat(run_date)
    end = rd - dt.timedelta(days=1)                       # last complete day
    w7 = {(end - dt.timedelta(days=i)).isoformat() for i in range(7)}
    w30 = {(end - dt.timedelta(days=i)).isoformat() for i in range(30)}
    w30p = {(end - dt.timedelta(days=i)).isoformat() for i in range(30, 60)}
    agg, notes, seen_days = {}, [], set()
    expected = got = 0
    for f in files:
        try:
            d = load_tool_json(f)
        except Exception as e:
            notes.append(f'{os.path.basename(f)} unreadable: {e}'); continue
        data = d.get('data') or {}
        expected = max(expected, data.get('total_count') or 0)
        for r in data.get('rows') or []:
            got += 1
            seller = r.get('seller_id')
            brand = SELLERS.get(seller)
            if not brand:
                continue
            mkt, asin = r.get('marketplace'), (r.get('asin') or '').upper()
            if not asin or not mkt:
                continue
            a = agg.setdefault(key(brand, mkt, asin), {'key': key(brand, mkt, asin), 'parent_asin': r.get('parent_asin'),
                                                     'product_name': r.get('product_name'), 'units_7d': 0,
                                                     'units_30d': 0, 'units_30d_prior': 0, 'fulfillment': set(),
                                                     'skus': set()})
            a['fulfillment'].add(r.get('fulfillment_type'))
            a['skus'].add(r.get('sku'))
            for day, u in ((r.get('sales_velocity') or {}).get('values') or {}).items():
                seen_days.add(day)
                u = int(u or 0)
                if day in w7:
                    a['units_7d'] += u
                if day in w30:
                    a['units_30d'] += u
                elif day in w30p:
                    a['units_30d_prior'] += u
    rows = []
    for a in sorted(agg.values(), key=lambda x: x['key']):
        a['fulfillment'] = sorted(x for x in a['fulfillment'] if x)
        a['skus'] = sorted(x for x in a['skus'] if x)
        rows.append(a)
    missing = sorted((w30 | w30p) - seen_days)
    status = 'failed' if not files or not got else ('partial' if (expected and got < expected) or missing else 'ok')
    if expected and got < expected:
        notes.append(f'{got} of {expected} SKU rows (missing pages)')
    if missing and got:
        notes.append(f'{len(missing)} of 60 days absent from the pull: {missing[0]}..{missing[-1]}')
    notes.append('Units only, FBA + FBM summed per ASIN. Windows end on the last complete day before run_date.')
    brands = defaultdict(set)
    for r in rows:
        b, m, _ = r['key'].split('|')
        brands[(b, m)].add(1)
    coverage = [{'brand': b, 'marketplace': m, 'status': status, 'asins': sum(1 for r in rows if r['key'].startswith(f'{b}|{m}|')),
                 'date_range': f'{min(w30p)}..{end}'} for (b, m) in sorted(brands)]
    return envelope('sales_h10', run_date, status if rows else 'failed', coverage, rows, notes)


# ---------------- Helium 10 rank ----------------
def build_rank(raw, run_date, asin_brand):
    files = sorted(glob.glob(os.path.join(raw, 'h10_rank_*.json')))
    rows, cov, notes = [], defaultdict(lambda: {'rows': 0, 'status': 'ok'}), []
    for f in files:
        m = re.match(r'h10_rank_([A-Z]{2})(?:_\d+)?\.json$', os.path.basename(f))
        if not m:
            continue
        mkt = m.group(1)
        try:
            d = (load_tool_json(f).get('data') or {})
        except Exception as e:
            cov[mkt]['status'] = 'failed'; notes.append(f'{os.path.basename(f)} unreadable: {e}'); continue
        if (d.get('pagination') or {}).get('has_more'):
            cov[mkt]['has_more_last'] = True
        else:
            cov[mkt]['has_more_last'] = False
        for r in d.get('rows') or []:
            asin = (r.get('asin') or '').upper()
            tags = [t.get('title') for t in r.get('tags') or [] if t.get('title')]
            brand = asin_brand.get((mkt, asin), 'unknown')

            def rk(v):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return None
            rows.append({'key': key(brand, mkt, asin), 'keyword': r.get('keywords_phrase'),
                         'tags': tags, 'organic_rank': rk(r.get('organic_rank')),
                         'organic_rank_trend': r.get('organic_rank_trend'),
                         'sponsored_rank': rk(r.get('sponsored_rank')),
                         'sponsored_rank_trend': r.get('sponsored_rank_trend'),
                         'search_volume': r.get('search_volume'), 'keyword_sales': r.get('keyword_sales')})
            cov[mkt]['rows'] += 1
    coverage = []
    for mkt, c in sorted(cov.items()):
        if c.pop('has_more_last', False) and c['status'] == 'ok':
            c['status'] = 'partial'; notes.append(f'{mkt}: last page reported more rows; pull is incomplete')
        coverage.append({'marketplace': mkt, **c})
    unk = sum(1 for r in rows if r['key'].startswith('unknown|'))
    if unk:
        notes.append(f'{unk} rows on ASINs with no Helium 10 sales in 60 days; brand unknown')
    notes.append('organic_rank null = not ranked or not detected. Trends are Helium 10 deltas, positive = improved.')
    return envelope('rank_h10', run_date, status_of(coverage), coverage, rows, notes)


# ---------------- validation ----------------
KEY_RE = re.compile(r'^(Cerakote Auto|Cerakote Legacy|Prismatic Powders|unknown)\|[A-Z]{2}\|[A-Z0-9]{10}$')


def validate(doc):
    errs = []
    for k in ('schema_version', 'source', 'run_date', 'generated_at', 'status', 'coverage', 'notes', 'rows'):
        if k not in doc:
            errs.append(f'missing envelope key {k}')
    if doc.get('status') not in ('ok', 'partial', 'failed'):
        errs.append(f'bad status {doc.get("status")}')
    keys = []
    for r in doc.get('rows', []):
        if not KEY_RE.match(r.get('key', '')):
            errs.append(f'bad key {r.get("key")}')
        if doc['source'] != 'rank_h10':
            keys.append(r.get('key'))

        def walk(x, path):
            if isinstance(x, dict):
                for kk, vv in x.items():
                    walk(vv, f'{path}.{kk}')
            elif isinstance(x, (int, float)) and not isinstance(x, bool) and x < 0 and 'trend' not in path:
                errs.append(f'negative {path}={x} on {r.get("key")}')
        walk(r, '')
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        errs.append(f'duplicate keys: {sorted(dupes)[:5]}')
    return errs



# ---------------- source_stale flags ----------------
def sync_stale_flags(flags_dir, run_date, docs):
    """Declare every source or market that did not come back ok. Clears the rest."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import flags as F
    new = []
    for name, doc in docs.items():
        bad = [c for c in doc['coverage'] if c.get('status') not in ('ok', None)]
        statuses = {c.get('status') for c in doc['coverage']}
        # whole source down, or every market in the same non-ok state: one source-level flag
        if doc['status'] == 'failed' or (bad and len(bad) == len(doc['coverage']) and len(statuses) == 1):
            why = sorted({c.get('note') or c.get('status') for c in bad}) or doc['notes'][:1]
            detail = '; '.join(n for n in doc['notes'] if 'missing' in n or 'absent' in n or 'unreadable' in n)
            new.append({'type': 'source_stale', 'key': f'all|all|{name}', 'scope': 'source',
                        'reason': (f'{name} {doc["status"]} on {run_date}: ' + '; '.join(why)
                                   + (f' ({detail})' if detail else ''))[:300],
                        'evidence': {'file': f'snapshots/{run_date}/manifest.json', 'source': name}})
            continue
        for c in bad:
            if c.get('status') in ('ok', None):
                continue
            b, m = c.get('brand', 'all'), c.get('marketplace', 'all')
            new.append({'type': 'source_stale', 'key': f'{b}|{m}|{name}', 'scope': 'marketplace',
                        'reason': f'{name} {c["status"]} for {b} {m} on {run_date}',
                        'evidence': {'file': f'snapshots/{run_date}/manifest.json', 'source': name}})
    F.sync(flags_dir, 'moat-collector', ['source_stale'], new)

# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('raw')
    ap.add_argument('--out', required=True)
    ap.add_argument('--run-date', default=dt.date.today().isoformat())
    ap.add_argument('--bulk', nargs='*', default=[])
    ap.add_argument('--flags-dir', default=None, help='MOAT flags dir; when set, source_stale flags are synced')
    ap.add_argument('--private', default='ads_si,ads_bulk',
                    help='sources written locally but kept out of the published repo (gitignored)')
    a = ap.parse_args()

    run_dir = os.path.join(a.out, a.run_date)
    os.makedirs(run_dir, exist_ok=True)
    sales = build_sales(a.raw, a.run_date)
    asin_brand = {}
    for r in sales['rows']:
        b, m, asin = r['key'].split('|')
        asin_brand.setdefault((m, asin), b)
    docs = {
        'ads_si': build_ads_si(a.raw, a.run_date),
        'ads_bulk': build_ads_bulk(a.bulk, a.run_date),
        'sales_h10': sales,
        'rank_h10': build_rank(a.raw, a.run_date, asin_brand),
    }
    manifest = {'schema_version': SCHEMA_VERSION, 'run_date': a.run_date,
                'generated_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                'sources': {}, 'not_built': {'catalog': 'build-order step 6 (catalog watchdog)'},
                'external': {'health_and_stock': 'INTL dashboard data, unchanged'}}
    bad = False
    for name, doc in docs.items():
        errs = validate(doc)
        if errs:
            bad = True
            doc['status'] = 'failed'
            doc['notes'].append('validation failed: ' + '; '.join(errs[:10]))
        with open(os.path.join(run_dir, f'{name}.json'), 'w') as f:
            json.dump(doc, f, indent=1, ensure_ascii=False)
        manifest['sources'][name] = {'file': f'{name}.json', 'status': doc['status'], 'rows': len(doc['rows']),
                                     'coverage': [{k: c[k] for k in c if k in ('brand', 'marketplace', 'window_days', 'status', 'date_range', 'age_days')}
                                                  for c in doc['coverage']],
                                     'validation_errors': len(errs),
                                     'published': name not in a.private.split(',')}
    st = {s['status'] for s in manifest['sources'].values()}
    manifest['status'] = 'ok' if st == {'ok'} else ('failed' if st == {'failed'} else 'partial')
    with open(os.path.join(run_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1)
    with open(os.path.join(a.out, 'latest'), 'w') as f:
        f.write(a.run_date + '\n')
    if a.flags_dir:
        sync_stale_flags(a.flags_dir, a.run_date, docs)
    for name, s in manifest['sources'].items():
        print(f"{name:10s} {s['status']:8s} rows={s['rows']:5d} errors={s['validation_errors']}")
    print(f"manifest   {manifest['status']}")
    sys.exit(2 if bad else 0)


if __name__ == '__main__':
    main()
