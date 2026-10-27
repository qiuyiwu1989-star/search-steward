"""Offline publish checks against explicitly authorized public artifacts."""
import json,hashlib
from html.parser import HTMLParser
from urllib.parse import urljoin
from .audit import Page
from .manifest import read_manifest,safe_file

class Schema(HTMLParser):
    def __init__(self,text):
        super().__init__();self.active=False;self.parts=[];self.blocks=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        if tag=='script' and dict(attrs).get('type','').lower()=='application/ld+json':self.active=True;self.parts=[]
    def handle_data(self,text):
        if self.active:self.parts.append(text)
    def handle_endtag(self,tag):
        if tag=='script' and self.active:self.blocks.append(''.join(self.parts));self.active=False

class BodyText(HTMLParser):
    def __init__(self,text):
        super().__init__();self.body=False;self.skip=0;self.words=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        if tag=='body':self.body=True
        if tag in ('head','script','style','nav','footer','header','noscript'):self.skip+=1
    def handle_endtag(self,tag):
        if tag=='body':self.body=False
        if tag in ('head','script','style','nav','footer','header','noscript'):self.skip=max(0,self.skip-1)
    def handle_data(self,text):
        if self.body and not self.skip:self.words.append(text)

def check_release(root):
    errors=[];warnings=[];pages=[]
    try:items=read_manifest(root,'content')
    except (ValueError,OSError) as e:return {'ok':False,'errors':[{'code':'manifest_invalid','detail':str(e)}],'warnings':[],'pages':[]}
    if not items:errors.append({'code':'empty_release','detail':'公开清单为空，不发布空产物'})
    for item in items:
        def add(code,detail,warning=False):(warnings if warning else errors).append({'id':item['id'],'url':item['url'],'code':code,'detail':detail})
        if 'path' not in item:add('artifact_missing','需要明确的本地 HTML 路径');continue
        try:text=safe_file(root,item['path']).read_text()
        except (ValueError,OSError,UnicodeError):add('artifact_unreadable','公开产物无法读取');continue
        p=Page(text);schema=Schema(text)
        canonical=item.get('canonical',item['url']);indexable=item.get('indexable',True)
        if not text.strip() or (indexable and not ''.join(BodyText(text).words).strip()):add('empty_html','原始 HTML 没有可读正文（排除导航与页眉页脚）')
        if not ''.join(p.title).strip():add('title_missing','缺少标题')
        if len(p.canonical)!=1 or urljoin(item['url'],p.canonical[0])!=canonical:add('canonical_mismatch','canonical 与公开清单不一致')
        if indexable and 'noindex' in p.robots:add('noindex_conflict','预期公开索引页设置了 noindex')
        for block in schema.blocks:
            try:
                value=json.loads(block)
                if not isinstance(value,(dict,list)):raise ValueError()
            except ValueError:add('jsonld_invalid','JSON-LD 必须为合法对象或列表')
        if schema.active:add('jsonld_unclosed','JSON-LD 标签没有闭合')
        if not p.description:add('description_missing','缺少搜索摘要',True)
        if len(''.join(p.words).strip())<150:add('thin_html','可读文字偏少，需要人工审查用途',True)
        if not p.h1:add('h1_missing','没有一级标题',True)
        pages.append({'id':item['id'],'url':item['url'],'version':item['version'],'sha256':hashlib.sha256(text.encode()).hexdigest()})
    return {'ok':not errors,'errors':errors,'warnings':warnings,'pages':pages,'limitations':['不判断观点真伪或搜索排名','尚未验证结构化数据语义与正文一致性','薄页只是提醒；正文统计包含导航','上线后仍需验证状态码、HTTP robots 和实际内容摘要']}
