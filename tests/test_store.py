import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'web'))
from store import Store,Conflict


def make_library(root):
    (root/'_state').mkdir(parents=True);(root/'SFW'/'rio_(blue_archive)').mkdir(parents=True)
    with sqlite3.connect(root/'_state'/'archive.sqlite3') as db:
        db.executescript('''CREATE TABLE images(sha TEXT PRIMARY KEY,dhash TEXT,signature TEXT,width INT,height INT,files TEXT,metadata TEXT,created REAL);
        CREATE TABLE fingerprints(sha TEXT PRIMARY KEY,phash TEXT,dhash TEXT,width INT,height INT,thumbnail TEXT,files TEXT);
        CREATE TABLE library_files(path TEXT PRIMARY KEY,size INT,mtime_ns INT,sha TEXT);''')
        file=root/'SFW'/'rio_(blue_archive)'/'temporary.png';Image.new('RGB',(80,96),(130,160,210)).save(file)
        sha=hashlib.sha256(file.read_bytes()).hexdigest();target=file.with_name(sha+'.png');file.rename(target);relative=str(target.relative_to(root));stat=target.stat()
        meta={'category':'SFW','characters':{'rio_(blue_archive)':.95},'tags':{'solo':.9,'pantyhose':.8},'additionalTags':{},'trainingTags':['rio_(blue_archive)','solo','pantyhose']}
        db.execute('INSERT INTO images VALUES(?,?,?,?,?,?,?,?)',(sha,'0','SFW:rio_(blue_archive)',80,96,json.dumps([relative]),json.dumps(meta),time.time()))
        db.execute('INSERT INTO fingerprints VALUES(?,?,?,?,?,?,?)',(sha,'0','0',80,96,'',json.dumps([relative])))
        db.execute('INSERT INTO library_files VALUES(?,?,?,?)',(relative,stat.st_size,stat.st_mtime_ns,sha));db.commit()
    db.close()
    return sha,target


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='danbooru-manager-test-');self.root=Path(self.temp.name)
        self.sha,self.file=make_library(self.root);self.store=Store({'root':str(self.root)},dictionaries=False)
        self.store.labels={'rio_(blue_archive)':'调月莉音','pantyhose':'连裤袜','solo':'单人'}

    def tearDown(self):self.temp.cleanup()

    def test_fresh_library_bootstrap(self):
        fresh=self.root/'fresh-library'
        store=Store({'root':str(fresh)},dictionaries=False)
        self.assertEqual(store.stats()['total'],0)
        self.assertTrue((fresh/'SFW').is_dir())
        self.assertTrue((fresh/'NSFW').is_dir())
        with store.connect() as db:
            for table in ('images','fingerprints','library_files','manager_flags'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_browse_and_filter(self):
        self.assertEqual(self.store.list_images({'tags':'pantyhose,rio_(blue_archive)'})['total'],1)
        self.assertEqual(self.store.list_images({'q':'莉音'})['total'],1)
        self.assertEqual(self.store.list_images({'tags':'black_hair'})['total'],0)
        self.assertEqual(self.store.tags('裤袜','general',True)['items'][0]['tag'],'pantyhose')

    def test_edit_moves_file_and_all_indexes_preserving_bytes(self):
        original=self.file.read_bytes();version=self.store.detail(self.sha)['version']
        result=self.store.edit(self.sha,{'version':version,'characters':['himesaki_rinami'],'tags':['solo','white_hair'],'category':'NSFW'})
        destination=self.root/'NSFW'/'himesaki_rinami'/self.file.name
        self.assertFalse(self.file.exists());self.assertEqual(destination.read_bytes(),original)
        self.assertEqual(result['tags'][1]['tag'],'white_hair');self.assertTrue(result['reviewed'])
        with self.store.connect() as db:
            for table in ('images','fingerprints'):
                self.assertEqual(json.loads(db.execute('SELECT files FROM '+table+' WHERE sha=?',(self.sha,)).fetchone()[0]),[str(destination.relative_to(self.root))])
            self.assertEqual(db.execute('SELECT path FROM library_files WHERE sha=?',(self.sha,)).fetchone()[0],str(destination.relative_to(self.root)))
        with self.assertRaises(Conflict):self.store.edit(self.sha,{'version':version,'tags':[]})

    def test_trash_and_restore_are_visible_to_existing_gallery(self):
        before=self.file.read_bytes();self.store.set_flags(self.sha,{'starred':True,'note':'测试'})
        result=self.store.delete(self.sha);self.assertFalse(self.file.exists());self.assertEqual(self.store.stats()['total'],0)
        with self.store.connect() as db:
            for table in ('images','fingerprints','library_files'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table+' WHERE sha=?',(self.sha,)).fetchone()[0],0)
        self.assertEqual(self.store.file_for(self.sha,result['trashId']).read_bytes(),before)
        self.store.restore(result['trashId']);self.assertEqual(self.file.read_bytes(),before)
        self.assertTrue(self.store.detail(self.sha)['starred']);self.assertEqual(self.store.stats()['trash'],0)

    def test_conflicting_move_rolls_back(self):
        destination=self.root/'NSFW'/'rio_(blue_archive)'/self.file.name;destination.parent.mkdir(parents=True);destination.write_bytes(b'preexisting')
        with self.assertRaises(Conflict):self.store.edit(self.sha,{'category':'NSFW'})
        self.assertTrue(self.file.exists());self.assertEqual(self.store.detail(self.sha)['category'],'SFW');self.assertEqual(destination.read_bytes(),b'preexisting')

    def test_paths_and_tags_cannot_escape_library(self):
        for relative in ('../config.json','SFW/../../config.json','_state/archive.sqlite3'):
            with self.assertRaises(ValueError):self.store.safe_file(relative)
        with self.assertRaises(ValueError):self.store.edit(self.sha,{'characters':['../../outside']})

    def test_flags_notes_and_review_filters(self):
        self.assertEqual(self.store.list_images({'view':'review'})['total'],1)
        self.store.set_flags(self.sha,{'reviewed':True,'starred':True,'note':'喜欢这张'})
        self.assertEqual(self.store.list_images({'view':'review'})['total'],0)
        self.assertEqual(self.store.list_images({'view':'starred','q':'喜欢'})['total'],1)


if __name__=='__main__':unittest.main()
