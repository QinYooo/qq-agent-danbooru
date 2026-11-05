"""Focused upload/move tests; no GPU, model downloads or real user library."""
import errno
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'web'))
from app import Application, Handler, ThreadingHTTPServer
from mobile_upload import finish_move, link_original
from runtime_config import load_config
from store import Store
from test_worker import w, FakeTagger, image_bytes


class MobileUploadTest(unittest.TestCase):
    def config(self, root):
        config = load_config()
        config.update(root=str(root), classificationEnabled=True, python=sys.executable)
        return config

    def test_http_upload_replay_live_append_and_directory_recreation(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Application(self.config(Path(folder)/'library'))
            server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
            server.app = app
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            local = f'http://127.0.0.1:{server.server_port}'
            def request(url, body=None, headers=None):
                with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers or {}), timeout=15) as response:
                    return json.loads(response.read())
            def local_post(route, body):
                return request(local+route, json.dumps(body).encode(), {'X-CSRF-Token':app.csrf})
            try:
                with self.assertRaises(urllib.error.HTTPError):
                    request(local+'/api/mobile/start', b'{}')
                with patch('mobile_upload.adapters', return_value=[{'name':'test','address':'127.0.0.1'}]):
                    info = local_post('/api/mobile/start', {'address':'127.0.0.1'})
                self.assertTrue(info['qr'].startswith('data:image/svg+xml;base64,'))
                phone = info['url'].split('/#')[0]
                auth = {'Authorization':'Bearer '+info['url'].split('#token=')[1]}
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request(phone+'/api/status')
                self.assertEqual(error.exception.code,403)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request(phone+'/api/images',headers=auth)
                self.assertEqual(error.exception.code,404)
                buffer=io.BytesIO(); Image.new('RGB',(30,30),'blue').save(buffer,'PNG')
                data=buffer.getvalue(); sha=hashlib.sha256(data).hexdigest(); upload=uuid.uuid4().hex
                headers={**auth,'X-Upload-ID':upload,'X-File-SHA256':sha}
                first=request(phone+'/api/upload?name=album%2Fone.png',data,headers)
                replay=request(phone+'/api/upload?name=album%2Fone.png',data,headers)
                self.assertEqual(first['position'],replay['position'])
                self.assertEqual(app.jobs.get(first['job'])['total'],1)
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    job=app.jobs.get(first['job'])
                    if job['status']=='done':break
                    time.sleep(.05)
                self.assertEqual(job['rejected'],1,job)  # Actual offline child, no model load.
                session=app.mobile.server.session
                original=session.incoming/(upload+'.png')
                self.assertEqual(original.read_bytes(),data)  # Rejections are recoverable.
                missing=session.incoming.with_name(session.id+'-moved')
                self.assertTrue(missing.is_relative_to(Path(folder)))
                session.incoming.rename(missing)
                second=request(phone+'/api/upload?name=two.png',data,{**headers,'X-Upload-ID':uuid.uuid4().hex})
                self.assertEqual(second['job'],first['job'])
                self.assertEqual(second['position'],1)
                self.assertTrue(session.incoming.is_dir())
                with self.assertRaises(urllib.error.HTTPError):
                    request(phone+'/api/upload?name=bad.png',data,{**headers,'X-Upload-ID':uuid.uuid4().hex,'X-File-SHA256':'0'*64})
                self.assertEqual(app.jobs.get(first['job'])['total'],2)
                self.assertFalse(list(session.incoming.glob('*.part')))
                self.assertEqual(len(request(phone+'/api/progress',headers=auth)['items']),2)
                local_post('/api/mobile/stop',{})
                self.assertFalse(app.mobile.info()['active'])
            finally:
                app.mobile.stop();app.jobs.stop();app.jobs.thread.join(timeout=10)
                server.shutdown();server.server_close();thread.join(timeout=5)

    def test_move_and_exact_duplicate_are_verified_and_idempotent(self):
        for cross_volume in (False,True):
            with self.subTest(cross_volume=cross_volume), tempfile.TemporaryDirectory() as folder:
                store=Store(self.config(Path(folder)/'library'))
                incoming=store.state/'incoming'/'phone'/'test';incoming.mkdir(parents=True)
                source=incoming/'one.png';data=image_bytes();source.write_bytes(data)
                class MobileArchive(w.Archive):
                    def write_original(self, target, raw):
                        link_original(source,target,raw,incoming.parent)
                arc=MobileArchive(store.root,FakeTagger())
                try:
                    req={'source':{'path':str(source),'trusted':True}}
                    if cross_volume:
                        with patch('mobile_upload.os.link',side_effect=OSError(errno.EXDEV,'cross-device')):
                            result=arc.process(req)
                    else:result=arc.process(req)
                    self.assertEqual(result['status'],'saved',result)
                    target=store.root/result['files'][0]
                    self.assertEqual(source.read_bytes(),data)  # Survives until DB verification.
                    if not cross_volume:self.assertTrue(source.samefile(target))
                    item={'path':str(source),'sha':result['sha256']}
                    self.assertTrue(finish_move(store,item,result)['moved'])
                    self.assertFalse(source.exists());self.assertEqual(target.read_bytes(),data)
                    self.assertTrue(finish_move(store,item,result)['moved'])
                    source.write_bytes(data)
                    duplicate=arc.process(req)
                    self.assertEqual(duplicate['reason'],'exact_duplicate')
                    self.assertTrue(finish_move(store,item,duplicate)['moved'])
                    self.assertFalse(source.exists());self.assertTrue(target.exists())
                finally:arc.close()

    def test_database_failure_and_bad_destination_preserve_source(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(self.config(Path(folder)/'library'))
            incoming=store.state/'incoming'/'phone'/'test';incoming.mkdir(parents=True)
            source=incoming/'one.png';data=image_bytes();source.write_bytes(data)
            class MobileArchive(w.Archive):
                def write_original(self,target,raw):link_original(source,target,raw,incoming.parent)
            arc=MobileArchive(store.root,FakeTagger())
            try:
                req={'source':{'path':str(source),'trusted':True}}
                with patch.object(arc,'index_image',side_effect=OSError('database_failure')):
                    failed=arc.process(req)
                self.assertEqual(failed['status'],'error')
                self.assertEqual(source.read_bytes(),data)
                self.assertEqual(arc.db.execute('SELECT count(*) FROM images').fetchone()[0],0)
                result=arc.process(req);self.assertEqual(result['status'],'saved')
                target=store.root/result['files'][0]
                # Replace the archive name, without changing the staged hard-link bytes.
                target.unlink();target.write_bytes(b'corrupted')
                result=finish_move(store,{'path':str(source),'sha':result['sha256']},result)
                self.assertEqual(result['reason'],'archive_move_pending')
                self.assertEqual(source.read_bytes(),data)
                self.assertEqual(finish_move(store,{'path':str(source)}, {'status':'duplicate','reason':'near_duplicate'}),{'status':'duplicate','reason':'near_duplicate'})
                self.assertEqual(source.read_bytes(),data)
            finally:arc.close()


if __name__=='__main__':unittest.main()
