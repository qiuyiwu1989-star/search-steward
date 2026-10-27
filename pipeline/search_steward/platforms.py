"""Evidence-based platform registry. No provider connection is inferred from a catalog entry."""
import json
from urllib.parse import urlsplit
from .store import DOMAINS
PLATFORMS=[
 {'id':'baidu','name':'百度','kind':'seo','priority':'重点','next':'核对已验证站点、提交回执及索引量报表','note':'提交接受不等于收录。','docs':'https://ziyuan.baidu.com/'},
 {'id':'google','name':'Google','kind':'seo','priority':'重点','next':'验证 Search Console 站点，提交 sitemap 并导入效果报表','note':'管理权限、地图提交和实际收录分别取证。','docs':'https://developers.google.com/search/docs/monitor-debug/search-console-start'},
 {'id':'bing','name':'Bing','kind':'seo','priority':'重点','next':'核对 Webmaster Tools 站点与现有 IndexNow 回执','note':'IndexNow 调度记录不能替代 Bing 收录报表。','docs':'https://www.bing.com/webmasters/'},
 {'id':'toutiao','name':'头条搜索','kind':'seo','priority':'重点','next':'核实当前官方站长入口及账号可用能力','note':'本轮未验证可用提交入口，暂不创建自动提交。','docs':''},
 {'id':'douyin','name':'抖音搜索','kind':'seo','priority':'重点','next':'登记账号及平台内内容，单独观测搜索表现','note':'平台内内容搜索与独立网站索引分开管理。','docs':''},
 {'id':'doubao','name':'豆包','kind':'geo','priority':'重点','next':'在豆包产品中执行固定问题并保存回答、模式和引用','note':'火山引擎搜索 API 样本与豆包 App 观测分开记录。','docs':'https://docs.volcengine.com/docs/Networkedsearch?lang=zh'},
 {'id':'chatgpt','name':'ChatGPT','kind':'geo','priority':'重点','next':'核对 OAI-SearchBot 访问条件，执行固定问题并保存引用','note':'搜索抓取与训练抓取独立；API 测试不等于 ChatGPT 产品表现。','docs':'https://developers.openai.com/api/docs/bots'},
 {'id':'deepseek','name':'DeepSeek','kind':'geo','priority':'后续','next':'建立相同问题与模式的真实观测','note':'能力及账号接入待核实。','docs':''},
 {'id':'yuanbao','name':'腾讯元宝','kind':'geo','priority':'后续','next':'建立相同问题与模式的真实观测','note':'能力及账号接入待核实。','docs':''},
 {'id':'gemini','name':'Gemini','kind':'geo','priority':'后续','next':'建立相同问题与模式的真实观测','note':'能力及账号接入待核实。','docs':''},
]
QUESTIONS=[
 {'id':'author-v1','text':'该站作者或组织是谁？请列出可核验的公开来源。','check':'核对身份及同名混淆。'},
 {'id':'books-v1','text':'该站有哪些公开作品？请提供原文链接。','check':'核对实际作品与作者归属。'},
 {'id':'framework-v1','text':'该站有哪些原创方法？请说明来源与适用边界。','check':'区分原文、模型推断与待验证假设。'},
]

IDS={p['id'] for p in PLATFORMS}

def report(store,sid):
    store.site(sid)
    with store.db() as c:
        records=[dict(r) for r in c.execute('SELECT * FROM records WHERE site_id=? ORDER BY created DESC',(sid,))]
    for r in records:r['payload']=json.loads(r['payload'])
    records.sort(key=lambda r:(r['payload'].get('observed_at',''),r['created']),reverse=True)
    rows=[]
    for platform in PLATFORMS:
        matches=[r for r in records if r['payload'].get('platform')==platform['id']]
        connections=[r for r in matches if r['kind']=='connection']
        samples=[r for r in matches if r['kind']=='geo']
        own=lambda u:any((urlsplit(u).hostname or '')==d or (urlsplit(u).hostname or '').endswith('.'+d) for d in DOMAINS)
        latest=connections[0]['payload'] if connections else None
        rows.append({**platform,'connection':latest,'record_count':len(matches),'sample_count':len(samples),'product_samples':sum(r['payload'].get('surface')=='product' for r in samples),'api_samples':sum(r['payload'].get('surface')=='api' for r in samples),
                     'cited_samples':sum(any(own(u) for u in r['payload'].get('citations',[])) for r in samples),
                     'unreviewed_samples':sum(r['payload']['accuracy']=='unreviewed' for r in samples),
                     'samples':[r['payload'] for r in samples[:20]],
                     'latest_evidence':matches[0]['payload'] if matches else None})
    return {'platforms':rows,'questions':QUESTIONS,'unassigned_records':sum(not r['payload'].get('platform') for r in records),
            'limitations':['平台状态为有日期的人工核验记录，不表示已建立实时连接','引用统计仅代表已登记样本，不代表全平台覆盖率；不同产品界面、API、模式不可直接比较','历史未标平台的记录保留原样，不自动推断归属']}
