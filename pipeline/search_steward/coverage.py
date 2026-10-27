"""Portable explicit public content manifest; never crawl private repository files."""
import json
from pathlib import Path
from .store import clean_url, now
from .manifest import read_manifest

def inventory(root, store):
    path = Path(root) / 'content.json'
    warnings = []
    entries = read_manifest(root, 'content')
    if not path.exists(): warnings.append('尚未提供 content.json 公开内容清单')
    probes = {}
    with store.db() as c:
        for scan in c.execute('SELECT started,result FROM scans WHERE result IS NOT NULL ORDER BY started DESC'):
            result = json.loads(scan['result'])
            for page in result.get('pages', []):
                if page['url'] not in probes: probes[page['url']] = {'checked_at':scan['started'], **page}
    rows = []
    for entry in entries:
        url = clean_url(entry['url'])
        try: clean_url(url, True); managed = True
        except ValueError: managed = False
        probe = probes.get(url)
        rows.append({'url':url, 'title':entry.get('title',url), 'kinds':[entry.get('kind','page')],
                     'sources':['content.json'], 'managed':managed, 'canonical':entry.get('canonical'),
                     'local_html':False, 'local_noindex':False, 'local_chars':None,
                     'in_sitemap':probe.get('in_sitemap') if probe else None, 'linked_from_catalog':None,
                     'checked_url':url if probe else None, 'checked_at':probe.get('checked_at') if probe else None,
                     'live_status':probe.get('status') if probe else None,
                     'status':'outside' if not managed else 'unchecked' if not probe else 'blocked' if probe.get('blocked') else 'failed' if probe.get('status')!=200 else 'noindex' if probe.get('noindex') else 'checked',
                     'thin_html':bool(probe and probe.get('chars') is not None and probe['chars']<150)})
    managed = [x for x in rows if x['managed']]
    return {'as_of':now(), 'items':rows, 'summary':{'total':len(rows),'managed':len(managed),
        'checked':sum(bool(x['checked_at']) for x in managed), 'unchecked':sum(x['status']=='unchecked' for x in managed),
        'missing_map':sum(x['in_sitemap'] is False for x in managed) if any(x['in_sitemap'] is not None for x in managed) else None,'outside':len(rows)-len(managed)}, 'warnings':warnings,
        'limitations':['公开清单来自 content.json；地图和目录覆盖尚未计算，空值表示未知。','实际请求记录不代表被收录。']}
