"""Durable one-URL jobs. No schedules, credentials, or raw provider responses stored."""
import json,sqlite3,hashlib
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit,urlencode
from urllib.request import Request,urlopen
from .manifest import public_url
from .store import now

class SubmissionQueue:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        existed=self.path.exists()
        with self.db() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS submission_jobs(
              id TEXT PRIMARY KEY, host TEXT NOT NULL, url TEXT NOT NULL, version TEXT NOT NULL,
              state TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL,
              UNIQUE(host,url,version));
              CREATE TABLE IF NOT EXISTS submission_attempts(
              id INTEGER PRIMARY KEY,job_id TEXT NOT NULL,started TEXT NOT NULL,state TEXT NOT NULL,
              accepted INTEGER,remaining INTEGER,provider_error INTEGER,exported INTEGER NOT NULL DEFAULT 0);
              CREATE UNIQUE INDEX IF NOT EXISTS one_sender_per_host ON submission_jobs(host) WHERE state='sending';''')
        if not existed:self.path.chmod(0o600)
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=20);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def enqueue(self,host,url,version):
        public_url('https://'+host+'/');public_url(url)
        if urlsplit(url).hostname!=host or not isinstance(version,str) or not version.strip():
            raise ValueError('必须使用本站正式地址及明确版本，不能改写其他子域')
        key=hashlib.sha256(json.dumps([host,url,version]).encode()).hexdigest()
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO submission_jobs(id,host,url,version,state,updated) VALUES(?,?,?,?,?,?)',(key,host,url,version,'pending',now()))
        return key
    def snapshot(self):
        with self.db() as c:return [dict(r) for r in c.execute('SELECT * FROM submission_jobs ORDER BY updated,id')]
    def claim(self,host):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM submission_jobs WHERE host=? AND state IN ('sending','unknown')",(host,)).fetchone():return None
            job=c.execute("SELECT * FROM submission_jobs WHERE host=? AND state='pending' ORDER BY updated,id LIMIT 1",(host,)).fetchone()
            if not job:return None
            c.execute("UPDATE submission_jobs SET state='sending',updated=? WHERE id=?",(now(),job['id']))
            aid=c.execute("INSERT INTO submission_attempts(job_id,started,state) VALUES(?,?,'sending')",(job['id'],now())).lastrowid
            return dict(job),aid
    def finish(self,job,aid,response):
        state='unknown';accepted=None;remaining=None;error=None
        if isinstance(response,dict):
            count=response.get('success');quota=response.get('remain');err=response.get('error')
            remaining=quota if type(quota) is int and quota>=0 else None
            error=err if type(err) is int else None
            if type(count) is int and count==1 and not response.get('not_valid') and not response.get('not_same_site') and error is None:
                state='sent';accepted=1
            elif error is not None or response.get('not_valid') or response.get('not_same_site'):
                state='pending' if error==400 and response.get('message')=='over quota' else 'failed';accepted=0
        with self.db() as c:
            c.execute('UPDATE submission_jobs SET state=?,updated=? WHERE id=?',(state,now(),job['id']))
            c.execute('UPDATE submission_attempts SET state=?,accepted=?,remaining=?,provider_error=? WHERE id=?',(state,accepted,remaining,error,aid))
        return {'job_id':job['id'],'attempt_id':aid,'state':state,'accepted':accepted,'remaining':remaining,'provider_error':error}
    def run(self,host,token,send,limit=10):
        if not token:raise ValueError('缺少该站授权凭据')
        if type(limit) is not int or not 1<=limit<=10:raise ValueError('单次预算为 1—10；实际配额以回执为准')
        receipts=[]
        for _ in range(limit):
            claimed=self.claim(host)
            if not claimed:break
            job,aid=claimed
            try:response=send(host,token,job['url'])
            except Exception:response=None
            r=self.finish(job,aid,response);receipts.append(r)
            if r['state']!='sent' or r['remaining']==0:break
        return receipts
    def reconcile(self,jid,state,evidence):
        if state not in ('pending','sent') or not isinstance(evidence,str) or len(evidence.strip())<8:
            raise ValueError('人工核验需要证据及 pending/sent 状态')
        if any(x in evidence.lower() for x in ('token=','password=','secret=')):raise ValueError('证据不得包含凭据')
        with self.db() as c:
            row=c.execute('SELECT state FROM submission_jobs WHERE id=?',(jid,)).fetchone()
            if not row or row['state'] not in ('sending','unknown','failed'):raise ValueError('该任务无需人工恢复')
            c.execute('UPDATE submission_jobs SET state=?,evidence=?,updated=? WHERE id=?',(state,evidence,now(),jid))
    def export_receipts(self,store,site_id,runner):
        site=store.site(site_id)
        with self.db() as c:
            rows=c.execute('SELECT a.*,j.host FROM submission_attempts a JOIN submission_jobs j ON j.id=a.job_id WHERE a.exported=0 AND a.state!=?',('sending',)).fetchall()
        count=0
        for a in rows:
            if urlsplit(site['url']).hostname!=a['host']:continue
            safe={k:a[k] for k in ('state','accepted','remaining','provider_error')}
            store.record('submission',dict(site_id=site_id,engine='百度',runner=runner,run_key=f"{a['job_id']}:{a['id']}",observed_at=a['started'],source='Search Steward 执行器',evidence=json.dumps(safe),submitted=1,accepted=a['accepted'] or 0,failed=1 if a['state']=='failed' else 0,status='success' if a['state']=='sent' else 'failed' if a['state']=='failed' else 'unknown'))
            with self.db() as c:c.execute('UPDATE submission_attempts SET exported=1 WHERE id=?',(a['id'],))
            count+=1
        return count

def baidu_sender(host,token,url,allow_http=False):
    # HTTPS certificate is always verified. HTTP requires explicit deployment decision.
    endpoint=('http' if allow_http else 'https')+'://data.zz.baidu.com/urls?'+urlencode({'site':host,'token':token})
    req=Request(endpoint,data=url.encode(),headers={'Content-Type':'text/plain'})
    with urlopen(req,timeout=20) as response:return json.loads(response.read(65536))
