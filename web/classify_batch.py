"""Stdio-only local model adapter. Network is disabled in this child process."""
import errno
import json
import os
from pathlib import Path
import socket
import sys
import time

for name in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_DATASETS_OFFLINE','HF_HUB_DISABLE_TELEMETRY','DO_NOT_TRACK'):
    os.environ[name]='1'


def deny(*args, **kwargs):
    raise RuntimeError('local_import_network_disabled')


socket.socket.connect=deny
socket.socket.connect_ex=lambda *args,**kwargs:errno.ENETUNREACH
socket.create_connection=deny
sys.stdout.reconfigure(encoding='utf-8')
sys.stdin.reconfigure(encoding='utf-8')


def emit(value):
    print(json.dumps(value,ensure_ascii=False),flush=True)


def main():
    initial=json.loads(sys.stdin.readline());config=initial['config'];job=initial['job']
    sys.path.insert(0,config['workerDir'])
    import worker
    from model_runtime import PIXAI_ID,PIXAI_REVISION,PIXAI_FILES

    class LazyTagger:
        model_id=PIXAI_ID
        revision=PIXAI_REVISION
        model_sha=PIXAI_FILES['model.safetensors']
        model=None

        def predict(self,image):
            if self.model is None:
                emit({'event':'phase','phase':'正在加载本地 PixAI 模型，首次需要稍候'})
                self.model=worker.PixAITagger(config['modelDir'])
                emit({'event':'phase','phase':'本地显卡分类中'})
            return self.model.predict(image)

    class LazyType:
        model=None

        def predict(self,image):
            if self.model is None:
                emit({'event':'phase','phase':'正在加载画面类型模型'})
                self.model=worker.ImageTypeClassifier(config['imageTypeModelDir'])
            return self.model.predict(image)

    class LocalArchive(worker.Archive):
        current=None

        def record(self,result,meta=None):
            if result.get('status')=='saved':
                row=self.db.execute('SELECT metadata FROM images WHERE sha=?',(result['sha256'],)).fetchone()
                metadata=json.loads(row[0])
                metadata['managerImport']={'job':job,'name':self.current['name'],'at':time.time()}
                self.db.execute('UPDATE images SET metadata=? WHERE sha=?',(json.dumps(metadata,ensure_ascii=False),result['sha256']))
                self.db.commit()
            return result

    cfg=json.loads(Path(config['importSettings']).read_text(encoding='utf-8-sig'))
    cfg['outputRoot']=config['root']
    archive=LocalArchive(config['root'],LazyTagger(),LazyType())
    archive.db.execute('PRAGMA busy_timeout=30000')
    emit({'event':'ready'})
    try:
        for line in sys.stdin:
            item=json.loads(line)
            archive.current=item
            result=archive.process({'id':item['position'],'source':{'path':item['path'],'trusted':True},'settings':cfg})
            emit({'event':'result','result':result})
    finally:
        archive.close()


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        emit({'event':'fatal','error':str(error)[:300]})
        raise SystemExit(1)
