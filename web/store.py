"""Local image archive with management state in the same SQLite database."""
from __future__ import annotations
import collections
from contextlib import contextmanager, closing
import datetime as dt
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def folder_name(tag):
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', tag).rstrip(' .')
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', clean, re.I):
        clean = '_' + clean
    if not clean or len(clean) > 90 or clean != tag:
        clean = (clean[:80] or 'character') + '_' + hashlib.sha256(tag.encode()).hexdigest()[:8]
    return clean


class Conflict(Exception):
    pass


class Store:
    def __init__(self, config, dictionaries=True):
        self.config = config
        self.root = Path(config['root']).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / '_state').mkdir(exist_ok=True)
        for category in ('SFW', 'NSFW'): (self.root / category).mkdir(exist_ok=True)
        self.database = self.root / '_state' / 'archive.sqlite3'
        if not self.database.resolve().is_relative_to(self.root):
            raise ValueError('数据库路径超出图库')
        self.state = self.root / '_state' / 'manager'
        self.state.mkdir(parents=True, exist_ok=True)
        self.cache = self.state / 'thumbnails'
        self.cache.mkdir(exist_ok=True)
        if not self.database.exists():
            with closing(sqlite3.connect(self.database)) as initial:
                initial.execute('PRAGMA journal_mode=WAL')
                initial.executescript('''
                CREATE TABLE images(sha TEXT PRIMARY KEY,dhash TEXT,signature TEXT,width INT,height INT,files TEXT,metadata TEXT,created REAL);
                CREATE TABLE fingerprints(sha TEXT PRIMARY KEY,phash TEXT,dhash TEXT,width INT,height INT,thumbnail TEXT,files TEXT);
                CREATE TABLE library_files(path TEXT PRIMARY KEY,size INT,mtime_ns INT,sha TEXT);
                ''')
                initial.commit()
        self.lock = threading.RLock()
        self._snapshot = None
        self._stamp = None
        self._at = 0
        self.labels, self.aliases, self.kinds = {}, {}, {}
        if dictionaries:
            self.load_dictionaries()
        backup = self.state / 'backups' / (dt.datetime.now().strftime('%Y%m%d') + '-before-manager.sqlite3')
        if not backup.exists():
            backup.parent.mkdir(exist_ok=True)
            with self.connect() as source, closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS manager_flags(sha TEXT PRIMARY KEY, reviewed INTEGER DEFAULT 0,
              starred INTEGER DEFAULT 0, note TEXT DEFAULT '', updated REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS manager_trash(id TEXT PRIMARY KEY,sha TEXT,payload TEXT,deleted REAL);
            CREATE TABLE IF NOT EXISTS manager_audit(id TEXT PRIMARY KEY,sha TEXT,action TEXT,before_json TEXT,at REAL);
            CREATE TABLE IF NOT EXISTS manager_jobs(id TEXT PRIMARY KEY,status TEXT,phase TEXT,source TEXT,total INTEGER,
              processed INTEGER DEFAULT 0,saved INTEGER DEFAULT 0,rejected INTEGER DEFAULT 0,duplicate INTEGER DEFAULT 0,
              errors INTEGER DEFAULT 0,cancel INTEGER DEFAULT 0,created REAL,updated REAL,error TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS manager_job_items(job TEXT,position INTEGER,name TEXT,path TEXT,status TEXT,
              reason TEXT DEFAULT '',sha TEXT DEFAULT '',result TEXT DEFAULT '{}',PRIMARY KEY(job,position));
            CREATE INDEX IF NOT EXISTS manager_library_sha ON library_files(sha);
            CREATE INDEX IF NOT EXISTS manager_trash_sha ON manager_trash(sha);
            ''')
            db.commit()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=30000')
        try:
            yield db
        finally:
            db.close()

    def load_dictionaries(self):
        location = Path(self.config['dictionaryDir'])
        with gzip.open(location / 'tag-zh.json.gz', 'rt', encoding='utf-8') as file:
            self.labels = json.load(file)
        names_file = location / 'characters.json'
        if not names_file.exists(): names_file = location / 'characters.seed.json'
        characters = json.loads(names_file.read_text(encoding='utf-8'))['characters']
        chinese = lambda value: bool(re.search(r'[\u3400-\u9fff]', value))
        for tag, record in characters.items():
            names = [record.get('name', ''), *record.get('aliases', []), *record.get('searchAliases', [])]
            self.aliases[tag] = names
            name = next((name for name in names if chinese(name) and len(name) < 40), '')
            if name:
                self.labels[tag] = name
            self.kinds[tag] = 'character'
        model_config = Path(self.config['modelDir']) / 'config.json'
        if not model_config.exists(): model_config = location / 'model-tags.json'
        model = json.loads(model_config.read_text(encoding='utf-8'))
        offset = 0
        for kind, length in model['tags_split']:
            for tag in model['tags'][offset:offset+length]:
                self.kinds[tag] = kind
            offset += length
        self.labels.update({'rio_(blue_archive)': '调月莉音', 'rio_(armed)_(blue_archive)': '调月莉音（战斗服）',
            'himesaki_rinami': '姬崎莉波', 'aemeath_(wuthering_waves)': '爱弥斯（鸣潮）',
            'pantyhose': '连裤袜', 'small_breasts': '贫乳', 'solo': '单人', '1girl': '单个女孩'})
        self.aliases.setdefault('pantyhose', []).extend(['丝袜'])
        self.aliases.setdefault('thighhighs', []).extend(['丝袜', '长筒袜'])

    def label(self, tag):
        return self.labels.get(tag, tag.replace('_', ' '))

    def valid_sha(self, sha):
        if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
            raise ValueError('无效的图片编号')

    def safe_file(self, relative, trash=False, exists=True):
        value = Path(relative)
        allowed = ('SFW', 'NSFW', '_trash') if trash else ('SFW', 'NSFW')
        if value.is_absolute() or not value.parts or value.parts[0] not in allowed or '..' in value.parts:
            raise ValueError('图片路径不在图库内')
        target = self.root / value
        resolved = target.resolve(strict=exists)
        if not resolved.is_relative_to(self.root) or target.is_symlink():
            raise ValueError('不允许链接或图库外路径')
        return resolved

    def file_for(self, sha, trash_id=None):
        self.valid_sha(sha)
        with self.connect() as db:
            if trash_id:
                entry = db.execute('SELECT payload FROM manager_trash WHERE id=? AND sha=?', (trash_id, sha)).fetchone()
                paths = [pair[1] for pair in json.loads(entry[0])['moves']] if entry else []
            else:
                entry = db.execute('SELECT files FROM images WHERE sha=?', (sha,)).fetchone()
                paths = json.loads(entry[0]) if entry else []
        for relative in paths:
            try:
                file = self.safe_file(relative, trash=bool(trash_id))
                if file.is_file():
                    return file
            except (OSError, ValueError):
                pass
        raise FileNotFoundError('图片不存在或已移出图库')

    def invalidate(self):
        self._snapshot = None

    def snapshot(self):
        with self.lock:
            stamp = []
            for file in (self.database, Path(str(self.database)+'-wal')):
                try:
                    stat = file.stat(); stamp.append((stat.st_size, stat.st_mtime_ns))
                except OSError:
                    stamp.append(None)
            if self._snapshot is not None and self._stamp == stamp and time.monotonic()-self._at < 10:
                return self._snapshot
            with self.connect() as db:
                rows = db.execute('''SELECT i.sha,i.width,i.height,i.files,i.created,
                  json_extract(i.metadata,'$.category') AS category,
                  json_extract(i.metadata,'$.characters') AS characters,
                  json_extract(i.metadata,'$.tags') AS tags,
                  json_extract(i.metadata,'$.additionalTags.copyright') AS copyright,
                  COALESCE(f.reviewed,0) AS reviewed,COALESCE(f.starred,0) AS starred,
                  COALESCE(f.note,'') AS note,COALESCE(f.updated,0) AS updated,
                  COALESCE((SELECT MAX(size) FROM library_files WHERE sha=i.sha),0) AS bytes
                  FROM images i LEFT JOIN manager_flags f ON i.sha=f.sha''').fetchall()
            images, catalog = [], {}
            for value in rows:
                row = dict(value)
                for field in ('characters', 'tags', 'copyright'):
                    row[field] = json.loads(row[field] or '{}')
                    for tag in row[field]:
                        kind = {'characters':'character','tags':'general','copyright':'copyright'}[field]
                        entry = catalog.setdefault((kind, tag), {'tag':tag,'kind':kind,'label':self.label(tag),'count':0,
                            'cover':row['sha'],'rating':row['category']})
                        entry['count'] += 1
                        if row['category'] == 'SFW':
                            entry['cover'],entry['rating'] = row['sha'],'SFW'
                row['_all'] = set(row['characters']) | set(row['tags']) | set(row['copyright'])
                row['_search'] = ' '.join([row['sha'], row['note'], *[tag+' '+self.label(tag)+' '+' '.join(self.aliases.get(tag, []))
                    for tag in row['_all']]]).casefold()
                images.append(row)
            self._snapshot = (images, list(catalog.values()))
            self._stamp, self._at = stamp, time.monotonic()
            return self._snapshot

    def compact(self, row):
        return {**{key:row[key] for key in ('sha','width','height','created','category','bytes','reviewed','starred','updated')},
            'characters':[{'tag':tag,'label':self.label(tag)} for tag in row['characters']],
            'tagCount':len(row['tags']), 'title':self.label(next(iter(row['characters']))) if row['characters'] else '角色未识别'}

    def stats(self):
        rows, catalog = self.snapshot()
        today = dt.datetime.now().replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        with self.connect() as db:
            trash = db.execute('SELECT COUNT(*) FROM manager_trash').fetchone()[0]
        return {'total':len(rows),'sfw':sum(row['category']=='SFW' for row in rows),'nsfw':sum(row['category']=='NSFW' for row in rows),
            'unreviewed':sum(not row['reviewed'] for row in rows),'unknown':sum(not row['characters'] for row in rows),
            'starred':sum(bool(row['starred']) for row in rows),'today':sum(row['created']>=today for row in rows),
            'bytes':sum(row['bytes'] for row in rows),'trash':trash,
            'characters':sum(row['kind']=='character' for row in catalog),'tags':sum(row['kind']=='general' for row in catalog)}

    def list_images(self, params):
        rows, _ = self.snapshot()
        tags = params.get('tags', '').split(',') if params.get('tags') else []
        exclude = params.get('exclude', '').split(',') if params.get('exclude') else []
        query = params.get('q','').strip().casefold().split()
        view = params.get('view','all'); rating = params.get('rating','all')
        start = dt.datetime.now().replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        matched = [row for row in rows if (rating=='all' or row['category']==rating)
            and (view!='review' or not row['reviewed']) and (view!='starred' or row['starred'])
            and (view!='unknown' or not row['characters']) and (view!='today' or row['created']>=start)
            and all(tag in row['_all'] for tag in tags) and not any(tag in row['_all'] for tag in exclude)
            and all(word in row['_search'] for word in query)]
        sort = params.get('sort','newest')
        key = {'oldest':lambda r:r['created'],'largest':lambda r:r['bytes'],
            'resolution':lambda r:r['width']*r['height'],'newest':lambda r:r['created']}.get(sort,lambda r:r['created'])
        matched.sort(key=key,reverse=sort!='oldest')
        page = max(1,int(params.get('page',1))); limit = min(120,max(12,int(params.get('limit',60))))
        return {'total':len(matched),'page':page,'pages':max(1,math.ceil(len(matched)/limit)),
            'items':[self.compact(row) for row in matched[(page-1)*limit:page*limit]]}

    def tags(self, query='', kind='general', library=False, limit=60, offset=0):
        _, catalog = self.snapshot()
        available = {row['tag']:dict(row) for row in catalog if row['kind']==kind or kind=='any' and row['kind'] in ('general','character','copyright')}
        if not library:
            for tag, category in self.kinds.items():
                if (category == kind or kind=='any' and category in ('general','character','copyright')) and tag not in available:
                    available[tag]={'tag':tag,'label':self.label(tag),'kind':category,'count':0}
        words = query.strip().casefold().split()
        def rank(row):
            search = (row['tag']+' '+row['label']+' '+' '.join(self.aliases.get(row['tag'],[]))).casefold()
            if words and not all(word in search for word in words):
                return None
            exact = query.casefold() in (row['tag'].casefold(),row['label'].casefold()) if query else False
            return (not exact,-row['count'],row['tag'])
        results = [(rank(row),row) for row in available.values()]
        results = sorted((score,row) for score,row in results if score is not None)
        return {'total':len(results),'items':[row for _,row in results[offset:offset+limit]]}

    def detail(self, sha):
        self.valid_sha(sha)
        with self.connect() as db:
            row = db.execute('SELECT * FROM images WHERE sha=?',(sha,)).fetchone()
            if not row:
                raise FileNotFoundError('图片已被移出图库')
            flag = db.execute('SELECT * FROM manager_flags WHERE sha=?',(sha,)).fetchone()
        metadata = json.loads(row['metadata'])
        return {'sha':sha,'width':row['width'],'height':row['height'],'created':row['created'],
            'category':metadata.get('category','SFW'),'files':json.loads(row['files']),
            'tags':[{'tag':tag,'label':self.label(tag),'score':score} for tag,score in metadata.get('tags',{}).items()],
            'characters':[{'tag':tag,'label':self.label(tag),'score':score} for tag,score in metadata.get('characters',{}).items()],
            'copyright':[{'tag':tag,'label':self.label(tag),'score':score} for tag,score in metadata.get('additionalTags',{}).get('copyright',{}).items()],
            'reviewed':bool(flag['reviewed']) if flag else False,'starred':bool(flag['starred']) if flag else False,
            'note':flag['note'] if flag else '', 'version':hashlib.sha256(row['metadata'].encode()).hexdigest(),
            'model':metadata.get('model',''),'quality':metadata.get('quality',{}),'source':metadata.get('managerImport',metadata.get('localImport',metadata.get('source',{})))}

    def validate_tags(self, values):
        if not isinstance(values,list) or len(values)>250:
            raise ValueError('标签必须是列表，最多250个')
        result = []
        for tag in values:
            if not isinstance(tag,str) or not tag.strip() or len(tag)>150 or re.search(r'[\x00-\x1f\\/]',tag):
                raise ValueError('无效的标签')
            tag=tag.strip()
            if tag not in result:
                result.append(tag)
        return result

    def audit(self, db, sha, action, value):
        db.execute('INSERT INTO manager_audit VALUES(?,?,?,?,?)',(uuid.uuid4().hex,sha,action,dumps(value),time.time()))

    def set_flags(self, sha, values):
        self.valid_sha(sha)
        with self.lock, self.connect() as db:
            if not db.execute('SELECT 1 FROM images WHERE sha=?',(sha,)).fetchone():
                raise FileNotFoundError('图片不存在')
            previous = db.execute('SELECT * FROM manager_flags WHERE sha=?',(sha,)).fetchone()
            flag = dict(previous) if previous else {'reviewed':0,'starred':0,'note':''}
            for key in ('reviewed','starred'):
                if key in values:
                    flag[key] = int(bool(values[key]))
            if 'note' in values:
                flag['note'] = str(values['note'])[:2000]
            db.execute('INSERT OR REPLACE INTO manager_flags VALUES(?,?,?,?,?)',(sha,flag['reviewed'],flag['starred'],flag['note'],time.time()))
            db.commit()
            self.invalidate()
        return {'ok':True}

    def edit(self, sha, values):
        self.valid_sha(sha)
        moved = []
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT * FROM images WHERE sha=?',(sha,)).fetchone()
                if not row:
                    raise FileNotFoundError('图片不存在')
                if values.get('version') and values['version'] != hashlib.sha256(row['metadata'].encode()).hexdigest():
                    raise Conflict('图片已被其他窗口修改，请重新打开后再保存')
                before = dict(row); meta=json.loads(row['metadata'])
                for field in ('tags','characters'):
                    if field in values:
                        tags=self.validate_tags(values[field]); old=meta.get(field,{})
                        meta[field]={tag:old.get(tag,1.0) for tag in tags}
                category=values.get('category',meta.get('category','SFW'))
                if category not in ('SFW','NSFW'):
                    raise ValueError('分级必须为SFW或NSFW')
                meta['category']=category
                meta['characterStatus']='identified' if meta.get('characters') else 'unidentified'
                meta['trainingTags']=[*meta.get('characters',{}),*meta.get('tags',{})]
                meta['managerEdit']={'at':time.time(),'manual':True}
                old_files=json.loads(row['files']); new_files=[]
                primary=next(iter(meta.get('characters',{})),'_unknown_character')
                for i,relative in enumerate(old_files):
                    original=self.safe_file(relative)
                    target_relative=str(Path(category)/folder_name(primary)/original.name)
                    target=self.safe_file(target_relative,exists=False)
                    if original!=target:
                        target.parent.mkdir(parents=True,exist_ok=True)
                        if target.exists():
                            raise Conflict('目标位置已有文件，请先检查图库')
                        os.replace(original,target); moved.append((original,target))
                    new_files.append(target_relative)
                db.execute('UPDATE images SET files=?,metadata=?,signature=? WHERE sha=?',(dumps(new_files),dumps(meta),category+':'+'|'.join(meta.get('characters',{})),sha))
                db.execute('UPDATE fingerprints SET files=? WHERE sha=?',(dumps(new_files),sha))
                db.execute('DELETE FROM library_files WHERE sha=?',(sha,))
                for relative in new_files:
                    stat=self.safe_file(relative).stat()
                    db.execute('INSERT OR REPLACE INTO library_files VALUES(?,?,?,?)',(relative,stat.st_size,stat.st_mtime_ns,sha))
                self.audit(db,sha,'edit',before)
                db.commit()
            except BaseException:
                db.rollback()
                for original,target in reversed(moved):
                    if target.exists() and not original.exists():
                        original.parent.mkdir(parents=True,exist_ok=True);os.replace(target,original)
                raise
            self.invalidate()
        self.set_flags(sha,{'reviewed':True})
        return self.detail(sha)

    def delete(self, sha):
        self.valid_sha(sha); moved=[]; identifier=uuid.uuid4().hex
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute('SELECT * FROM images WHERE sha=?',(sha,)).fetchone()
                if not row:
                    raise FileNotFoundError('图片已不在图库中')
                fp=db.execute('SELECT * FROM fingerprints WHERE sha=?',(sha,)).fetchone()
                flag=db.execute('SELECT * FROM manager_flags WHERE sha=?',(sha,)).fetchone()
                indexed=[dict(v) for v in db.execute('SELECT * FROM library_files WHERE sha=?',(sha,))]
                moves=[]
                for relative in json.loads(row['files']):
                    try: original=self.safe_file(relative)
                    except FileNotFoundError: continue
                    target_relative=str(Path('_trash')/identifier/relative)
                    target=self.safe_file(target_relative,trash=True,exists=False)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    os.replace(original,target);moved.append((original,target));moves.append([relative,target_relative])
                payload={'image':dict(row),'fingerprint':dict(fp) if fp else None,'flag':dict(flag) if flag else None,'indexed':indexed,'moves':moves}
                db.execute('INSERT INTO manager_trash VALUES(?,?,?,?)',(identifier,sha,dumps(payload),time.time()))
                for table in ('images','fingerprints','library_files','manager_flags'):
                    db.execute('DELETE FROM '+table+' WHERE sha=?',(sha,))
                self.audit(db,sha,'trash',{'trashId':identifier})
                db.commit()
            except BaseException:
                db.rollback()
                for original,target in reversed(moved):
                    if target.exists() and not original.exists():
                        original.parent.mkdir(parents=True,exist_ok=True);os.replace(target,original)
                raise
            self.invalidate()
        return {'ok':True,'trashId':identifier}

    def trash_list(self):
        with self.connect() as db:
            entries=db.execute('SELECT * FROM manager_trash ORDER BY deleted DESC').fetchall()
        result=[]
        for entry in entries:
            payload=json.loads(entry['payload']); row=payload['image']; meta=json.loads(row['metadata'])
            result.append({'sha':entry['sha'],'trashId':entry['id'],'deleted':entry['deleted'],
                'width':row['width'],'height':row['height'],'category':meta.get('category','SFW'),
                'title':self.label(next(iter(meta.get('characters',{})))) if meta.get('characters') else '角色未识别'})
        return {'total':len(result),'items':result}

    def restore(self, identifier):
        moved=[]
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute('SELECT * FROM manager_trash WHERE id=?',(identifier,)).fetchone()
                if not row: raise FileNotFoundError('回收站记录不存在')
                payload=json.loads(row['payload']);sha=row['sha']
                if db.execute('SELECT 1 FROM images WHERE sha=?',(sha,)).fetchone():
                    raise Conflict('图库中已重新收录同一张图片，暂不能恢复旧版本')
                for relative,trashed in payload['moves']:
                    original=self.safe_file(trashed,trash=True);target=self.safe_file(relative,exists=False)
                    if target.exists(): raise Conflict('原位置已有文件，无法覆盖恢复')
                    target.parent.mkdir(parents=True,exist_ok=True);os.replace(original,target);moved.append((original,target))
                for table,item in (('images',payload['image']),('fingerprints',payload['fingerprint']),('manager_flags',payload['flag'])):
                    if item:
                        keys=list(item);db.execute('INSERT INTO '+table+'('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',list(item.values()))
                for item in payload['indexed']:
                    file=self.safe_file(item['path']);stat=file.stat()
                    db.execute('INSERT OR REPLACE INTO library_files VALUES(?,?,?,?)',(item['path'],stat.st_size,stat.st_mtime_ns,sha))
                db.execute('DELETE FROM manager_trash WHERE id=?',(identifier,));db.commit()
            except BaseException:
                db.rollback()
                for original,target in reversed(moved):
                    if target.exists() and not original.exists():
                        original.parent.mkdir(parents=True,exist_ok=True);os.replace(target,original)
                raise
            self.invalidate()
        return {'ok':True,'sha':sha}
