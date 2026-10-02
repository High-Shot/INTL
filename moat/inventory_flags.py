#!/usr/bin/env python3
"""Turn moat-inventory-monitor output into stock_out and cover_short flags, then sync them.

  python3 inventory_flags.py <monitor_output.json> [--flags-dir DIR] [--dry-run]

Rules (owner decision 2026-10-02: always promote into thin stock):
  stock_out   = available == 0 and the ASIN sold in the last 30 days (velocity_daily > 0)
  cover_short = available > 0, cover_available_days < COVER_SHORT_DAYS, and inbound == 0
                (inbound > 0 counts as landing in time: the monitor has no ETA, and the
                 owner's rule is to keep promoting unless a stockout is certain)
Below-8-weeks never sets a flag. Prints the do_not_scale bridge list (MKT:ASIN) last.
"""
import argparse, json, os, sys

COVER_SHORT_DAYS = 14
HERE = os.path.dirname(os.path.abspath(__file__))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build(findings, run_date):
    by = {}
    for f in findings:
        if f.get('scope', 'asin') != 'asin' or not f.get('asin'):
            continue
        k = f'{f["brand"]}|{f["marketplace"]}|{f["asin"].upper()}'
        by.setdefault(k, f)          # first finding per ASIN carries the same stock numbers
    out, skipped = [], []
    for k, f in sorted(by.items()):
        avail, inbound = num(f.get('available')), num(f.get('inbound'))
        vel, cover = num(f.get('velocity_daily')), num(f.get('cover_available_days'))
        if avail is None or vel is None:
            skipped.append(k); continue
        ev = {'available': avail, 'inbound': inbound, 'velocity_daily': vel,
              'cover_available_days': cover, 'finding': f.get('id'), 'run_date': run_date}
        if avail <= 0 and vel > 0:
            out.append({'type': 'stock_out', 'key': k, 'evidence': ev,
                        'reason': f'0 available, selling {vel:g}/day' + (f', {inbound:g} inbound' if inbound else ', nothing inbound')})
        elif avail > 0 and vel > 0 and cover is not None and cover < COVER_SHORT_DAYS and not inbound:
            out.append({'type': 'cover_short', 'key': k, 'evidence': ev,
                        'reason': f'{cover:g} days of available cover, nothing inbound'})
    return out, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('monitor_output')
    ap.add_argument('--flags-dir', default=os.path.join(HERE, 'flags'))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    doc = json.load(open(a.monitor_output))
    run_date = (doc.get('run') or {}).get('date')
    new, skipped = build(doc.get('findings') or [], run_date)
    if skipped:
        print(f'skipped {len(skipped)} ASINs with no stock or velocity number', file=sys.stderr)
    for t in ('stock_out', 'cover_short'):
        print(f'{t}: {sum(n["type"] == t for n in new)}')
    if a.dry_run:
        print(json.dumps(new, indent=1)); return
    sys.path.insert(0, HERE)
    import flags as F
    F.sync(a.flags_dir, 'moat-inventory-monitor', ['stock_out', 'cover_short'], new)
    print('do_not_scale=' + ','.join(sorted({f'{n["key"].split("|")[1]}:{n["key"].split("|")[2]}' for n in new})))


if __name__ == '__main__':
    main()
