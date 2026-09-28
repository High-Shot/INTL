#!/usr/bin/env python3
"""
Q4 projection page: data/q4/*.json -> q4/index.html
Reuses the tracker's head, CSS, header and brand ribbon from template.html so both pages stay on one brand.
Usage: python3 scripts/build_q4.py
"""
import json, os, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with open(os.path.join(ROOT, 'template.html')) as f:
        tpl = f.read()
    head = tpl[:tpl.index('</header>') + len('</header>')]
    head = head.replace('<title>NIC Industries · Account Health Tracker</title>', '<title>NIC Industries · Q4 Inventory Projection</title>')
    head = head.replace('<div class="header-title">Account <span>Health</span> Tracker</div>',
                        '<div class="header-title">Q4 <span>Inventory</span> Projection</div>')
    head = head.replace('<div class="lbl">Week</div>', '<div class="lbl">Projection</div>')
    head = head.replace('<div class="lbl">Generated</div>', '<div class="lbl">Data</div>')
    head = head.replace('<div class="header-right" id="hdr-sources"></div>',
                        '<div class="header-right"><a class="btn" href="../">&larr; Account Health Tracker</a></div>')
    head = head.replace('</style>', '@media(max-width:700px){.header{padding:0 16px;flex-wrap:wrap}.header-right{display:none}.page{padding:16px}.mkt-bar,.week-ctrl,.trend-bar{padding-left:16px;padding-right:16px}}\n</style>')
    tail = tpl[tpl.index('<div class="brand-ribbon">'):tpl.index('<div class="toast"')]
    tail = tail.replace('Account Health Tracker &middot;', 'Q4 Inventory Projection &middot;')
    with open(os.path.join(ROOT, 'q4', 'body.html')) as f:
        body = f.read()
    with open(os.path.join(ROOT, 'q4', 'app.js')) as f:
        app = f.read()
    snaps = []
    for p in sorted(glob.glob(os.path.join(ROOT, 'data', 'q4', '*.json')))[-26:]:
        with open(p) as f:
            s_ = json.load(f)
        mf = os.path.join(ROOT, 'q4', 'files', s_['week'], 'manifest.json')
        if os.path.exists(mf):
            with open(mf) as f:
                s_['files'] = json.load(f)
        snaps.append(s_)
    data_js = 'var Q4 = ' + json.dumps(snaps, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/') + ';'
    html = head + '\n' + body + tail + '<div class="toast" id="toast"></div>\n<script>\n' + data_js + '\n</script>\n<script>\n' + app + '\n</script>\n</body>\n</html>\n'
    out = os.path.join(ROOT, 'q4', 'index.html')
    with open(out, 'w') as f:
        f.write(html)
    print(f'built {out}: {len(snaps)} projection(s), {len(html)//1024} KB, latest {snaps[-1]["week"] if snaps else "none"}')


if __name__ == '__main__':
    main()
