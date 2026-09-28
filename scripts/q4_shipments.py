#!/usr/bin/env python3
"""
Q4 shipment upload files: data/q4/<WEEK>.json -> q4/files/<WEEK>/

One Send to Amazon upload file per destination (Amazon's own case-pack template, imperial for US,
metric elsewhere) holding every SKU with a 'Ship now' quantity, plus one review workbook for NIC.

Rules:
  - Quantity = dashboard ship_now (forecast demand over lead time + 67.2 days, minus available and inbound).
  - Listings on hold (removed / restricted) are left out.
  - SKU = the FBA SKU on that ASIN with the most FBA units in the last 30 days (tie: most available).
    FBM SKUs never appear (Helium10 inventory rows are FBA only).
  - Case pack known (data/q4_ref/casepacks.csv): quantity rounds UP to full boxes, Units per box +
    Number of boxes filled. Unknown: individual units, box columns blank for NIC to fill.
  - Box dimensions and weight are left blank; NIC enters them in Send to Amazon.
Usage: python3 scripts/q4_shipments.py 2026-W40
"""
import csv, glob, json, math, os, sys, shutil
from collections import defaultdict
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize import ACC, BY_SELLER_MKT  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, 'data', 'q4_ref')
SHEET = 'Create workflow – template'
FIRST_ROW = 9
MAX_ROWS = 1500
DEST = {  # file key -> (label, template, where NIC creates the workflow)
    'CC_US': ('US AUTO', 'imperial', 'Cerakote Auto, Amazon.com'),
    'CC_CA': ('CA AUTO', 'metric', 'Cerakote Auto, Amazon.ca'),
    'CC_UK': ('UK AUTO', 'metric', 'Cerakote Auto, Amazon.co.uk'),
    'CC_EU': ('EU AUTO (pooled)', 'metric', 'Cerakote Auto EU, the marketplace you normally inbound EU pool stock to'),
    'CC_DE': ('DE AUTO', 'metric', 'Cerakote Auto, Amazon.de'),
    'CC_FR': ('FR AUTO', 'metric', 'Cerakote Auto, Amazon.fr'),
    'CC_IT': ('IT AUTO', 'metric', 'Cerakote Auto, Amazon.it'),
    'CC_ES': ('ES AUTO', 'metric', 'Cerakote Auto, Amazon.es'),
    'CC_NL': ('NL AUTO', 'metric', 'Cerakote Auto, Amazon.nl'),
    'CC_SA': ('SA AUTO', 'metric', 'Cerakote Auto, Amazon.sa'),
    'CC_AE': ('AE AUTO', 'metric', 'Cerakote Auto, Amazon.ae'),
    'CC_AU': ('AU AUTO', 'metric', 'Cerakote Auto, Amazon.com.au'),
    'CL_US': ('US LEGACY', 'imperial', 'Cerakote Legacy, Amazon.com'),
    'PP_US': ('US PRIS', 'imperial', 'Prismatic Powders, Amazon.com'),
}


def load_rows(path_glob):
    rows = []
    for fp in sorted(glob.glob(path_glob)):
        with open(fp) as f:
            rows += json.load(f)['data']['rows']
    return rows


def main():
    week = sys.argv[1]
    with open(os.path.join(ROOT, 'data', 'q4', f'{week}.json')) as f:
        snap = json.load(f)
    raw = os.path.join(ROOT, 'data', 'raw', week, 'q4')

    # FBA SKUs per (market code, asin) with availability, and FBA units per SKU over the last 30 days
    skus = defaultdict(dict)
    for r in load_rows(os.path.join(raw, 'h10_inventory*.json')):
        code = BY_SELLER_MKT.get((r.get('seller_id'), r['marketplace']))
        if code and r.get('fulfillment_type') == 'FBA':
            skus[(code, r['asin'])][r['sku']] = {'avail': int((r.get('inventory') or {}).get('available') or 0), 'u30': 0}
    for r in load_rows(os.path.join(raw, 'daily_*.json')):
        code = BY_SELLER_MKT.get((r.get('seller_id'), r['marketplace']))
        if not code or (r.get('fulfillment_type') or '').upper() != 'FBA':
            continue
        vals = list(r['sales_velocity']['values'].values())[-30:]
        rec = skus[(code, r['asin'])].setdefault(r['sku'], {'avail': 0, 'u30': 0})
        rec['u30'] += sum(float(v or 0) for v in vals)

    packs = {}
    cp = os.path.join(REF, 'casepacks.csv')
    if os.path.exists(cp):
        with open(cp, newline='') as f:
            for r in csv.DictReader(f):
                if r.get('units_per_box'):
                    packs[(r['account'], r['sku'])] = (int(r['units_per_box']), r.get('source') or '')

    def pick_sku(row):
        cands = {}
        for mk in row['markets']:
            code = f"{row['brand']}_{mk}"
            for s, v in skus.get((code, row['asin']), {}).items():
                c = cands.setdefault(s, {'avail': 0, 'u30': 0})
                c['avail'] = max(c['avail'], v['avail'])
                c['u30'] += v['u30']
        if not cands:
            return row.get('sku'), 'dashboard SKU (no FBA SKU found in inventory)', []
        ranked = sorted(cands.items(), key=lambda kv: (-kv[1]['u30'], -kv[1]['avail'], kv[0]))
        others = [s for s, _ in ranked[1:]]
        return ranked[0][0], ('only FBA SKU' if not others else 'top FBA SKU by 30-day FBA units'), others

    def pack_for(dest, row, sku):
        for code in [dest] + [f"{row['brand']}_{m}" for m in row['markets']]:
            if (code, sku) in packs:
                return packs[(code, sku)]
        return None, None

    out_dir = os.path.join(ROOT, 'q4', 'files', week)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    lines = defaultdict(list)
    for row in snap['rows']:
        if row['blocked'] or row['ship_now'] <= 0:
            continue
        sku, sku_basis, others = pick_sku(row)
        upb, src = pack_for(row['account'], row, sku)
        qty = row['ship_now']
        boxes = None
        if upb:
            boxes = math.ceil(qty / upb)
            qty = boxes * upb
        lines[row['account']].append({
            'sku': sku, 'qty': qty, 'upb': upb, 'boxes': boxes, 'asin': row['asin'], 'name': row['name'],
            'ship_now': row['ship_now'], 'status': row['status'], 'cover': row['cover_avail'],
            'available': row['available'], 'inbound': row['inbound'], 'pack_src': src or '',
            'sku_basis': sku_basis, 'other_skus': ', '.join(others), 'markets': ' '.join(row['markets']),
            'next_ship_by': row['schedule'][0]['ship_by'] if row['schedule'] else '',
        })

    manifest = []
    review = openpyxl.Workbook()
    rs = review.active
    rs.title = 'Summary'
    rs.append([f'Q4 shipment files, {week} (as of {snap["as_of"]})'])
    rs['A1'].font = Font(bold=True, size=13)
    rs.append(['Upload each file in Send to Amazon > Choose inventory to send > File upload. Check ship-from and marketplace first.'])
    rs.append([])
    rs.append(['File', 'Create the workflow in', 'SKUs', 'Units', 'SKUs without case pack'])
    for c in rs[4]:
        c.font = Font(bold=True)
    for dest in sorted(lines, key=lambda d: list(DEST).index(d) if d in DEST else 99):
        items = sorted(lines[dest], key=lambda x: -x['qty'])[:MAX_ROWS]
        label, units, where = DEST.get(dest, (dest, 'metric', dest))
        wb = openpyxl.load_workbook(os.path.join(REF, f'sta_template_{units}.xlsx'))
        ws = wb[SHEET]
        for i, it in enumerate(items):
            r = FIRST_ROW + i
            ws.cell(r, 1, it['sku'])
            ws.cell(r, 2, it['qty'])
            if it['upb']:
                ws.cell(r, 3, it['upb'])
                ws.cell(r, 4, it['boxes'])
        fname = f"STA_{label.split(' (')[0].replace(' ', '_')}_{week}.xlsx"
        wb.save(os.path.join(out_dir, fname))
        no_pack = sum(1 for it in items if not it['upb'])
        manifest.append({'account': dest, 'label': label, 'file': fname, 'where': where,
                         'skus': len(items), 'units': sum(it['qty'] for it in items), 'no_case_pack': no_pack})
        rs.append([fname, where, len(items), sum(it['qty'] for it in items), no_pack])
        sh = review.create_sheet(label.split(' (')[0][:31])
        hdr = ['Merchant SKU', 'Quantity (upload)', 'Units per box', 'Boxes', 'ASIN', 'Product', 'Dashboard ship now',
               'Status', 'Days of cover', 'Available', 'Inbound', 'Next ship-by', 'Case pack source', 'SKU choice', 'Other FBA SKUs', 'Markets']
        sh.append(hdr)
        for c in sh[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill('solid', fgColor='FFE0B2')
        for it in items:
            sh.append([it['sku'], it['qty'], it['upb'], it['boxes'], it['asin'], it['name'], it['ship_now'], it['status'],
                       it['cover'], it['available'], it['inbound'], it['next_ship_by'],
                       it['pack_src'] or 'MISSING: NIC to add units per box', it['sku_basis'], it['other_skus'], it['markets']])
        for col, w in zip('ABCDEFGHIJKLMNOP', [18, 10, 9, 7, 12, 40, 10, 12, 9, 9, 9, 11, 30, 26, 22, 14]):
            sh.column_dimensions[col].width = w
        sh.freeze_panes = 'B2'
    rs.column_dimensions['A'].width = 30
    rs.column_dimensions['B'].width = 60
    review_name = f'Q4_ship_plan_{week}.xlsx'
    review.save(os.path.join(out_dir, review_name))
    with open(os.path.join(out_dir, 'manifest.json'), 'w') as f:
        json.dump({'week': week, 'as_of': snap['as_of'], 'review': review_name, 'files': manifest}, f, indent=1)
    for m in manifest:
        print(f"{m['file']}: {m['skus']} SKUs, {m['units']:,} units, {m['no_case_pack']} without case pack")
    print(f'review: {review_name}')


if __name__ == '__main__':
    main()
