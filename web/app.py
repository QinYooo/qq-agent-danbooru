"""Loopback-only local gallery. Python stdlib + the already installed Pillow."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit,parse_qs,unquote
import uuid
from PIL import Image,ImageOps
from store import Store,Conflict,dumps
from jobs import Jobs,EXTENSIONS
from runtime_config import load_config

HERE=Path(__file__).resolve().parent
Image.MAX_IMAGE_PIXELS=40000000


class Application:
    def __init__(self,config):
        self.config=config;self.store=Store(config);self.jobs=Jobs(self.store);self.csrf=secrets.token_urlsafe(32)
        self.thumb_slots=threading.Semaphore(4)

    def thumbnail(self,sha,trash=None,size=480):
        size=240 if size<=240 else 480 if size<=480 else 1200
        original=self.store.file_for(sha,trash)
        target=self.store.cache/(sha+'-'+str(size)+'.webp')
        if not target.exists():
            with self.thumb_slots:
                if not target.exists():
                    with Image.open(original) as image:
                        image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((size,size),Image.Resampling.LANCZOS)
                        buffer=io.BytesIO();image.save(buffer,'WEBP',quality=82,method=3)
                    temporary=target.with_suffix('.'+uuid.uuid4().hex+'.tmp')
                    temporary.write_bytes(buffer.getvalue());os.replace(temporary,target)
        return target


class Handler(BaseHTTPRequestHandler):
    server_version='DanbooruLocal/1.0'
    protocol_version='HTTP/1.1'

    @property
    def app(self):return self.server.app

    def log_message(self,fmt,*args):
        if args and str(args[0]).startswith(('GET /api/health','GET /media/')):return
        print(time.strftime('%H:%M:%S'),fmt%args,flush=True)

    def headers_for(self,code,mime,length,cache='no-store'):
        self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(length))
        self.send_header('Cache-Control',cache);self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")

    def reply(self,value,code=200):
        data=dumps(value).encode('utf-8');self.headers_for(code,'application/json; charset=utf-8',len(data));self.end_headers();self.wfile.write(data)

    def send_file(self,file,mime=None,download=False):
        size=file.stat().st_size
        cache='no-store' if download or file.parent==HERE/'static' else 'private,max-age=60'
        self.headers_for(200,mime or mimetypes.guess_type(file.name)[0] or 'application/octet-stream',size,cache)
        if download:self.send_header('Content-Disposition','attachment; filename="'+file.name+'"')
        self.end_headers()
        with file.open('rb') as source:
            while chunk:=source.read(256*1024):self.wfile.write(chunk)

    def params(self):
        return {k:v[-1] for k,v in parse_qs(urlsplit(self.path).query).items()}

    def json_body(self):
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=4*1024*1024:raise ValueError('请求内容过大或为空')
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def check_origin(self,write=False):
        port=self.server.server_port
        if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'):
            raise PermissionError('只允许本机访问')
        origin=self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
            raise PermissionError('不允许跨站访问')
        if write and not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),self.app.csrf):
            raise PermissionError('页面会话已过期，请刷新后重试')

    def do_GET(self):
        try:
            self.check_origin();route=urlsplit(self.path).path;p=self.params();store=self.app.store
            if route=='/api/health':return self.reply({'ok':True,'app':'danbooru-manager','version':'1.1.0'})
            if route=='/api/bootstrap':return self.reply({'csrf':self.app.csrf,'root':str(store.root),'stats':store.stats(),'classificationEnabled':self.app.config.get('classificationEnabled',True)})
            if route=='/api/stats':return self.reply(store.stats())
            if route=='/api/images':return self.reply(store.list_images(p))
            if route.startswith('/api/images/'):return self.reply(store.detail(route.rsplit('/',1)[1]))
            if route=='/api/tags':return self.reply(store.tags(p.get('q',''),p.get('kind','general'),p.get('scope')=='library',min(240,int(p.get('limit',60))),max(0,int(p.get('offset',0)))))
            if route=='/api/trash':return self.reply(store.trash_list())
            if route=='/api/jobs':return self.reply(self.app.jobs.get())
            if route.startswith('/api/jobs/'):return self.reply(self.app.jobs.get(route.rsplit('/',1)[1]))
            if route.startswith('/media/'):
                parts=route.split('/');sha=parts[2];mode=parts[3] if len(parts)>3 else 'thumb'
                if mode=='thumb':return self.send_file(self.app.thumbnail(sha,p.get('trash'),int(p.get('size',480))),'image/webp')
                if mode in ('original','download'):return self.send_file(store.file_for(sha,p.get('trash')),download=mode=='download')
            name='index.html' if route=='/' else route.removeprefix('/static/') if route.startswith('/static/') else None
            if name in ('index.html','app.js','styles.css'):
                mime={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','styles.css':'text/css; charset=utf-8'}[name]
                return self.send_file(HERE/'static'/name,mime)
            raise FileNotFoundError('页面不存在')
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as error:self.handle_error(error)

    def do_POST(self):
        try:
            self.check_origin(write=True);route=urlsplit(self.path).path;store=self.app.store
            if route=='/api/upload':
                name=self.params().get('name','image');extension=Path(name).suffix.lower()
                if extension not in EXTENSIONS:raise ValueError('请选择 JPG、PNG、WEBP、BMP 或 GIF 图片')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=25*1024*1024:raise ValueError('单张图片须小于25MiB')
                incoming=store.state/'incoming';incoming.mkdir(exist_ok=True)
                token=uuid.uuid4().hex+extension;target=incoming/token
                try:
                    with target.open('xb') as stream:
                        remaining=length
                        while remaining:
                            block=self.rfile.read(min(remaining,256*1024))
                            if not block:raise ValueError('图片传输未完成')
                            stream.write(block);remaining-=len(block)
                except BaseException:target.unlink(missing_ok=True);raise
                return self.reply({'token':token,'name':name})
            body=self.json_body()
            if route=='/api/shutdown':
                self.reply({'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if route.startswith('/api/images/'):
                parts=route.split('/');sha=parts[3];action=parts[4]
                if action=='edit':return self.reply(store.edit(sha,body))
                if action=='flags':return self.reply(store.set_flags(sha,body))
                if action=='delete':return self.reply(store.delete(sha))
            if route=='/api/batch':
                ids=body.get('ids',[])
                if not isinstance(ids,list) or not 1<=len(ids)<=120:raise ValueError('一次最多选择120张')
                results=[]
                for sha in ids:
                    try:
                        value=store.delete(sha) if body.get('action')=='delete' else store.set_flags(sha,body.get('flags',{}))
                        results.append({'sha':sha,'ok':True})
                    except Exception as error:results.append({'sha':sha,'ok':False,'error':str(error)})
                return self.reply({'items':results})
            if route=='/api/import/folder':return self.reply(self.app.jobs.folder(body.get('path','')))
            if route=='/api/import/files':
                files=[]
                for item in body.get('files',[]):
                    token=item.get('token','')
                    import re
                    if not re.fullmatch('[a-f0-9]{32}\\.(jpg|jpeg|png|gif|webp|bmp)',token):raise ValueError('无效的上传编号')
                    file=(store.state/'incoming'/token).resolve(strict=True)
                    if not file.is_relative_to(store.state/'incoming') or file.is_symlink():raise ValueError('无效的上传文件')
                    files.append({'path':str(file),'name':str(item.get('name',token))[:500]})
                return self.reply(self.app.jobs.create('网页文件导入',files))
            if route.startswith('/api/jobs/') and route.endswith('/cancel'):return self.reply(self.app.jobs.cancel(route.split('/')[3]))
            if route.startswith('/api/trash/') and route.endswith('/restore'):return self.reply(store.restore(route.split('/')[3]))
            raise FileNotFoundError('接口不存在')
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as error:self.handle_error(error)

    def handle_error(self,error):
        code=403 if isinstance(error,PermissionError) else 409 if isinstance(error,Conflict) else 404 if isinstance(error,FileNotFoundError) else 400 if isinstance(error,(ValueError,KeyError,TypeError)) else 500
        try:self.reply({'error':str(error)[:300]},code)
        except (BrokenPipeError,ConnectionResetError):pass


def serve(config):
    app=Application(config)
    server=ThreadingHTTPServer(('127.0.0.1',int(config['port'])),Handler);server.daemon_threads=True;server.app=app
    print('Danbooru Manager http://127.0.0.1:'+str(server.server_port),flush=True)
    try:server.serve_forever(poll_interval=.5)
    finally:
        app.jobs.stop();server.server_close();app.jobs.thread.join(timeout=60)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',default=None)
    serve(load_config(parser.parse_args().config))
