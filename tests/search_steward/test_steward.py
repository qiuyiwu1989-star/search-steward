import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'pipeline'))
from search_steward.store import Store,clean_url
from search_steward.audit import crawl,fetch
from search_steward.web import create_blueprint
from flask import Flask

class StewardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'private/state.sqlite3')
        self.sid=self.store.add_site({'name':'主站','url':'https://qiuyiwu.com'})
    def receipt(self):
        return dict(site_id=self.sid,observed_at='2026-10-26',source='脱敏日志',engine='百度',runner='电脑B',run_key='20261026',evidence='已接受 3 条',submitted=3,accepted=3,failed=0,status='success')
    def test_persistence_and_concurrent_writes(self):
        def write(i):return self.store.task({'site_id':self.sid,'title':str(i)})
        with ThreadPoolExecutor(max_workers=6) as p: list(p.map(write,range(20)))
        self.assertEqual(len(Store(self.store.path).snapshot()['tasks']),20)
    def test_existing_shared_database_permissions_preserved(self):
        self.store.path.chmod(0o660)
        Store(self.store.path)
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o660)

    def test_task_closure_requires_evidence(self):
        tid=self.store.task({'site_id':self.sid,'title':'修复标题'})
        with self.assertRaises(ValueError):self.store.update_task(tid,{'state':'done'})
        self.store.update_task(tid,{'state':'done','evidence':'已检查线上 HTML'})
        self.assertEqual(self.store.snapshot()['tasks'][0]['state'],'done')
    def test_receipt_idempotent_and_consistent(self):
        d=self.receipt();a=self.store.record('submission',d)
        self.assertEqual(a,self.store.record('submission',d))
        d['evidence']='另一回执'
        with self.assertRaises(ValueError): self.store.record('submission',d)
        d=self.receipt();d['accepted']=4
        with self.assertRaises(ValueError): self.store.record('submission',d)
    def test_unknown_is_not_zero(self):
        self.store.record('metric',dict(site_id=self.sid,observed_at='2026-10-01',period_end='2026-10-26',source='百度',evidence='平台导出',indexed=None,impressions=12,clicks=None))
        self.assertIsNone(self.store.snapshot()['records'][0]['payload']['indexed'])
    def test_credentials_rejected(self):
        d=self.receipt();d['evidence']='https://provider/urls?token=example-secret'
        with self.assertRaises(ValueError):self.store.record('submission',d)
    def test_urls_and_private_dns(self):
        for u in ['file:///etc/passwd','http://localhost/','https://evil.com/','https://qiuyiwu.com/?token=x']:
            with self.assertRaises(ValueError):clean_url(u,True)
        with patch('search_steward.audit.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):fetch('https://qiuyiwu.com/')
    def fixture(self,url):
        path=url.split('qiuyiwu.com')[-1]
        text={'/robots.txt':'User-agent: *\nDisallow: /private.html','/sitemap.xml':'<urlset><url><loc>https://qiuyiwu.com/a.html</loc></url><url><loc>https://qiuyiwu.com/private.html</loc></url></urlset>','/':'<html><title>首页</title><h1>首页</h1><a href="/a.html">正文</a></html>','/a.html':'<html><title>正文</title><meta name="robots" content="noindex"></html>'}.get(path,'')
        return dict(url=url,status=200 if text else 404,type='text/html',robots='',text=text)
    def test_crawl_robots_noindex_and_scope(self):
        calls=[]
        def get(u):calls.append(u);return self.fixture(u)
        r=crawl(self.store.site(self.sid),getter=get)
        codes={f['code'] for f in r['findings']}
        self.assertIn('mapped_noindex',codes);self.assertIn('mapped_blocked',codes)
        self.assertNotIn('https://qiuyiwu.com/private.html',calls)
        self.assertIn('limitations',r)
    def test_bounded_scan_and_reopen(self):
        r=crawl(self.store.site(self.sid),limit=1,getter=self.fixture)
        self.assertTrue(r['truncated']);self.assertEqual(len(r['pages']),1)
        rid=self.store.begin_scan(self.sid);self.store.finish_scan(rid,r)
        t=self.store.snapshot()['tasks'][0];self.store.update_task(t['id'],{'state':'done','evidence':'先前检查通过'})
        rid=self.store.begin_scan(self.sid);self.store.finish_scan(rid,r)
        self.assertEqual(next(x for x in self.store.snapshot()['tasks'] if x['id']==t['id'])['state'],'open')
    def test_scan_continues_frontier(self):
        first=crawl(self.store.site(self.sid),limit=1,getter=self.fixture)
        second=crawl(self.store.site(self.sid),limit=1,getter=self.fixture,resume=first['crawl_state'])
        self.assertNotEqual(first['pages'][0]['url'],second['pages'][0]['url'])
        self.assertEqual(second['scope'],'public_live_continuation')

    def test_canonical_alias_is_not_duplicate_title(self):
        def get(u):
            if u.endswith('robots.txt'): return dict(url=u,status=200,type='text/plain',text='User-agent: *',robots='')
            if u.endswith('sitemap.xml'): return dict(url=u,status=200,type='text/xml',robots='',text='<urlset><url><loc>https://qiuyiwu.com/index.html</loc></url></urlset>')
            return dict(url=u,status=200,type='text/html',robots='',text='<html><title>首页</title><link rel="canonical" href="https://qiuyiwu.com/"><h1>首页</h1></html>')
        r=crawl(self.store.site(self.sid),getter=get)
        self.assertNotIn('title_duplicate',[f['code'] for f in r['findings']])

    def test_transient_get_retry_and_blocked_scan(self):
        calls={}
        def get(u):
            calls[u]=calls.get(u,0)+1
            if calls[u]==1: raise TimeoutError()
            return self.fixture(u)
        with patch('search_steward.audit.time.sleep'):
            r=crawl(self.store.site(self.sid),getter=get)
        self.assertTrue(r['pages']);self.assertNotIn('blocked',r)
        def fail(u):raise TimeoutError()
        with patch('search_steward.audit.time.sleep'):
            r=crawl(self.store.site(self.sid),getter=fail)
        self.assertTrue(r['blocked']);self.assertEqual(r['pages'],[])

    def test_failed_discovery_does_not_erase_frontier(self):
        from search_steward.audit import run_scan
        first=crawl(self.store.site(self.sid),limit=1,getter=self.fixture)
        rid=self.store.begin_scan(self.sid);self.store.finish_scan(rid,first)
        rid=self.store.begin_scan(self.sid);self.store.finish_scan(rid,{'pages':[],'truncated':True})
        rid=self.store.begin_scan(self.sid)
        with patch('search_steward.audit.crawl',return_value={'pages':[],'blocked':True}) as fn:
            run_scan(self.store,rid,self.store.site(self.sid))
        self.assertEqual(fn.call_args.kwargs['resume'],first['crawl_state'])
        self.assertEqual(self.store.snapshot()['scans'][0]['state'],'blocked')

    def test_running_scan_exclusion(self):
        self.store.begin_scan(self.sid)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):self.store.begin_scan(self.sid)
    def test_api_auth_and_validation(self):
        from flask import request
        app=Flask(__name__);app.register_blueprint(create_blueprint(self.store.path,self.root,lambda:request.headers.get('X-Test-Auth')=='yes'))
        c=app.test_client()
        self.assertEqual(c.get('/api/inside/search-steward/snapshot').status_code,403)
        h={'X-Test-Auth':'yes'}
        r=c.get('/api/inside/search-steward/snapshot',headers=h)
        self.assertEqual(r.status_code,200);self.assertEqual(r.headers['Cache-Control'],'no-store')
        r=c.post('/api/inside/search-steward/tasks',headers=h,json={'site_id':self.sid,'title':'测试任务'})
        self.assertEqual(r.status_code,201)
        self.assertEqual(c.patch('/api/inside/search-steward/tasks/'+r.json['id'],headers=h,json={'state':'done'}).status_code,400)
        self.assertEqual(c.post('/api/inside/search-steward/sites',headers=h,json=[]).status_code,400)
    def test_passive_baidu_observer_never_stores_raw_response(self):
        from search_steward.receipts import record_baidu
        response={'success':2,'not_valid':['https://qiuyiwu.com/bad'], 'message':'token=must-not-store'}
        record_baidu(self.store.path,self.root,'qiuyiwu.com',3,response,'server')
        r=self.store.snapshot()['records'][0]['payload']
        self.assertEqual(r['status'],'partial');self.assertEqual(r['failed'],1)
        self.assertNotIn('must-not-store',json.dumps(r))
        record_baidu(self.store.path,self.root,'qiuyiwu.com',3,None,'server')
        self.assertEqual(self.store.snapshot()['records'][0]['payload']['status'],'unknown')

    def test_real_auth_csrf_and_bearer(self):
        from flask import request
        from search_steward.auth import authorize
        app=Flask(__name__)
        with app.test_request_context('/',method='POST',headers={'Origin':'https://evil.example'}):
            self.assertFalse(authorize(request,True))
        with app.test_request_context('/',method='POST',headers={'Origin':'https://qiuyiwu.com'}):
            self.assertTrue(authorize(request,True));self.assertFalse(authorize(request,False))
        with app.test_request_context('/',method='POST',headers={'Authorization':'Bearer test-only'}):
            self.assertTrue(authorize(request,False,'test-only'))
            self.assertFalse(authorize(request,False,''))
        with app.test_request_context('/',method='GET'):
            self.assertFalse(authorize(request,False));self.assertTrue(authorize(request,True))

    def test_geo_records_citations_and_review(self):
        d=dict(site_id=self.sid,observed_at='2026-10-26',source='人工观测',question='某书的观点？',product='测试产品',mode='联网',answer='实际回答',evidence='截图记录',accuracy='unreviewed',citations=['https://qiuyiwu.com/'])
        self.store.record('geo',d)
        self.assertEqual(self.store.snapshot()['records'][0]['payload']['accuracy'],'unreviewed')
        d['citations']=['javascript:alert(1)']
        with self.assertRaises(ValueError):self.store.record('geo',d)

if __name__=='__main__':unittest.main()
