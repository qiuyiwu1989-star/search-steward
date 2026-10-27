"""Bounded public crawl. DNS is resolved and pinned; redirects revalidated."""
import http.client
import hashlib
import ipaddress
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urldefrag
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET
from .store import clean_url, now

UA='SearchSteward/0.1'
LIMIT_BYTES=2_000_000

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, ip, port):
        super().__init__(host, port, timeout=8, context=ssl.create_default_context())
        self.ip=ip
    def connect(self):
        sock=socket.create_connection((self.ip,self.port),self.timeout)
        self.sock=self._context.wrap_socket(sock,server_hostname=self.host)

class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, ip, port):
        super().__init__(host,port,timeout=8); self.ip=ip
    def connect(self):
        self.sock=socket.create_connection((self.ip,self.port),self.timeout)

def fetch(url):
    for _ in range(5):
        clean_url(url, True)
        p=urlsplit(url)
        addresses={row[4][0] for row in socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError('目标解析到非公网地址，已拒绝')
        ip=sorted(addresses)[0]
        con=(PinnedHTTPS if p.scheme=='https' else PinnedHTTP)(p.hostname,ip,p.port or (443 if p.scheme=='https' else 80))
        try:
            con.request('GET',p.path or '/',headers={'User-Agent':UA,'Accept-Encoding':'identity'})
            r=con.getresponse()
            if r.status in (301,302,303,307,308):
                loc=r.getheader('Location')
                if not loc: raise ValueError('重定向缺少地址')
                url=urljoin(url,loc); continue
            raw=r.read(LIMIT_BYTES+1)
            if len(raw)>LIMIT_BYTES: raise ValueError('响应超过 2MB 上限')
            return {'url':url,'status':r.status,'type':r.getheader('Content-Type',''),'robots':r.getheader('X-Robots-Tag',''),'text':raw.decode('utf-8','replace')}
        finally: con.close()
    raise ValueError('重定向超过上限')

class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.title=[]; self.h1=0; self.description=''; self.canonical=[]; self.robots=''; self.links=[]; self.words=[]; self.skip=0; self.intitle=False; self.schema=False
        self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('script','style','noscript'):
            self.skip+=1
            if tag=='script' and a.get('type')=='application/ld+json': self.schema=True
        if tag=='title': self.intitle=True
        if tag=='h1': self.h1+=1
        if tag=='a' and a.get('href'): self.links.append(a['href'])
        if tag=='link' and 'canonical' in a.get('rel','').lower().split(): self.canonical.append(a.get('href',''))
        if tag=='meta':
            name=a.get('name','').lower()
            if name=='description': self.description=a.get('content','')
            if name in ('robots','baiduspider','googlebot'): self.robots+=' '+a.get('content','').lower()
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'): self.skip=max(0,self.skip-1)
        if tag=='title': self.intitle=False
    def handle_data(self,text):
        if self.intitle: self.title.append(text)
        if not self.skip: self.words.append(text)

def crawl(site, limit=50, getter=fetch, budget=240, resume=None):
    started=time.monotonic(); base=site['url']; host=urlsplit(base).netloc
    raw_getter=getter; cache={}
    def getter(url):
        if url not in cache:
            try: cache[url]=raw_getter(url)
            except (OSError, http.client.HTTPException):
                time.sleep(0.25)
                cache[url]=raw_getter(url)
        return cache[url]
    out={'observed_at':now(),'scope':'public_live_sample','page_limit':limit,'pages':[],'findings':[],'discovered':0,'truncated':False,'files':{},'limitations':['正文长度含导航，仅作排查线索','未检测真实收录、排名、移动视觉和 Core Web Vitals','canonical 目标与所有外链未逐一抓取验证','未自动关闭旧问题；需按证据复验']}
    def finding(url,code,title,priority='P1'):
        out['findings'].append(dict(url=url,code=code,title=title,priority=priority))
    rp=RobotFileParser(); robots_known=False; robots_text=''
    for file in ('robots.txt','sitemap.xml','llms.txt'):
        url=urljoin(base,file)
        try:
            r=getter(url); out['files'][file]={'status':r['status'],'url':r['url']}
            if file=='robots.txt':
                if r['status']==200:
                    robots_text=r['text'];rp.parse(robots_text.splitlines());robots_known=True
                elif r['status']==404: rp.parse([]);robots_known=True
            if file=='sitemap.xml' and r['status']!=200: finding(url,'sitemap_unavailable','站点地图不可读取')
        except Exception:
            out['files'][file]={'status':None,'error':'网络、证书或响应限制导致读取失败'}
            if file!='llms.txt': finding(url,'fetch_failed','发现文件读取失败，需复核')
    if not robots_known:
        out['limitations'].append('robots 无法确认，暂停正文抓取');out['truncated']=True;out['blocked']=True;return out
    maps=[urljoin(base,'sitemap.xml')]+(rp.site_maps() or [])
    queue=[base]; mapped=set(); seenmaps=set(); seen=set()
    # Same-host only; cross-host maps belong in that site's separate scan.
    while maps and len(seenmaps)<10 and time.monotonic()-started<budget:
        u=maps.pop(0)
        if u in seenmaps or urlsplit(u).netloc!=host: continue
        seenmaps.add(u)
        try:
            r=getter(u)
            if r['status']!=200: continue
            if '<!DOCTYPE' in r['text'].upper() or '<!ENTITY' in r['text'].upper(): raise ValueError()
            tree=ET.fromstring(r['text'])
            locs=[e.text.strip() for e in tree.iter() if e.tag.split('}')[-1]=='loc' and e.text]
            if tree.tag.split('}')[-1]=='sitemapindex': maps.extend(locs[:100])
            else:
                for v in locs[:10000]:
                    if urlsplit(v).netloc==host:
                        mapped.add(v)
                        if v not in queue: queue.append(v)
        except ET.ParseError: finding(u,'sitemap_invalid','站点地图 XML 无法解析')
        except Exception: finding(u,'sitemap_fetch_failed','站点地图读取失败，需复核网络或响应限制')
    if resume and resume.get('pending'):
        seen=set(resume.get('visited',[]))
        queue=list(dict.fromkeys(resume['pending']+[u for u in queue if u not in seen]))
        out['scope']='public_live_continuation'
    titles={}
    while queue and len(out['pages'])<limit and time.monotonic()-started<budget:
        u=queue.pop(0)
        if u in seen: continue
        seen.add(u)
        try: clean_url(u,True)
        except ValueError: continue
        path=urlsplit(u).path
        if any(path.startswith(x) for x in ('/admin/','/api/','/pipeline/','/data/','/docs/','/uploads/')): continue
        if path.rsplit('/',1)[-1].count('.') and not path.endswith(('.html','.htm')): continue
        if not rp.can_fetch(UA,u):
            out['pages'].append({'url':u,'status':None,'blocked':True})
            if u in mapped: finding(u,'mapped_blocked','地图包含 robots 禁止抓取的网址')
            continue
        try:
            r=getter(u)
            row={'url':u,'final_url':r['url'],'status':r['status'],'in_sitemap':u in mapped}
            out['pages'].append(row)
            if r['status']!=200:
                finding(u,'http_error',f"公开页面返回 HTTP {r['status']}",'P0' if r['status']>=500 else 'P1');continue
            if 'html' not in r['type'] and '<html' not in r['text'].lower(): continue
            p=Page(r['text']); title=''.join(p.title).strip(); chars=len(''.join(p.words).strip())
            noindex='noindex' in (p.robots+' '+r.get('robots','').lower())
            row.update(title=title,description=p.description,canonical=p.canonical,chars=chars,noindex=noindex,h1=p.h1,schema=p.schema)
            if noindex:
                if u in mapped: finding(u,'mapped_noindex','地图包含 noindex 页面')
                continue
            if not title: finding(u,'title_missing','页面缺少标题')
            canonical=urljoin(r['url'],p.canonical[0]) if len(p.canonical)==1 else None
            identity=(canonical,hashlib.sha256(''.join(p.words).encode()).hexdigest())
            if title and title in titles and not (canonical and identity in titles[title]):
                finding(u,'title_duplicate','标题与本次另一页面重复','P2')
            if title: titles.setdefault(title,set()).add(identity)
            if not p.description: finding(u,'description_missing','页面缺少描述','P2')
            if len(p.canonical)!=1: finding(u,'canonical_count','页面应有一个明确的 canonical')
            elif not urlsplit(p.canonical[0]).scheme: finding(u,'canonical_relative','canonical 为相对地址，需核对','P2')
            if chars<150: finding(u,'thin_html','原始 HTML 正文偏少，需核对 JS 内容')
            if not p.h1: finding(u,'h1_missing','页面缺少一级标题','P2')
            for link in p.links:
                v=urldefrag(urljoin(r['url'],link))[0]
                if urlsplit(v).netloc==host and v not in seen and v not in queue and len(queue)<10000: queue.append(v)
        except Exception:
            if not out['pages'] or out['pages'][-1]['url']!=u: out['pages'].append({'url':u,'status':None})
            finding(u,'fetch_failed','页面读取失败，需复核网络或证书')
    out['discovered']=len(set(queue)|seen|mapped)
    out['truncated']=bool(queue or maps)
    out['crawl_state']={'pending':queue[:10000],'visited':list(seen)[:20000]} if queue else None
    out['duration_seconds']=round(time.monotonic()-started,1)
    return out

def run_scan(store,rid,site,limit=50):
    try:
        import json
        with store.db() as c:
            previous=c.execute("SELECT result FROM scans WHERE site_id=? AND state='complete' ORDER BY started DESC LIMIT 20",(site['id'],)).fetchall()
        last=next((json.loads(row['result']) for row in previous if row['result'] and json.loads(row['result']).get('pages')),None)
        resume=last.get('crawl_state') if last else None
        result=crawl(site,limit,resume=resume)
        store.finish_scan(rid,result,'blocked' if result.get('blocked') else 'complete')
    except Exception: store.finish_scan(rid,{'error':'巡检执行失败，请重新检查任务与网络'},'failed')
