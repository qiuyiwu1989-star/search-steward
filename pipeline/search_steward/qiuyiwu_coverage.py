"""Join explicit public catalogs with repository maps and persisted live probes."""
import json
from pathlib import Path
from urllib.parse import urljoin,urlsplit,urldefrag
from xml.etree import ElementTree as ET
from .audit import Page
from .store import now,DOMAINS


def inventory(root,store):
    root=Path(root).resolve();rows={};warnings=[];maps=set();links=set()
    def read_json(rel,key):
        p=root/rel
        if not p.exists():warnings.append('缺少公开清单：'+rel);return []
        try:return json.loads(p.read_text())[key]
        except (ValueError,KeyError):warnings.append('公开清单解析失败：'+rel);return []
    def local(rel):
        p=(root/rel).resolve()
        if not p.is_relative_to(root) or any(x in p.relative_to(root).parts for x in ('admin','private','pipeline')):return None
        return p if p.is_file() else None
    def add(url,title,kind,source,path=None):
        url=urldefrag(urljoin('https://qiuyiwu.com/',url))[0]
        parsed=urlsplit(url)
        if parsed.scheme not in ('https','http') or parsed.username or parsed.password or parsed.query:return
        host=parsed.hostname or '';managed=any(host==d or host.endswith('.'+d) for d in DOMAINS)
        page=None
        if path and (p:=local(path)) and p.suffix in ('.html','.htm'):
            try:page=Page(p.read_text())
            except (OSError,UnicodeError):warnings.append('HTML 无法读取：'+path)
        canonical=urljoin(url,page.canonical[0]) if page and len(page.canonical)==1 else None
        # Preserve original URL; canonical is an alias only if its content is actually probed.
        if url not in rows:
            rows[url]={'url':url,'title':title or url,'kinds':[],'sources':[],'managed':managed,'canonical':canonical,'local_html':page is not None,'local_noindex':bool(page and 'noindex' in page.robots),'local_chars':len(''.join(page.words).strip()) if page else None}
        for key,value in [('kinds',kind),('sources',source)]:
            if value not in rows[url][key]:rows[url][key].append(value)
    books=read_json('data/books-index.json','books');map_paths={'sitemap.xml'}
    for b in books:
        folder={'zhuanxing':'ai-transformation','zaowu':'ai-zaowu-v8'}.get(b['slug'],b['source']).rstrip('/')
        home=local(folder+'/index.html');base=b['url'].rstrip('/')+'/'
        if home:
            p=Page(home.read_text());base=urljoin(base,p.canonical[0]) if len(p.canonical)==1 else base
        add(base,b['name'],'书籍','data/books-index.json',folder+'/index.html');map_paths.add(folder+'/sitemap.xml')
        for section in b.get('sections',[]):
            # These two books use virtual chapter IDs, not filesystem paths.
            # Their public read-*.html companions below are the crawlable URLs.
            if folder in ('ai-transformation','ai-zaowu-v8'):continue
            rel=section.get('chapter_file','')
            if not rel or '..' in Path(rel).parts or rel.startswith('/'):continue
            chapter_url=urljoin(base,rel) if local(folder+'/'+rel) else section.get('url')
            if not chapter_url:
                warnings.append('章节地址待核对：'+b['name']+' / '+rel);continue
            add(chapter_url,section.get('chapter') or section.get('heading') or b['name'],'章节','data/books-index.json',folder+'/'+rel)
        # Explicitly generated public reading pages, not a filesystem-wide crawl.
        if folder in ('ai-transformation','ai-zaowu-v8'):
            for p in (root/folder).glob('read-*.html'):
                add(urljoin(base,p.name),''.join(Page(p.read_text()).title),'章节','generated-public-reading',folder+'/'+p.name)
    for rel,key,kind,urlkey,titlekey in [('data/talks-index.json','talks','演讲','url','title'),('data/public/articles.json','items','文章','href','title'),('data/public/products.json','items','项目','url','name'),('data/public/methods-full.json','methods','方法','url','name_zh')]:
        for item in read_json(rel,key):
            url=item.get(urlkey,'')
            if not url:continue
            p=urlsplit(url);path=p.path.lstrip('/')
            if not p.hostname or p.hostname in ('qiuyiwu.com','www.qiuyiwu.com'):
                if not Path(path).suffix:path=path.rstrip('/')+'/index.html'
            else:path=None
            add(url,item.get(titlekey,''),kind,rel,path)
    for rel in sorted(map_paths):
        p=local(rel)
        if not p: warnings.append('缺少本地地图：'+rel);continue
        try:
            tree=ET.fromstring(p.read_text())
            if tree.tag.split('}')[-1]!='urlset':warnings.append('地图索引待进一步展开：'+rel);continue
            maps.update(e.text.strip() for e in tree.iter() if e.tag.split('}')[-1]=='loc' and e.text)
        except (ET.ParseError,OSError):warnings.append('本地地图无法解析：'+rel)
    for rel in ['books.html','public-books.html','methods.html','speakings.html','writing.html']+[{'zhuanxing':'ai-transformation','zaowu':'ai-zaowu-v8'}.get(b['slug'],b['source']).rstrip('/')+'/index.html' for b in books]:
        p=local(rel)
        if not p:continue
        page=Page(p.read_text());base=page.canonical[0] if len(page.canonical)==1 else 'https://qiuyiwu.com/'+rel
        links.update(urldefrag(urljoin(base,u))[0] for u in page.links)
    probes={}
    with store.db() as c:
        for scan in c.execute("SELECT started,result FROM scans WHERE result IS NOT NULL ORDER BY started DESC"):
            result=json.loads(scan['result'])
            for page in result.get('pages',[]):
                for u in (page['url'],page.get('final_url')):
                    if u and u not in probes:probes[u]={'checked_at':scan['started'],**page}
    for row in rows.values():
        candidates={row['url']}
        if row['canonical']:candidates.add(row['canonical'])
        row['in_sitemap']=bool(candidates & maps) if row['managed'] else None
        row['linked_from_catalog']=bool(candidates & links)
        probe=probes.get(row['url']) or (probes.get(row['canonical']) if row['canonical'] else None)
        row['checked_url']=probe.get('url') if probe else None
        row['checked_at']=probe.get('checked_at') if probe else None
        row['status']=('outside' if not row['managed'] else 'unchecked' if not probe else 'blocked' if probe.get('blocked') else 'failed' if probe.get('status')!=200 else 'noindex' if probe.get('noindex') else 'checked')
        row['live_status']=probe.get('status') if probe else None
        row['thin_html']=bool(probe and probe.get('chars') is not None and probe['chars']<150)
    items=list(rows.values());managed=[r for r in items if r['managed']]
    return {'as_of':now(),'items':items,'summary':{'total':len(items),'managed':len(managed),'checked':sum(bool(r['checked_at']) for r in managed),'unchecked':sum(r['status']=='unchecked' for r in managed),'missing_map':sum(r['in_sitemap'] is False and not r['local_noindex'] for r in managed),'outside':len(items)-len(managed)},'warnings':warnings,'limitations':['清单仅来自显式公开内容源，不读取草稿或私有资料','地图和目录入口为服务器文件对照，不能替代线上路由验证','已检查表示存在实际请求记录，不等于可索引或已被搜索引擎收录','canonical 目标的检查可作为内容证据，原入口地址仍需分别核验','未出现在这些公开清单中的站点内容仍可能遗漏']}
