#!/usr/bin/env python3
"""Explicit operator commands. Dry-run default; no scheduler is installed."""
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from search_steward.manifest import read_manifest
from search_steward.gates import check_release
from search_steward.store import Store
from search_steward.submissions import SubmissionQueue,baidu_sender

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['validate','gate','enqueue','queue','submit','export-receipts'])
    p.add_argument('--root',default=os.environ.get('STEWARD_CONTENT_ROOT','.'))
    p.add_argument('--queue-db',default=str(Path.home()/'.local/share/search-steward/queue.sqlite3'))
    p.add_argument('--host');p.add_argument('--limit',type=int,default=10)
    p.add_argument('--execute',action='store_true');p.add_argument('--allow-http',action='store_true')
    p.add_argument('--store-db');p.add_argument('--site-id');p.add_argument('--runner',default='search-steward')
    args=p.parse_args()
    try:
        if args.action=='validate':
            print(json.dumps({'sites':len(read_manifest(args.root,'sites')),'items':len(read_manifest(args.root,'content'))}));return
        if args.action=='gate':
            report=check_release(args.root);print(json.dumps(report,ensure_ascii=False));sys.exit(0 if report['ok'] else 1)
        if Path(args.queue_db).expanduser().resolve().is_relative_to(Path(__file__).resolve().parents[1]):raise ValueError('队列数据库必须位于源码目录之外')
        q=SubmissionQueue(Path(args.queue_db).expanduser())
        if args.action=='enqueue':
            items=read_manifest(args.root,'content')
            for row in items:
                if row.get('indexable',True) and row.get('canonical',row['url'])==row['url']:
                    from urllib.parse import urlsplit
                    q.enqueue(urlsplit(row['url']).hostname,row['url'],row['version'])
            print(json.dumps({'queued_or_existing':len(q.snapshot())}));return
        if args.action=='queue':print(json.dumps(q.snapshot(),ensure_ascii=False));return
        if args.action=='export-receipts':
            if not args.store_db or not args.site_id:raise ValueError('需要 --store-db 和 --site-id')
            print(json.dumps({'exported':q.export_receipts(Store(args.store_db),args.site_id,args.runner)}));return
        if not args.host:raise ValueError('需要 --host，凭据仅用于此域')
        if not args.execute:
            print(json.dumps({'dry_run':True,'pending':sum(x['host']==args.host and x['state']=='pending' for x in q.snapshot())}));return
        # The operator supplies one verified host's credential per invocation.
        receipts=q.run(args.host,os.environ.get('STEWARD_BAIDU_TOKEN',''),lambda h,t,u:baidu_sender(h,t,u,args.allow_http),args.limit)
        print(json.dumps({'receipts':receipts},ensure_ascii=False))
        if any(r['state']!='sent' for r in receipts):sys.exit(1)
    except (ValueError,OSError):
        print(json.dumps({'error':'配置或操作失败；请核对清单、路径、域名、凭据和任务状态。未输出凭据。'},ensure_ascii=False));sys.exit(2)
if __name__=='__main__':main()
