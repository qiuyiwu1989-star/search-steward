"""Passive Baidu observer. Never submits URLs or stores raw provider responses."""
import json
from urllib.parse import urlsplit
from uuid import uuid4
from .store import Store,now

def record_baidu(path,root,host,count,response,runner):
    store=Store(path);store.seed(root)
    sites=store.snapshot()['sites']
    site=next((s for s in sites if urlsplit(s['url']).hostname==host),None)
    if not site:
        sid=store.add_site({'name':host,'url':'https://'+host+'/'})
    else: sid=site['id']
    resp=response if isinstance(response,dict) else {}
    accepted=resp.get('success',0)
    if type(accepted) is not int or accepted<0 or accepted>count: accepted=0
    rejected=set()
    for key in ('not_same_site','not_valid'):
        if isinstance(resp.get(key),list):rejected.update(str(x) for x in resp[key])
    failed=min(count-accepted,len(rejected))
    status='unknown'
    if resp.get('error'):status='failed';failed=count-accepted
    elif accepted==count and count>0:status='success'
    elif accepted:status='partial'
    elif failed:status='failed'
    safe={'accepted':accepted,'explicit_rejected':failed,'response_received':bool(resp)}
    if type(resp.get('error')) is int:safe['provider_error_code']=resp['error']
    return store.record('submission',dict(site_id=sid,observed_at=now(),source='百度推送脚本自动回执',engine='百度',runner=runner,run_key=uuid4().hex,evidence=json.dumps(safe),submitted=count,accepted=accepted,failed=failed,status=status))
