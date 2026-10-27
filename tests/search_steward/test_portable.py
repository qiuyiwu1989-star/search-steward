import os,sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from app import create_app
from search_steward.store import Store
from search_steward.coverage import inventory
class PortableTests(unittest.TestCase):
 def test_independent_auth_and_private_files(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'STEWARD_TOKEN':'x'*32,'QIU_SEARCH_STEWARD_DB':d+'/state.sqlite3','STEWARD_CONTENT_ROOT':d}):
   client=create_app().test_client()
   self.assertEqual(client.get('/api/inside/search-steward/snapshot').status_code,403)
   self.assertEqual(client.get('/api/inside/search-steward/snapshot',headers={'Authorization':'Bearer '+'x'*32}).status_code,200)
   for path in ['/assets/../../app.py','/assets/state.sqlite3','/.env','/sites.json']:
    self.assertEqual(client.get(path).status_code,404)
   response=client.get('/');self.assertEqual(response.status_code,200);response.close()
 def test_manifest_unknown_coverage_is_not_failure(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'content.json').write_text(json.dumps({'items':[{'url':'https://qiuyiwu.com/','title':'Public'}]}))
   s=Store(p/'state.sqlite3');report=inventory(p,s)
   self.assertEqual(report['items'][0]['status'],'unchecked')
   self.assertIsNone(report['items'][0]['in_sitemap'])
 def test_missing_or_short_secret_fails_closed(self):
  with patch.dict(os.environ,{'STEWARD_TOKEN':''}):
   with self.assertRaises(RuntimeError):create_app()
