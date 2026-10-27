"""Versioned explicit public manifests. No filesystem-wide content discovery."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from .store import clean_url

KINDS={'topic','framework','case','chapter','solution','article','product','page'}
PRIVATE_PARTS={'admin','private','pipeline','.git','drafts','data'}

def public_url(value):
    url=clean_url(value,True)
    if PRIVATE_PARTS.intersection(urlsplit(url).path.lower().split('/')):
        raise ValueError('公开地址包含私有路径')
    return url

def read_manifest(root,kind):
    path=Path(root)/(kind+'.json')
    if not path.exists():return []
    data=json.loads(path.read_text())
    if not isinstance(data,dict) or data.get('schema_version')!=1:
        raise ValueError(f'{kind}.json 需要 schema_version: 1')
    key='sites' if kind=='sites' else 'items'
    rows=data.get(key)
    if not isinstance(rows,list):raise ValueError(f'{key} 必须为列表')
    ids=set();urls=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('清单条目必须为对象')
        ident=row.get('id');url=public_url(row.get('url'))
        if not isinstance(ident,str) or not ident.strip() or ident in ids or url in urls:
            raise ValueError('清单 id/url 必须唯一且非空')
        ids.add(ident);urls.add(url)
        if kind=='sites':
            if not isinstance(row.get('name'),str) or not row['name'].strip():raise ValueError('站点名称必填')
        else:
            if row.get('kind') not in KINDS:raise ValueError('未知内容类型')
            if row.get('status')!='published' or row.get('public') is not True:
                raise ValueError('公开清单只允许已发布且明确公开的内容')
            if not isinstance(row.get('title'),str) or not row['title'].strip():raise ValueError('内容标题必填')
            if not isinstance(row.get('version'),str) or not row['version'].strip():raise ValueError('内容版本必填')
            canonical=row.get('canonical',url);public_url(canonical)
            if urlsplit(canonical).hostname!=urlsplit(url).hostname:
                raise ValueError('跨域 canonical 需要单独人工迁移，不进入默认生成流程')
            if 'indexable' in row and type(row['indexable']) is not bool:raise ValueError('indexable 必须为布尔值')
            if 'path' in row:safe_file(root,row['path'],must_exist=False)
    return rows

def safe_file(root,relative,must_exist=True):
    if not isinstance(relative,str) or Path(relative).is_absolute():raise ValueError('文件路径必须相对内容目录')
    path=(Path(root)/relative).resolve();base=Path(root).resolve()
    if not path.is_relative_to(base) or PRIVATE_PARTS.intersection(Path(relative).parts):raise ValueError('文件来源越过公开边界')
    if must_exist and not path.is_file():raise ValueError('公开产物不存在')
    return path
