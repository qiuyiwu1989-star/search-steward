import json
import os
import re
from contextlib import contextmanager
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

DOMAINS = tuple(d.strip().lower() for d in os.environ.get('STEWARD_DOMAINS', 'example.com').split(',') if d.strip())
STATES = ('open', 'doing', 'verify', 'done', 'deferred')

def now():
    return datetime.now(timezone.utc).isoformat()

def clean_url(value, owned=False):
    value = str(value or '').strip()
    p = urlsplit(value)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password or p.fragment or p.port not in (None, 80, 443):
        raise ValueError('网址必须为不含账号、锚点的 HTTP(S) 地址')
    if p.query:
        raise ValueError('请使用不含查询参数的公开网址，避免保存密钥')
    if owned and not any(p.hostname == d or p.hostname.endswith('.' + d) for d in DOMAINS):
        raise ValueError('仅管理 STEWARD_DOMAINS 中授权的域名及其子域名')
    return value

def required(data, key, limit=2000):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{key} 必填，最长 {limit} 字符')
    return value.strip()

class Store:
    def __init__(self, path):
        self.path = Path(path)
        existed = self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS sites(id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT UNIQUE NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY, site_id TEXT NOT NULL, state TEXT NOT NULL, started TEXT NOT NULL, finished TEXT, result TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_scan ON scans(site_id) WHERE state='running';
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, site_id TEXT NOT NULL, title TEXT NOT NULL, priority TEXT NOT NULL, state TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '', finding_key TEXT UNIQUE, created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, site_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL, dedup TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, subject TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, created TEXT NOT NULL);
            ''')
        if not existed: self.path.chmod(0o600)

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def event(self, c, subject, action, detail):
        c.execute('INSERT INTO events(subject,action,detail,created) VALUES(?,?,?,?)', (subject, action, json.dumps(detail, ensure_ascii=False), now()))

    def seed(self, root):
        manifest = Path(root) / 'sites.json'
        entries = []
        if manifest.exists():
            entries = [(x['name'], x['url']) for x in json.loads(manifest.read_text())['sites']]
        with self.db() as c:
            for name, url in entries:
                c.execute('INSERT OR IGNORE INTO sites VALUES(?,?,?,?)', (uuid4().hex, name, clean_url(url, True).rstrip('/')+'/', now()))

    def site(self, sid):
        with self.db() as c:
            row = c.execute('SELECT * FROM sites WHERE id=?', (sid,)).fetchone()
        if not row:
            raise ValueError('站点不存在')
        return dict(row)

    def add_site(self, d):
        sid = uuid4().hex
        with self.db() as c:
            c.execute('INSERT INTO sites VALUES(?,?,?,?)', (sid, required(d, 'name', 120), clean_url(required(d, 'url'), True).rstrip('/')+'/', now()))
            self.event(c, sid, 'site_added', {})
        return sid

    def snapshot(self):
        with self.db() as c:
            out = {k: [dict(r) for r in c.execute(sql)] for k, sql in {
                'sites': 'SELECT * FROM sites ORDER BY created',
                'tasks': 'SELECT * FROM tasks ORDER BY updated DESC',
                'scans': 'SELECT * FROM scans ORDER BY started DESC LIMIT 100',
                'records': 'SELECT * FROM records ORDER BY created DESC LIMIT 500',
                'events': 'SELECT * FROM events ORDER BY id DESC LIMIT 100'}.items()}
        for r in out['records']: r['payload'] = json.loads(r['payload'])
        for r in out['scans']:
            r['result'] = json.loads(r['result']) if r['result'] else None
            if r['result']: r['result'].pop('crawl_state',None)
        out['as_of'] = now()
        out['limits'] = {'scans':100, 'records':500, 'events':100}
        return out

    def task(self, d):
        self.site(d.get('site_id'))
        priority = d.get('priority', 'P1')
        if priority not in ('P0', 'P1', 'P2'): raise ValueError('优先级无效')
        tid = uuid4().hex
        with self.db() as c:
            c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)', (tid,d['site_id'],required(d,'title',300),priority,'open',str(d.get('owner',''))[:100],'',None,now(),now()))
            self.event(c,tid,'task_created',{})
        return tid

    def update_task(self, tid, d):
        state = d.get('state')
        if state not in STATES: raise ValueError('任务状态无效')
        evidence = str(d.get('evidence','')).strip()
        if len(evidence)>4000: raise ValueError('证据过长')
        if state in ('done','deferred') and not evidence: raise ValueError('完成需复验证据，暂缓需原因')
        with self.db() as c:
            old = c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
            if not old: raise ValueError('任务不存在')
            c.execute('UPDATE tasks SET state=?,owner=?,evidence=?,updated=? WHERE id=?',(state,str(d.get('owner',old['owner']))[:100],evidence,now(),tid))
            self.event(c,tid,'task_updated',{'from':old['state'],'to':state,'evidence':evidence})

    def record(self, kind, d):
        self.site(d.get('site_id'))
        p = {'observed_at': required(d,'observed_at',40), 'source':required(d,'source',200)}
        try: datetime.fromisoformat(p['observed_at'].replace('Z','+00:00'))
        except ValueError: raise ValueError('观测日期无效')
        platform=d.get('platform')
        if platform:
            from .platforms import IDS
            if not isinstance(platform,str) or platform not in IDS: raise ValueError('平台无效')
            p['platform']=platform
        if kind == 'connection':
            if not platform: raise ValueError('请选择平台')
            p['status']=required(d,'status',40)
            if p['status'] not in ('pending','verified','blocked','paused'): raise ValueError('接入状态无效')
            p['evidence']=required(d,'evidence',2000)
            p['next_action']=required(d,'next_action',1000)
        elif kind == 'submission':
            p.update({k:required(d,k,1000) for k in ('engine','runner','run_key','evidence')})
            for k in ('submitted','accepted','failed'):
                n=d.get(k)
                if type(n) is not int or n<0: raise ValueError('提交计数必须为非负整数')
                p[k]=n
            if p['accepted']+p['failed']>p['submitted']: raise ValueError('成功及失败数量不能超过提交数')
            p['status']=required(d,'status',30)
            if p['status'] not in ('success','partial','failed','unknown'): raise ValueError('回执状态无效')
            if p['status']=='success' and (p['submitted']==0 or p['accepted']!=p['submitted']): raise ValueError('全部接受才可标记成功')
        elif kind == 'geo':
            p.update({k:required(d,k,4000) for k in ('question','product','mode','answer','evidence')})
            if platform:
                p['surface']=required(d,'surface',30)
                if p['surface'] not in ('product','api','other'): raise ValueError('观测环境无效')
                qid=d.get('question_id','')
                if qid:
                    from .platforms import QUESTIONS
                    question=next((q for q in QUESTIONS if q['id']==qid),None)
                    if not question or question['text']!=p['question']: raise ValueError('固定问题与版本不一致')
                    p['question_id']=qid
            p['accuracy']=required(d,'accuracy',30)
            if p['accuracy'] not in ('accurate','inaccurate','unreviewed'): raise ValueError('准确性状态无效')
            citations=d.get('citations',[])
            if not isinstance(citations,list) or len(citations)>20: raise ValueError('引用最多20条')
            p['citations']=[clean_url(u) for u in citations]
        elif kind == 'metric':
            p['period_end']=required(d,'period_end',40)
            try:
                if datetime.fromisoformat(p['period_end']) < datetime.fromisoformat(p['observed_at']): raise ValueError()
            except (ValueError,TypeError): raise ValueError('统计结束日期必须不早于开始日期')
            p['evidence']=required(d,'evidence',2000)
            for k in ('indexed','impressions','clicks'):
                n=d.get(k)
                if n is not None and (type(n) is not int or n<0): raise ValueError('指标必须为非负整数或留空')
                p[k]=n
            if all(p[k] is None for k in ('indexed','impressions','clicks')): raise ValueError('至少填写一项真实指标')
        elif kind == 'automation':
            p.update({k:required(d,k,1000) for k in ('engine','runner','schedule','status','evidence')})
            if p['status'] not in ('unverified','active','failed','paused'): raise ValueError('自动化状态无效')
        else: raise ValueError('记录类型无效')
        if re.search(r'(?:token|secret|password|密钥|密码)\s*[=:：]\s*\S+|gh[pousr]_[A-Za-z0-9]+|AKID[A-Za-z0-9]+', json.dumps(p, ensure_ascii=False), re.I):
            raise ValueError('记录疑似包含凭据，请脱敏后保存')
        # Immutable evidence, never accept arbitrary raw provider response / credentials.
        rid=uuid4().hex
        dedup=f"{d['site_id']}:{p['engine']}:{p['runner']}:{p['run_key']}" if kind=='submission' else None
        with self.db() as c:
            if dedup:
                old=c.execute('SELECT id,payload FROM records WHERE dedup=?',(dedup,)).fetchone()
                if old:
                    if json.loads(old['payload'])!=p: raise ValueError('同一运行标识已有不同回执')
                    return old['id']
            c.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',(rid,d['site_id'],kind,json.dumps(p,ensure_ascii=False),now(),dedup))
            self.event(c,rid,'record_added',{'kind':kind,'site_id':d['site_id']})
        return rid

    def begin_scan(self, sid):
        self.site(sid)
        rid=uuid4().hex
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT COUNT(*) FROM scans WHERE state='running'").fetchone()[0] >= 2:
                raise ValueError('已有两个巡检任务，请等待完成')
            c.execute('INSERT INTO scans VALUES(?,?,?,?,?,?)',(rid,sid,'running',now(),None,None))
        return rid

    def finish_scan(self, rid, result, state='complete'):
        with self.db() as c:
            scan=c.execute('SELECT * FROM scans WHERE id=?',(rid,)).fetchone()
            if not scan or scan['state']!='running': return
            c.execute('UPDATE scans SET state=?,finished=?,result=? WHERE id=?',(state,now(),json.dumps(result,ensure_ascii=False),rid))
            for f in result.get('findings',[]):
                key=scan['site_id']+'|'+f['url']+'|'+f['code']
                old=c.execute('SELECT id,state FROM tasks WHERE finding_key=?',(key,)).fetchone()
                if old:
                    if old['state']=='done':
                        c.execute("UPDATE tasks SET state='open',updated=? WHERE id=?",(now(),old['id']))
                        self.event(c,old['id'],'finding_reopened',{'scan':rid})
                else:
                    tid=uuid4().hex
                    c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(tid,scan['site_id'],f['title'],f['priority'],'open','',f['url'],key,now(),now()))
                    self.event(c,tid,'finding_created',{'scan':rid,'url':f['url'],'code':f['code']})
            self.event(c,rid,'scan_finished',{'state':state,'checked':len(result.get('pages',[]))})
