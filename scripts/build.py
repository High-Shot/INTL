#!/usr/bin/env python3
"""
Cerakote INTL Health Tracker: snapshots -> index.html
Embeds every data/snapshots/*.json (sorted by week) into template.html at /*__DATA__*/.
Usage: python3 scripts/build.py
"""
import json, os, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_WEEKS = 52


def main():
    files = sorted(glob.glob(os.path.join(ROOT, 'data', 'snapshots', '*.json')))[-MAX_WEEKS:]
    snaps = []
    for p in files:
        with open(p) as f:
            snaps.append(json.load(f))
    with open(os.path.join(ROOT, 'template.html')) as f:
        tpl = f.read()
    data_js = 'var SNAPSHOTS = ' + json.dumps(snaps, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/') + ';'
    html = tpl.replace('/*__DATA__*/', data_js)
    out = os.path.join(ROOT, 'index.html')
    with open(out, 'w') as f:
        f.write(html)
    print(f'built {out}: {len(snaps)} week(s), {len(html)//1024} KB, latest {snaps[-1]["week"] if snaps else "none"}')


if __name__ == '__main__':
    main()
