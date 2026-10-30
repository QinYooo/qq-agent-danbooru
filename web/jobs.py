import collections
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid
from store import dumps

EXTENSIONS={'.jpg','.jpeg','.png','.webp','.bmp','.gif'}


class Jobs:
    def __init__(self,store):
        self.store=store;self.queue=queue.Queue();self.stopping=False
        with store.connect() as db:
            db.execute("UPDATE manager_jobs SET status='interrupted',phase='上次运行已中断，请重新选择来源导入' WHERE status IN ('queued','running')")
            db.commit()
        self.thread=threading.Thread(target=self.run,daemon=True,name='local-imports');self.thread.start()

    def create(self,source,files):
        if not self.store.config.get('classificationEnabled',True):raise ValueError('当前为浏览模式，请重新运行安装器配置本地分类环境')
        if not files:raise ValueError('没有找到可导入的图片')
        if len(files)>20000:raise ValueError('一次最多导入20000张图片，请分批选择')
        job=uuid.uuid4().hex;at=time.time()
        with self.store.connect() as db:
            db.execute('INSERT INTO manager_jobs(id,status,phase,source,total,created,updated) VALUES(?,?,?,?,?,?,?)',
                (job,'queued','等待本地分类',source,len(files),at,at))
            db.executemany('INSERT INTO manager_job_items(job,position,name,path,status) VALUES(?,?,?,?,?)',
                [(job,i,item['name'],item['path'],'pending') for i,item in enumerate(files)])
            db.commit()
        self.queue.put(job)
        return {'ok':True,'id':job,'total':len(files)}

    def folder(self,value):
        root=Path(value).resolve(strict=True)
        if not root.is_dir() or root==self.store.root or root.is_relative_to(self.store.root) or self.store.root.is_relative_to(root):
            raise ValueError('请选择图库以外的图片来源文件夹')
        files=[]
        for directory,folders,names in os.walk(root,followlinks=False):
            folders[:]=[name for name in sorted(folders) if not (Path(directory)/name).is_symlink()]
            for name in sorted(names):
                file=Path(directory)/name
                if file.suffix.lower() in EXTENSIONS and not file.is_symlink() and file.resolve().is_relative_to(root):
                    files.append({'path':str(file),'name':str(file.relative_to(root))})
                    if len(files)>20000:raise ValueError('一次最多导入20000张图片')
        return self.create(str(root),files)

    def get(self,job=None):
        with self.store.connect() as db:
            if job:
                row=db.execute('SELECT * FROM manager_jobs WHERE id=?',(job,)).fetchone()
                if not row:raise FileNotFoundError('导入任务不存在')
                result=dict(row)
                result['items']=[dict(item) for item in db.execute('SELECT position,name,status,reason,sha FROM manager_job_items WHERE job=? ORDER BY position DESC LIMIT 100',(job,))]
                result['reasons']=[{'reason':row[0],'count':row[1]} for row in db.execute("SELECT reason,count(*) FROM manager_job_items WHERE job=? AND status IN ('rejected','duplicate','error') GROUP BY reason ORDER BY count(*) DESC",(job,))]
                return result
            return {'items':[dict(row) for row in db.execute('SELECT * FROM manager_jobs ORDER BY created DESC LIMIT 30')]}

    def cancel(self,job):
        with self.store.connect() as db:
            db.execute("UPDATE manager_jobs SET cancel=1,phase='完成当前图片后停止' WHERE id=? AND status IN ('queued','running')",(job,));db.commit()
        return {'ok':True}

    def update(self,job,**values):
        values['updated']=time.time()
        with self.store.connect() as db:
            db.execute('UPDATE manager_jobs SET '+','.join(key+'=?' for key in values)+' WHERE id=?',[*values.values(),job]);db.commit()

    def cancelled(self,job):
        with self.store.connect() as db:
            row=db.execute('SELECT cancel FROM manager_jobs WHERE id=?',(job,)).fetchone()
        return self.stopping or not row or bool(row[0])

    def run(self):
        while not self.stopping:
            try:job=self.queue.get(timeout=1)
            except queue.Empty:continue
            try:self.process(job)
            except Exception as error:self.update(job,status='error',phase='导入停止',error=str(error)[:300])
            finally:self.queue.task_done();self.store.invalidate()

    def process(self,job):
        if self.cancelled(job):self.update(job,status='cancelled',phase='已取消');return
        self.update(job,status='running',phase='正在准备本地筛选流程')
        with self.store.connect() as db:
            files=[dict(row) for row in db.execute('SELECT * FROM manager_job_items WHERE job=? ORDER BY position',(job,))]
        logfile=self.store.state/(job+'.log')
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        process=None
        counts=collections.Counter()
        try:
            with logfile.open('a',encoding='utf-8') as errors:
                process=subprocess.Popen([self.store.config['python'],'-B','-u','-X','utf8',str(Path(__file__).with_name('classify_batch.py'))],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True,encoding='utf-8',creationflags=flags)
                process.stdin.write(dumps({'config':self.store.config,'job':job})+'\n');process.stdin.flush()
                def receive():
                    while True:
                        line=process.stdout.readline()
                        if not line:raise RuntimeError('本地分类进程已退出，请查看导入日志')
                        try:value=json.loads(line)
                        except ValueError:continue
                        if value.get('event')=='phase':self.update(job,phase=value['phase'])
                        elif value.get('event')=='fatal':raise RuntimeError(value['error'])
                        else:return value
                receive()
                for item in files:
                    if self.cancelled(job):break
                    self.update(job,phase='正在分类：'+item['name'])
                    process.stdin.write(dumps(item)+'\n');process.stdin.flush()
                    value=receive();result=value['result'];status=result.get('status','error');counts[status]+=1
                    with self.store.connect() as db:
                        db.execute('UPDATE manager_job_items SET status=?,reason=?,sha=?,result=? WHERE job=? AND position=?',
                            (status,result.get('reason',''),result.get('sha256',''),dumps(result),job,item['position']))
                        db.commit()
                    self.update(job,processed=sum(counts.values()),saved=counts['saved'],rejected=counts['rejected'],duplicate=counts['duplicate'],errors=counts['error'])
                    self.store.invalidate()
                    file=Path(item['path'])
                    if file.resolve().is_relative_to((self.store.state/'incoming').resolve()):file.unlink(missing_ok=True)
                    if result.get('reason') in ('gpu_out_of_memory','insufficient_disk_space'):raise RuntimeError(result['reason'])
                process.stdin.close();process.wait(timeout=30)
                cancelled=self.cancelled(job)
                self.update(job,status='cancelled' if cancelled else 'done',phase='已取消' if cancelled else '导入完成')
        finally:
            if process and process.poll() is None:
                try:process.stdin.close();process.wait(timeout=30)
                except Exception:process.terminate()
            if process and process.stdout:process.stdout.close()

    def stop(self):
        self.stopping=True
