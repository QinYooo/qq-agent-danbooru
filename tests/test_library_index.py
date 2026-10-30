"""Archive integration tests using synthetic artwork and a deterministic tagger."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / 'engine'))
SPEC = importlib.util.spec_from_file_location('library_test_worker', Path(__file__).parents[1] / 'engine' / 'worker.py')
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)
from test_similarity import artwork


class FakeTagger:
    def __init__(self, characters=None):
        self.characters = {'test_character': .97} if characters is None else characters
        self.calls = 0

    def predict(self, image):
        self.calls += 1
        return {
            'general': {'1girl': .98, 'blue_hair': .8},
            'characters': self.characters,
            'rating': {'general': .97, 'sensitive': .02, 'questionable': .009, 'explicit': .001},
        }


def encoded(image, format='PNG'):
    stream = io.BytesIO()
    image.save(stream, format=format, **({'quality': 85} if format == 'JPEG' else {}))
    return stream.getvalue()


class LibraryIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = artwork(21)
        cls.b = artwork(72)
        cls.bytes_a = encoded(cls.a)
        cls.bytes_b = encoded(cls.b)
        cls.jpeg_a = encoded(cls.a, 'JPEG')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='danbooru-index-test-')
        self.root = Path(self.tmp.name)
        self.arc = None

    def tearDown(self):
        if self.arc:
            self.arc.close()
        self.tmp.cleanup()

    def archive(self, characters=None):
        self.arc = w.Archive(self.root, FakeTagger(characters))
        return self.arc

    def request(self, data=None):
        return {
            'id': 'synthetic-library-check',
            'source': {'base64': base64.b64encode(self.bytes_a if data is None else data).decode()},
            'meta': {'groupId': 'test', 'messageId': '1'},
        }

    def test_legacy_database_gets_fingerprints_without_rewriting_images(self):
        legacy = self.root / 'SFW' / 'legacy_character' / 'old.png'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(self.bytes_a)
        state = self.root / '_state'
        state.mkdir()
        db = sqlite3.connect(state / 'archive.sqlite3')
        db.execute('CREATE TABLE images (sha TEXT PRIMARY KEY, dhash TEXT, signature TEXT, width INT, height INT, files TEXT, metadata TEXT, created REAL)')
        db.execute('INSERT INTO images VALUES (?,?,?,?,?,?,?,?)', (
            hashlib.sha256(self.bytes_a).hexdigest(), '0' * 16, 'SFW:legacy_character', 768, 1024,
            json.dumps([str(legacy.relative_to(self.root))]), '{}', 1,
        ))
        db.commit()
        db.close()
        before = legacy.stat().st_mtime_ns
        arc = self.archive()
        result = arc.process(self.request(self.jpeg_a))
        self.assertEqual(result['reason'], 'near_duplicate')
        self.assertEqual(arc.tagger.calls, 0)
        self.assertEqual(legacy.read_bytes(), self.bytes_a)
        self.assertEqual(legacy.stat().st_mtime_ns, before)
        self.assertEqual(arc.db.execute('SELECT count(*) FROM images').fetchone()[0], 1)
        self.assertEqual(arc.db.execute('SELECT count(*) FROM fingerprints').fetchone()[0], 1)

    def test_unknown_bucket_participates_in_global_duplicate_checks(self):
        arc = self.archive({})
        first = arc.process(self.request())
        self.assertEqual(first['status'], 'saved')
        self.assertEqual(first['characterStatus'], 'unidentified')
        first_path = self.root / first['files'][0]
        self.assertEqual(first_path.parent.name, '_unknown_character')
        metadata = json.loads(arc.db.execute('SELECT metadata FROM images WHERE sha=?', (first['sha256'],)).fetchone()[0])
        self.assertNotIn('_unknown_character', metadata['trainingTags'])
        self.assertFalse(first_path.with_suffix('.txt').exists())
        arc.tagger.characters = {'newly_identified_character': .99}
        second = arc.process(self.request(self.jpeg_a))
        self.assertEqual(second['reason'], 'near_duplicate')
        self.assertEqual(arc.tagger.calls, 1)
        self.assertEqual(second['duplicateFile'], first['files'][0])
        self.assertEqual(first_path.read_bytes(), self.bytes_a)

    def test_other_category_and_character_paths_are_included(self):
        external = self.root / 'NSFW' / 'different_character' / 'manual.png'
        external.parent.mkdir(parents=True)
        external.write_bytes(self.bytes_a)
        arc = self.archive({'new_character': .99})
        result = arc.process(self.request(self.jpeg_a))
        self.assertEqual(result['reason'], 'near_duplicate')
        self.assertEqual(result['duplicateFile'], str(external.relative_to(self.root)))
        self.assertEqual(external.read_bytes(), self.bytes_a)

    def test_manually_added_picture_is_discovered_on_refresh(self):
        arc = self.archive()
        manual = self.root / 'SFW' / 'manual.png'
        manual.write_bytes(self.bytes_a)
        arc.last_scan = 0
        result = arc.process(self.request(self.jpeg_a))
        self.assertEqual(result['reason'], 'near_duplicate')
        self.assertEqual(manual.read_bytes(), self.bytes_a)

    def test_deleted_files_cannot_cause_duplicate_rejection(self):
        arc = self.archive()
        first = arc.process(self.request())
        (self.root / first['files'][0]).unlink()
        arc.sync_library()
        self.assertEqual(arc.db.execute('SELECT count(*) FROM fingerprints').fetchone()[0], 0)
        second = arc.process(self.request())
        self.assertEqual(second['status'], 'saved')
        self.assertEqual((self.root / second['files'][0]).read_bytes(), self.bytes_a)

    def test_modified_candidate_is_not_matched_using_stale_fingerprint(self):
        arc = self.archive()
        first = arc.process(self.request())
        altered_path = self.root / first['files'][0]
        altered_path.write_bytes(self.bytes_b)
        # No full-library refresh has run since an in-place edit.
        second = arc.process(self.request(self.jpeg_a))
        self.assertEqual(second['status'], 'saved')
        self.assertEqual(altered_path.read_bytes(), self.bytes_b)
        self.assertEqual((self.root / second['files'][0]).read_bytes(), self.jpeg_a)

    def test_changed_hash_named_path_is_not_reused_for_wrong_content(self):
        arc = self.archive()
        first = arc.process(self.request())
        altered_path = self.root / first['files'][0]
        altered_path.write_bytes(self.bytes_b)
        arc.sync_library()
        second = arc.process(self.request())
        self.assertEqual(second['status'], 'saved')
        self.assertEqual(altered_path.read_bytes(), self.bytes_b, 'existing file must remain untouched')
        self.assertNotEqual(second['files'][0], first['files'][0])
        self.assertEqual((self.root / second['files'][0]).read_bytes(), self.bytes_a)


if __name__ == '__main__':
    unittest.main(verbosity=2)
