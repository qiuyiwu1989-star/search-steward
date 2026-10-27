import json,sys,tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'pipeline'))
from search_steward.submissions import SubmissionQueue
from search_steward.manifest import read_manifest
from search_steward.gates import check_release
from search_steward.store import Store
from search_steward.issues import grouped_tasks

class FoundationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.q=SubmissionQueue(self.root/'queue.sqlite3')
 def manifest(self,**overrides):
  row=dict(id='home',url='https://qiuyiwu.com/',title='Public',kind='topic',status='published',public=True,version='1',path='index.html')
  row.update(overrides);(self.root/'content.json').write_text(json.dumps({'schema_version':1,'items':[row]}));return row
 def html(self,extra='',canonical='https://qiuyiwu.com/'):
  (self.root/'index.html').write_text(f'<html><head><title>Public</title><link rel="canonical" href="{canonical}">{extra}</head><body><h1>Useful</h1><p>Actual published body</p></body></html>')
 def test_enqueue_dedup_and_exact_domain(self):
  a=self.q.enqueue('qiuyiwu.com','https://qiuyiwu.com/','1');self.assertEqual(a,self.q.enqueue('qiuyiwu.com','https://qiuyiwu.com/','1'))
  self.assertEqual(len(self.q.snapshot()),1)
  with self.assertRaises(ValueError):self.q.enqueue('qiuyiwu.com','https://midu.qiuyiwu.com/','1')
 def test_concurrent_claims_one_host(self):
  for i in range(2):self.q.enqueue('qiuyiwu.com',f'https://qiuyiwu.com/{i}','1')
  with ThreadPoolExecutor(max_workers=2) as ex:results=list(ex.map(lambda _:self.q.claim('qiuyiwu.com'),range(2)))
  self.assertEqual(sum(x is not None for x in results),1)
 def test_unknown_not_resent(self):
  self.q.enqueue('qiuyiwu.com','https://qiuyiwu.com/','1');calls=[]
  def send(*args):calls.append(args);raise TimeoutError()
  receipt=self.q.run('qiuyiwu.com','test-only',send)
  self.assertEqual(receipt[0]['state'],'unknown');self.q.run('qiuyiwu.com','test-only',send)
  self.assertEqual(len(calls),1)
 def test_stale_sender_requires_evidence(self):
  jid=self.q.enqueue('qiuyiwu.com','https://qiuyiwu.com/','1');self.q.claim('qiuyiwu.com')
  self.assertIsNone(self.q.claim('qiuyiwu.com'))
  self.q.reconcile(jid,'pending','Operator checked provider receipt and confirmed no acceptance')
  self.assertIsNotNone(self.q.claim('qiuyiwu.com'))
 def test_quota_stop_and_receipt_export_idempotent(self):
  for i in range(2):self.q.enqueue('qiuyiwu.com',f'https://qiuyiwu.com/{i}','1')
  receipts=self.q.run('qiuyiwu.com','test-only',lambda *a:{'success':1,'remain':0})
  self.assertEqual(len(receipts),1);self.assertEqual([x['state'] for x in self.q.snapshot()].count('pending'),1)
  store=Store(self.root/'store.sqlite3');sid=store.add_site({'name':'Public','url':'https://qiuyiwu.com/'})
  self.assertEqual(self.q.export_receipts(store,sid,'test'),1);self.assertEqual(self.q.export_receipts(store,sid,'test'),0)
  self.assertEqual(len(store.snapshot()['records']),1)
 def test_reject_and_quota_never_drop_job(self):
  self.q.enqueue('qiuyiwu.com','https://qiuyiwu.com/','1')
  r=self.q.run('qiuyiwu.com','test-only',lambda *a:{'error':400,'message':'over quota'})
  self.assertEqual(r[0]['state'],'pending');self.assertEqual(len(self.q.snapshot()),1)
 def test_raw_credentials_not_persisted(self):
  self.q.enqueue('qiuyiwu.com','https://qiuyiwu.com/','1')
  self.q.run('qiuyiwu.com','NEVER-STORE',lambda *a:{'error':401,'message':'token=NEVER-STORE'})
  self.assertNotIn(b'NEVER-STORE',(self.root/'queue.sqlite3').read_bytes())
 def test_manifest_boundary_and_version(self):
  self.manifest();self.assertEqual(len(read_manifest(self.root,'content')),1)
  for bad in [{'status':'draft'},{'public':False},{'path':'../hidden.html'},{'url':'https://qiuyiwu.com/admin/a.html'},{'canonical':'https://midu.qiuyiwu.com/'}]:
   self.manifest(**bad)
   with self.assertRaises(ValueError):read_manifest(self.root,'content')
 def test_symlink_cannot_escape_root(self):
  with tempfile.TemporaryDirectory() as d:
   outside=Path(d)/'outside.html';outside.write_text('private')
   (self.root/'escape.html').symlink_to(outside)
   self.manifest(path='escape.html')
   with self.assertRaises(ValueError):read_manifest(self.root,'content')
 def test_gate_valid_and_blocking_errors(self):
  self.manifest();self.html();self.assertTrue(check_release(self.root)['ok'])
  self.html('<script type="application/ld+json">{"broken":}</script><meta name="robots" content="noindex">')
  result=check_release(self.root);self.assertFalse(result['ok'])
  self.assertTrue({'jsonld_invalid','noindex_conflict'}.issubset({x['code'] for x in result['errors']}))
  self.html(canonical='https://qiuyiwu.com/other');self.assertFalse(check_release(self.root)['ok'])
 def test_title_only_is_not_published_body(self):
  self.manifest();(self.root/'index.html').write_text('<html><head><title>Only title</title><link rel="canonical" href="https://qiuyiwu.com/"></head><body><nav>Menu</nav></body></html>')
  self.assertIn('empty_html',{x['code'] for x in check_release(self.root)['errors']})
 def test_empty_release_not_success(self):
  self.assertFalse(check_release(self.root)['ok'])
 def test_issue_groups_preserve_tasks(self):
  s=Store(self.root/'store.sqlite3');sid=s.add_site({'name':'Public','url':'https://qiuyiwu.com/'})
  rid=s.begin_scan(sid);s.finish_scan(rid,{'findings':[dict(url=f'https://qiuyiwu.com/{i}',code='description_missing',title='Missing description',priority='P2') for i in range(3)]})
  groups=grouped_tasks(s);self.assertEqual(groups[0]['affected_pages'],3);self.assertEqual(groups[0]['task_count'],3)
  self.assertEqual(len(s.snapshot()['tasks']),3)
