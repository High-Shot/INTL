#!/usr/bin/env python3
"""
Client note generator: turns the latest snapshot into a bi-weekly note for NIC (the client).

Cadence: a note is DUE on even ISO weeks (first one 2026-W38, Mon 14 Sep 2026) or, on any week, when
  - a CRITICAL item is new this week, or
  - any open item has a deadline within 14 days.
Usage: python3 scripts/client_note.py [WEEK]   (defaults to the latest snapshot)
Writes notes/<WEEK>-client-note.md and prints SEND=yes|no plus the reason.
"""
import json, os, sys, re, glob, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESCALATE_DAYS = 14          # CRITICAL open longer than this is called out by age
DEADLINE_WINDOW = 14        # a deadline inside this window forces a same-week note

MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


def load_snaps():
    files = sorted(glob.glob(os.path.join(ROOT, 'data', 'snapshots', '*.json')))
    return [json.load(open(f)) for f in files]


def week_monday(week):
    y, w = week.split('-W')
    return dt.date.fromisocalendar(int(y), int(w), 1)


def find_deadline(text):
    """Pull a deadline date out of free text: 'by 10 Nov 2026', 'due 2026-11-10', 'deadline 2026-08-19'."""
    if not text:
        return None
    m = re.search(r'(?:by|due|deadline|before)\s+(\d{4}-\d{2}-\d{2})', text, re.I)
    if m:
        return dt.date.fromisoformat(m.group(1))
    m = re.search(r'(?:by|due|deadline|before)\s+(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})', text, re.I)
    if m and m.group(2).lower()[:3] in MONTHS:
        return dt.date(int(m.group(3)), MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
    return None


def find_money(text):
    m = re.search(r'(EUR|GBP|USD|€|£|\$)\s?([\d,]+(?:\.\d+)?)', text or '')
    return f'{m.group(1)} {m.group(2)}'.replace('€ ', '€').replace('£ ', '£').replace('$ ', '$') if m else None


def weeks_open(snaps, idx, item_id):
    n = 0
    for k in range(idx, -1, -1):
        if any(i['id'] == item_id for i in snaps[k]['items']):
            n += 1
        else:
            break
    return n


def main():
    snaps = load_snaps()
    if not snaps:
        print('SEND=no no snapshots'); return
    week = sys.argv[1] if len(sys.argv) > 1 else snaps[-1]['week']
    idx = next(i for i, s in enumerate(snaps) if s['week'] == week)
    s, prev = snaps[idx], (snaps[idx - 1] if idx > 0 else None)
    prev_ids = {i['id'] for i in prev['items']} if prev else set()
    today = week_monday(week)
    even_week = int(week.split('-W')[1]) % 2 == 0

    items = [i for i in s['items'] if i['severity'] in ('CRITICAL', 'URGENT')]
    new_crit = [i for i in items if i['severity'] == 'CRITICAL' and i['id'] not in prev_ids and prev is not None]
    soon = []
    for i in items:
        d = find_deadline(i.get('reason')) or find_deadline(i.get('status'))
        i['_deadline'] = d
        if d and 0 <= (d - today).days <= DEADLINE_WINDOW:
            soon.append(i)
        i['_weeks'] = weeks_open(snaps, idx, i['id'])
        i['_money'] = find_money(i.get('reason'))
        opened = i.get('opened')
        i['_days'] = (today - dt.date.fromisoformat(opened)).days if opened and re.match(r'\d{4}-\d{2}-\d{2}$', opened) else None

    send = even_week or bool(new_crit) or bool(soon)
    why = 'scheduled (even ISO week)' if even_week else ('new CRITICAL: ' + ', '.join(i['name'] for i in new_crit) if new_crit else ('deadline within 14 days: ' + ', '.join(i['name'] for i in soon) if soon else 'off-week, nothing forcing'))

    resolved = [i for i in (prev['items'] if prev else []) if i['id'] not in {x['id'] for x in s['items']} and i['severity'] in ('CRITICAL', 'URGENT')]

    def line(i):
        bits = [f"**{i['label']}**", i['name'] + (f" ({i['asin']})" if i.get('asin') else '')]
        why_ = re.sub(r'\s+', ' ', i.get('reason') or '').strip()
        bits.append(why_)
        tail = []
        if i['_deadline']:
            tail.append(f"deadline {i['_deadline'].strftime('%d %b %Y')}")
        if i['_days'] is not None and i['_days'] > ESCALATE_DAYS and i['severity'] == 'CRITICAL':
            tail.append(f"open {i['_days']} days")
        elif i['_weeks'] > 1:
            tail.append(f"open {i['_weeks']} weeks")
        if i.get('status'):
            tail.append(i['status'])
        act = i.get('action')
        return '- ' + ' · '.join(bits) + (f" ({'; '.join(tail)})" if tail else '') + (f"\n  Action: {act}" if act else '')

    client = [i for i in items if i['owner'] == 'client' and i['type'] == 'account']
    ours = [i for i in items if i['owner'] != 'client' and i['type'] == 'account']
    restock_all = [i for i in s['items'] if i['type'] == 'stock' and i['severity'] in ('CRITICAL', 'URGENT')]
    restock = [i for i in restock_all if i.get('restock_rec') is not None or (i.get('restock_est') or 0) >= 10]
    skipped = len(restock_all) - len(restock)

    out = [f"# Amazon account health and restock · week of {today.strftime('%d %b %Y')}", '',
           '_DRAFT for Barcus review before sending. Delete this line._', '']
    out.append(f"Open across all accounts: {s['totals']['CRITICAL']} critical, {s['totals']['URGENT']} urgent. Dashboard: https://high-shot.github.io/INTL/")
    out.append('')
    out.append('## Needs your action (documents, registrations, shipments)')
    out += [line(i) for i in sorted(client, key=lambda x: (x['severity'] != 'CRITICAL', -(x['_days'] or 0)))] or ['- Nothing open.']
    out.append('')
    out.append('## Restock: create FBA shipments')
    if restock:
        by = {}
        for i in restock:
            by.setdefault(i['label'] + (' (' + ' '.join(i['pool_markets']) + ')' if i.get('pool_markets') else ''), []).append(i)
        for k, rows in by.items():
            out.append(f'**{k}**')
            for i in rows:
                q = f"{i['restock_rec']} units (Amazon rec)" if i.get('restock_rec') is not None else (f"~{i['restock_est']} units (estimate, lead time + 30d cover)" if i.get('restock_est') is not None else 'qty TBD')
                sku = i['sku'] or i['asin']
                out.append(f"- {sku} · {i['name']} ({i['asin']}) · available {i['available']} · inbound {i['inbound'] if i['inbound'] is not None else '?'} · {('%.1f' % i['doc']) if i.get('doc') is not None else '?'} days of cover · send {q}")
        if skipped:
            out.append(f"- {skipped} more low-velocity or inbound-covered SKUs are on the dashboard, not listed here.")
    else:
        out.append('- No stock items this cycle.')
    out.append('')
    out.append('## We are handling')
    out += [line(i) for i in ours] or ['- Nothing open.']
    out.append('')
    out.append('## Resolved since last note')
    out += [f"- {i['label']} · {i['name']}" for i in resolved] or ['- None.']
    out.append('')
    out.append('_Generated from the NIC Account Health Tracker. Days of cover use Helium10 stock and 30-day velocity; EU markets share one FBA pool._')

    os.makedirs(os.path.join(ROOT, 'notes'), exist_ok=True)
    path = os.path.join(ROOT, 'notes', f'{week}-client-note.md')
    open(path, 'w').write('\n'.join(out))
    print(f"SEND={'yes' if send else 'no'} {why}")
    print(path)


if __name__ == '__main__':
    main()
