import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'web'))
from app import Application,Handler,ThreadingHTTPServer
from test_store import make_library


class ApiTest(unittest.TestCase):
    def test_complete_management_and_import_boundary(self):
        with tempfile.TemporaryDirectory(prefix='danbooru-api-') as folder:
            root=Path(folder)/'library';sha,original=make_library(root)
            from runtime_config import load_config
            config=load_config()
            config['root']=str(root);config['classificationEnabled']=True
            application=Application(config)
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True;server.app=application
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url='http://127.0.0.1:'+str(server.server_port)
            def request(route,body=None,token=None,origin=None,raw=False):
                headers={}
                if token:headers['X-CSRF-Token']=token
                if origin:headers['Origin']=origin
                if body is not None:headers['Content-Type']='application/octet-stream' if raw else 'application/json'
                data=body if raw else json.dumps(body).encode() if body is not None else None
                with urllib.request.urlopen(urllib.request.Request(url+route,data=data,headers=headers),timeout=15) as response:
                    content=response.read();return content if raw else json.loads(content)
            try:
                token=request('/api/bootstrap')['csrf']
                with urllib.request.urlopen(url+'/static/app.js') as script:
                    self.assertEqual(script.headers.get_content_type(),'text/javascript')
                    self.assertEqual(script.headers['Cache-Control'],'no-store')
                with self.assertRaises(urllib.error.HTTPError) as error:request('/api/images/'+sha+'/flags',{'starred':True})
                self.assertEqual(error.exception.code,403)
                with self.assertRaises(urllib.error.HTTPError) as error:request('/api/stats',origin='https://external.invalid')
                self.assertEqual(error.exception.code,403)
                thumb=request('/media/'+sha+'/thumb',raw=True)
                with Image.open(io.BytesIO(thumb)) as image:self.assertEqual(image.format,'WEBP')
                before=original.read_bytes()
                detail=request('/api/images/'+sha)
                request('/api/images/'+sha+'/edit',{'version':detail['version'],'characters':['himesaki_rinami'],'tags':['solo','white_hair'],'category':'SFW'},token)
                self.assertEqual(request('/api/images?tags=white_hair,himesaki_rinami')['total'],1)
                deleted=request('/api/images/'+sha+'/delete',{},token)
                self.assertEqual(request('/api/images')['total'],0)
                request('/api/trash/'+deleted['trashId']+'/restore',{},token)
                self.assertEqual(request('/media/'+sha+'/original',raw=True),before)
                # Upload, commit and filter a real tiny image through the same
                # offline import process. The model need not load for a reject.
                buffer=io.BytesIO();Image.new('RGB',(30,30),(100,120,140)).save(buffer,'PNG')
                uploaded=request('/api/upload?name=small.png',buffer.getvalue(),token,raw=True)
                uploaded=json.loads(uploaded)
                job=request('/api/import/files',{'files':[uploaded]},token)
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    status=request('/api/jobs/'+job['id'])
                    if status['status'] not in ('queued','running'):break
                    time.sleep(.1)
                self.assertEqual(status['status'],'done',status)
                self.assertEqual(status['rejected'],1)
                self.assertEqual(status['items'][0]['reason'],'low_resolution')
                self.assertEqual(request('/api/images')['total'],1)
                self.assertFalse((application.store.state/'incoming'/uploaded['token']).exists())
                source=Path(folder)/'source';source.mkdir()
                duplicate=source/'duplicate.png';duplicate.write_bytes(before)
                small=source/'small.png';small.write_bytes(buffer.getvalue())
                job=request('/api/import/folder',{'path':str(source)},token)
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    status=request('/api/jobs/'+job['id'])
                    if status['status'] not in ('queued','running'):break
                    time.sleep(.1)
                self.assertEqual(status['status'],'done',status)
                self.assertEqual(status['duplicate'],1);self.assertEqual(status['rejected'],1)
                self.assertEqual(duplicate.read_bytes(),before);self.assertTrue(small.exists())
            finally:
                application.jobs.stop();application.jobs.thread.join(timeout=5)
                server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':unittest.main()
