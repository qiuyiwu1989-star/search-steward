#!/usr/bin/env python3
"""Local scheduler / sanitized receipt import; shares the backend database."""
import argparse
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
from search_steward.store import Store
from search_steward.audit import run_scan

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=['scan','import','snapshot'])
    ap.add_argument('--db',default=os.environ.get('QIU_SEARCH_STEWARD_DB',str(Path.home()/'.local/share/qiuyiwu/search-steward.sqlite3')))
    ap.add_argument('--site',help='Exact registered site URL; scan one site at a time')
    ap.add_argument('--file',help='JSON object containing kind and record fields, no secrets')
    ap.add_argument('--limit',type=int,default=50)
    args=ap.parse_args()
    if Path(args.db).resolve().is_relative_to(ROOT.resolve()): ap.error('数据库必须位于公开目录之外')
    content_root=Path(os.environ.get('STEWARD_CONTENT_ROOT',str(ROOT)))
    store=Store(args.db);store.seed(content_root)
    if args.action=='snapshot':
        print(json.dumps(store.snapshot(),ensure_ascii=False));return
    if args.action=='import':
        if not args.file: ap.error('需要 --file')
        d=json.loads(Path(args.file).read_text());kind=d.pop('kind');rid=store.record(kind,d)
        print(json.dumps({'id':rid}));return
    if not 1<=args.limit<=200: ap.error('limit 必须为 1—200')
    sites=store.snapshot()['sites']
    if args.site: sites=[s for s in sites if s['url'].rstrip('/')==args.site.rstrip('/')]
    if not sites: ap.error('站点未登记')
    # Scheduled default scans one least-recently scanned site per invocation.
    if not args.site:
        with store.db() as c:
            latest={r['site_id']:r['t'] for r in c.execute('SELECT site_id,MAX(started) t FROM scans GROUP BY site_id')}
        sites=sorted(sites,key=lambda s:latest.get(s['id'],''))[:1]
    for site in sites:
        rid=store.begin_scan(site['id']);run_scan(store,rid,site,args.limit)
        with store.db() as c: state=c.execute('SELECT state FROM scans WHERE id=?',(rid,)).fetchone()['state']
        print(json.dumps({'id':rid,'state':state}))
        if state!='complete': sys.exit(1)
if __name__=='__main__': main()
