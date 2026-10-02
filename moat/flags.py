#!/usr/bin/env python3
"""MOAT flags: the shared constraint file every agent reads before recommending.

Each flag type has exactly one writer. A writer declares its full current set each run
with `sync`; flags it no longer reports are cleared, so a flag never outlives its evidence.

  python3 flags.py sync --writer moat-inventory-monitor --type stock_out,cover_short --from new.json
  python3 flags.py list [--type cover_short] [--key-prefix "Cerakote Auto|US|"]
  python3 flags.py expire                       # clears flags past expires_at
  python3 flags.py asins --type stock_out,cover_short   # MKT:ASIN list (do_not_scale bridge)

new.json is a list of {"key", "reason", "evidence"?, "scope"?, "target"?, "expires_at"?}.
Files: <dir>/flags.json (active only) and <dir>/flags_history.jsonl (append-only).
"""
import argparse, datetime as dt, json, os, re, sys

WRITERS = {  # flag type -> the only agent allowed to set or clear it
    'stock_out': 'moat-inventory-monitor',
    'cover_short': 'moat-inventory-monitor',
    'catalog_broken': 'moat-catalog-watchdog',
    'health_hold': 'moat-health-monitor',
    'recently_changed': {'moat-auto-apply', 'bid-engine', 'moat-dispatcher'},
    'protected': 'owner',
    'source_stale': 'moat-collector',
}
CLEAR_RULES = {
    'stock_out': 'next inventory run shows available > 0',
    'cover_short': 'inbound lands, or cover back to 14 days',
    'catalog_broken': 'next catalog.json row is clean',
    'health_hold': 'health item closes in the dashboard',
    'recently_changed': '14 days after set',
    'protected': 'owner removes it, or expires_at',
    'source_stale': 'next good pull',
}
KEY_RE = re.compile(r'^(Cerakote Auto|Cerakote Legacy|Prismatic Powders|all)\|([A-Z]{2}|all)\|[A-Za-z0-9_\-]+$')
DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'flags')


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def load(d):
    p = os.path.join(d, 'flags.json')
    if not os.path.exists(p):
        return {'schema_version': 1, 'updated_at': None, 'flags': []}
    return json.load(open(p))


def save(d, doc, events):
    os.makedirs(d, exist_ok=True)
    doc['updated_at'] = now()
    doc['flags'].sort(key=lambda f: (f['type'], f['key'], f.get('target') or ''))
    tmp = os.path.join(d, 'flags.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(tmp, os.path.join(d, 'flags.json'))
    with open(os.path.join(d, 'flags_history.jsonl'), 'a') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')


def fid(ftype, key, target=None):
    return f'{ftype}|{key}' + (f'|{target}' if target else '')


def may_write(writer, ftype):
    w = WRITERS.get(ftype)
    return writer in w if isinstance(w, set) else writer == w


def sync(d, writer, types, new):
    for t in types:
        if t not in WRITERS:
            sys.exit(f'unknown flag type {t}')
        if not may_write(writer, t):
            sys.exit(f'{writer} may not write {t} (owner: {WRITERS[t]})')
    errs = []
    for n in new:
        if n.get('type') not in types:
            errs.append(f'{n.get("key")}: type {n.get("type")} not in this sync ({",".join(types)})')
        if not KEY_RE.match(n.get('key', '')):
            errs.append(f'bad key {n.get("key")!r}')
        if not n.get('reason'):
            errs.append(f'{n.get("key")}: reason is required')
    if errs:
        sys.exit('refused, nothing written:\n  ' + '\n  '.join(errs[:20]))

    doc = load(d)
    ts = now()
    keep = [f for f in doc['flags'] if f['type'] not in types or f['set_by'] != writer]
    old = {f['id']: f for f in doc['flags'] if f['type'] in types and f['set_by'] == writer}
    events, out, seen = [], [], set()
    for n in new:
        i = fid(n['type'], n['key'], n.get('target'))
        if i in seen:
            continue
        seen.add(i)
        prev = old.get(i)
        f = {'id': i, 'type': n['type'], 'key': n['key'], 'scope': n.get('scope', 'asin'),
             'target': n.get('target'), 'set_by': writer,
             'set_at': prev['set_at'] if prev else ts, 'updated_at': ts,
             'reason': n['reason'], 'evidence': n.get('evidence'),
             'expires_at': n.get('expires_at'), 'clear_rule': CLEAR_RULES[n['type']]}
        out.append(f)
        if not prev:
            events.append({'at': ts, 'event': 'set', **f})
        elif prev.get('reason') != f['reason']:
            events.append({'at': ts, 'event': 'update', 'id': i, 'reason': f['reason']})
    for i, f in old.items():
        if i not in seen:
            events.append({'at': ts, 'event': 'clear', 'id': i, 'type': f['type'], 'key': f['key'],
                           'set_by': writer, 'held_days': round((dt.datetime.fromisoformat(ts) -
                                                                 dt.datetime.fromisoformat(f['set_at'])).total_seconds() / 86400, 1)})
    doc['flags'] = keep + out
    save(d, doc, events)
    s = sum(e['event'] == 'set' for e in events)
    c = sum(e['event'] == 'clear' for e in events)
    print(f'sync {writer} {",".join(types)}: {len(out)} active, {s} set, {c} cleared')


def expire(d):
    doc = load(d)
    ts = now()
    live, events = [], []
    for f in doc['flags']:
        if f.get('expires_at') and f['expires_at'] <= ts:
            events.append({'at': ts, 'event': 'expire', 'id': f['id'], 'type': f['type'], 'key': f['key']})
        else:
            live.append(f)
    doc['flags'] = live
    save(d, doc, events)
    print(f'expire: {len(events)} cleared')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['sync', 'list', 'expire', 'asins'])
    ap.add_argument('--dir', default=DEFAULT_DIR)
    ap.add_argument('--writer')
    ap.add_argument('--type', default='')
    ap.add_argument('--from', dest='src')
    ap.add_argument('--key-prefix', default='')
    a = ap.parse_args()
    types = [t for t in a.type.split(',') if t]
    if a.cmd == 'sync':
        if not (a.writer and types and a.src):
            sys.exit('sync needs --writer, --type and --from')
        sync(a.dir, a.writer, types, json.load(open(a.src)))
    elif a.cmd == 'expire':
        expire(a.dir)
    else:
        fl = [f for f in load(a.dir)['flags'] if (not types or f['type'] in types) and f['key'].startswith(a.key_prefix)]
        if a.cmd == 'list':
            print(json.dumps(fl, indent=1, ensure_ascii=False))
        else:
            print(','.join(sorted({f'{f["key"].split("|")[1]}:{f["key"].split("|")[2]}' for f in fl})))


if __name__ == '__main__':
    main()
