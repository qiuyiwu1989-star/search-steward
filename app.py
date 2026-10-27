"""Standalone private management server. Public source does not mean public admin."""
import os,sys,hmac
from pathlib import Path
from flask import Flask,request,send_from_directory
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'pipeline'))
from search_steward.web import create_blueprint

def create_app():
    token=os.environ.get('STEWARD_TOKEN','')
    if len(token)<32: raise RuntimeError('STEWARD_TOKEN must contain at least 32 characters')
    db=Path(os.environ.get('QIU_SEARCH_STEWARD_DB',str(Path.home()/'.local/share/search-steward/state.sqlite3'))).expanduser().resolve()
    if db.is_relative_to(ROOT): raise RuntimeError('Database must be outside the source/web directory')
    app=Flask(__name__,static_folder=None)
    app.config['MAX_CONTENT_LENGTH']=24000
    def authorized():
        value=request.headers.get('Authorization','')
        return hmac.compare_digest(value.encode(),('Bearer '+token).encode())
    app.register_blueprint(create_blueprint(str(db),os.environ.get('STEWARD_CONTENT_ROOT',str(ROOT)),authorized))
    @app.get('/')
    def index():return send_from_directory(ROOT/'admin','search-steward.html')
    @app.get('/assets/<name>')
    def assets(name):
        if name not in ('search-steward.css','search-steward.js'):return '',404
        return send_from_directory(ROOT/'assets',name)
    @app.after_request
    def headers(resp):
        resp.headers['X-Robots-Tag']='noindex, nofollow'
        resp.headers['Cache-Control']='no-store'
        resp.headers['X-Content-Type-Options']='nosniff'
        return resp
    return app

if __name__=='__main__':create_app().run(host='127.0.0.1',port=8765,debug=False)
