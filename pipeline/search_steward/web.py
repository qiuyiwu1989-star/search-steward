import sqlite3
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, jsonify, request
from .store import Store
from .audit import run_scan


def create_blueprint(path, root, authorize):
    store=Store(path);store.seed(root)
    bp=Blueprint('search_steward',__name__,url_prefix='/api/inside/search-steward')
    pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='seo-audit')
    @bp.before_request
    def guard():
        if not authorize(): return jsonify(error='请登录后台，写入请求需来自本站'),403
        if request.method not in ('GET','HEAD'):
            if request.content_length and request.content_length>24000: return jsonify(error='请求过大'),413
            if not request.is_json: return jsonify(error='请使用 JSON 请求'),415
    @bp.after_request
    def private(resp):
        resp.headers['Cache-Control']='no-store';return resp
    @bp.errorhandler(ValueError)
    def bad(e): return jsonify(error=str(e)),400
    @bp.errorhandler(sqlite3.IntegrityError)
    def conflict(e): return jsonify(error='该站点已存在，或已有巡检正在运行'),409
    def data():
        d=request.get_json()
        if not isinstance(d,dict): raise ValueError('请求应为对象')
        return d
    @bp.get('/snapshot')
    def snapshot(): return jsonify(store.snapshot())
    @bp.get('/coverage')
    def coverage():
        from .coverage import inventory
        return jsonify(inventory(root,store))
    @bp.get('/platforms')
    def platforms():
        from .platforms import report
        return jsonify(report(store,request.args.get('site_id')))
    @bp.post('/sites')
    def sites(): return jsonify(id=store.add_site(data())),201
    @bp.post('/tasks')
    def tasks(): return jsonify(id=store.task(data())),201
    @bp.patch('/tasks/<tid>')
    def update(tid): store.update_task(tid,data());return jsonify(ok=True)
    @bp.post('/records/<kind>')
    def records(kind): return jsonify(id=store.record(kind,data())),201
    @bp.post('/scans')
    def scan():
        d=data();sid=d.get('site_id');site=store.site(sid)
        limit=d.get('limit',50)
        if type(limit) is not int or not 1<=limit<=200: raise ValueError('巡检上限为 1—200 页')
        with store.db() as c:
            # Multi-process bounded queue, stale runs are explicitly interrupted by operator.
            if c.execute("SELECT COUNT(*) FROM scans WHERE state='running'").fetchone()[0]>=2:
                return jsonify(error='已有两个巡检任务，请等待完成'),409
        rid=store.begin_scan(sid)
        pool.submit(run_scan,store,rid,site,limit)
        return jsonify(id=rid),202
    @bp.post('/scans/<rid>/interrupt')
    def interrupt(rid):
        data()
        # Only abandoned runs older than 15 minutes: normal crawl budget is four minutes.
        from datetime import datetime,timezone,timedelta
        with store.db() as c:
            row=c.execute('SELECT * FROM scans WHERE id=?',(rid,)).fetchone()
        if not row or row['state']!='running': raise ValueError('无正在运行的任务')
        if datetime.fromisoformat(row['started'])>datetime.now(timezone.utc)-timedelta(minutes=15): raise ValueError('任务未超时，请等待完成')
        store.finish_scan(rid,{'error':'超时任务已人工标记中断，可重新巡检'},'interrupted')
        return jsonify(ok=True)
    return bp
