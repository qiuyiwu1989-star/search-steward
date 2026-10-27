"""Group existing tasks without changing their status or claiming a root cause."""
from collections import defaultdict
RULES={
 'http_error':('availability','P0'), 'fetch_failed':('transient','P1'),
 'sitemap_fetch_failed':('transient','P1'), 'sitemap_invalid':('discovery','P1'),
 'sitemap_unavailable':('discovery','P1'), 'mapped_noindex':('indexability','P1'),
 'mapped_blocked':('indexability','P1'), 'canonical_count':('canonical','P1'),
 'canonical_relative':('canonical','P2'), 'title_missing':('metadata','P1'),
 'title_duplicate':('metadata','P2'), 'description_missing':('metadata','P2'),
 'h1_missing':('structure','P2'), 'thin_html':('content_review','P2')}

def grouped_tasks(store):
    groups=defaultdict(list)
    for task in store.snapshot()['tasks']:
        if task['state'] in ('done','deferred'):continue
        key=task.get('finding_key') or ''
        code=key.rsplit('|',1)[-1] if key else 'manual'
        groups[(task['site_id'],code)].append(task)
    output=[]
    for (site,code),tasks in groups.items():
        category,default=RULES.get(code,('manual','P2'))
        priority=min(t['priority'] for t in tasks)
        output.append({'site_id':site,'code':code,'category':category,'priority':priority,
            'task_count':len(tasks),'affected_pages':len({t['evidence'] for t in tasks if t.get('finding_key')}),
            'tasks':[{'id':t['id'],'title':t['title'],'state':t['state'],'evidence':t['evidence']} for t in tasks],
            'note':'同站同规则聚合，不表示已证明同一模板或根因；网络失败需复核。'})
    return sorted(output,key=lambda x:(x['priority'],-x['affected_pages'],x['code']))
