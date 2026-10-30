import base64
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).parents[1] / 'engine'))
from test_similarity import artwork

SPEC = importlib.util.spec_from_file_location('worker', Path(__file__).parents[1] / 'engine' / 'worker.py')
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


class FakeTagger:
    def __init__(self, rating=None, general=None, characters=None):
        self.rating = rating or {'general': .97, 'sensitive': .02, 'questionable': .009, 'explicit': .001}
        self.general = general or {'1girl': .98, 'blue_hair': .8}
        self.characters = {'test_character': .95} if characters is None else characters
        self.calls = 0

    def predict(self, image):
        self.calls += 1
        return {'rating': self.rating, 'general': self.general, 'characters': self.characters}


def image_bytes(seed=1, size=(800, 1024)):
    output = io.BytesIO()
    artwork(seed).resize(size).save(output, format='PNG')
    return output.getvalue()


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = image_bytes()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archives = []

    def tearDown(self):
        for archive in self.archives:
            archive.close()
        self.tmp.cleanup()

    def archive(self, tagger=None):
        archive = w.Archive(Path(self.tmp.name) / str(len(self.archives)), tagger or FakeTagger())
        self.archives.append(archive)
        return archive

    def request(self, data=None, **kwargs):
        return {'id': 'test', 'source': {'base64': base64.b64encode(self.data if data is None else data).decode()}, 'meta': {'groupId': 'test', 'messageId': '1'}, **kwargs}

    def test_sfw_saves_original_and_metadata_in_database_only(self):
        arc = self.archive()
        result = arc.process(self.request())
        self.assertEqual(result['status'], 'saved')
        self.assertEqual(result['category'], 'SFW')
        image_path = arc.root / result['files'][0]
        self.assertEqual(image_path.read_bytes(), self.data)
        self.assertFalse(image_path.with_suffix('.txt').exists())
        self.assertFalse(image_path.with_suffix('.json').exists())
        metadata = json.loads(arc.db.execute('SELECT metadata FROM images WHERE sha=?', (result['sha256'],)).fetchone()[0])
        self.assertIn('test_character', metadata['trainingTags'])
        self.assertEqual(metadata['modelRevision'], w.MODEL_REVISION)

    def test_sensitive_rating_alone_does_not_make_nsfw(self):
        tagger = FakeTagger(rating={'general': .05, 'sensitive': .93, 'questionable': .019, 'explicit': .001})
        result = self.archive(tagger).process(self.request())
        self.assertEqual(result['category'], 'SFW')

    def test_uncertain_and_unknown_and_meme_never_save_images(self):
        cases = [
            (FakeTagger(rating={name: .25 for name in w.RATINGS}), 'uncertain_rating'),
            (FakeTagger(characters={}, general={'no_humans': .95, 'scenery': .9}), 'no_reliable_person'),
            (FakeTagger(general={'meme': .9}), 'filtered_tag:meme'),
        ]
        for tagger, reason in cases:
            arc = self.archive(tagger)
            result = arc.process(self.request())
            self.assertEqual(result['status'], 'rejected')
            self.assertEqual(result['reason'], reason)
            self.assertFalse(list(arc.root.rglob('*.png')))
            self.assertEqual(len(list((arc.root / '_logs').glob('*.jsonl'))), 1)

    def test_low_resolution_flat_and_invalid(self):
        small = image_bytes(size=(64, 64))
        flat = io.BytesIO()
        Image.new('RGB', (1024, 1024), 'white').save(flat, format='PNG')
        for data, reason in [(small, 'low_resolution'), (flat.getvalue(), 'flat_or_strongly_blurred_image'), (b'not an image', 'invalid_or_unsafe_image')]:
            arc = self.archive()
            result = arc.process(self.request(data))
            self.assertEqual(result['reason'], reason)
            self.assertEqual(arc.tagger.calls, 0)
            self.assertFalse(list((arc.root / 'SFW').rglob('*.*')))

    def test_exact_duplicate_survives_reopen(self):
        arc = self.archive()
        first = arc.process(self.request())
        second = arc.process(self.request())
        self.assertEqual(second['reason'], 'exact_duplicate')
        self.assertEqual(arc.tagger.calls, 1)
        root = arc.root
        arc.close()
        self.archives.remove(arc)
        arc2 = w.Archive(root, FakeTagger())
        self.archives.append(arc2)
        self.assertEqual(arc2.process(self.request())['reason'], 'exact_duplicate')
        # A manually removed file may be restored by a later received copy.
        (root / first['files'][0]).unlink()
        self.assertEqual(arc2.process(self.request())['status'], 'saved')

    def test_multicharacter_saves_only_top_role(self):
        arc = self.archive(FakeTagger(characters={'alice': .98, 'bob': .94}))
        result = arc.process(self.request())
        self.assertEqual(len(result['files']), 1)
        self.assertEqual((arc.root / result['files'][0]).parent.name, 'alice')
        self.assertFalse((arc.root / 'SFW' / 'bob').exists())
        self.assertTrue((arc.root / result['files'][0]).is_file())

    def test_near_duplicate_including_better_resolution(self):
        arc = self.archive()
        self.assertEqual(arc.process(self.request())['status'], 'saved')
        same = io.BytesIO()
        Image.open(io.BytesIO(self.data)).save(same, format='PNG', compress_level=0)
        self.assertEqual(arc.process(self.request(same.getvalue()))['reason'], 'near_duplicate')
        larger = io.BytesIO()
        Image.open(io.BytesIO(self.data)).resize((1200, 1536), Image.Resampling.LANCZOS).save(larger, format='PNG')
        self.assertEqual(arc.process(self.request(larger.getvalue()))['reason'], 'near_duplicate')

    def test_unknown_person_saved_without_invented_character_tags(self):
        arc = self.archive(FakeTagger(characters={'uncertain_character': .2}))
        result = arc.process(self.request())
        self.assertEqual(result['reason'], 'accepted_unknown_character_candidate')
        self.assertEqual(result['characters'], [])
        self.assertEqual(result['characterStatus'], 'unidentified')
        saved = arc.root / result['files'][0]
        self.assertEqual(saved.parent.name, '_unknown_character')
        tags = json.loads(arc.db.execute('SELECT metadata FROM images WHERE sha=?', (result['sha256'],)).fetchone()[0])['trainingTags']
        self.assertIn('1girl', tags)
        self.assertNotIn('_unknown_character', tags)
        self.assertNotIn('uncertain_character', tags)
        self.assertFalse(saved.with_suffix('.txt').exists())

    def test_unknown_person_still_checks_quality_and_optional_switch(self):
        for tagger, config, reason in [
            (FakeTagger(characters={}), {'saveUnknownCharacters': False}, 'unknown_or_uncertain_character'),
            (FakeTagger(characters={}, general={'1girl': .65}), {}, 'no_reliable_person'),
            (FakeTagger(characters={}, general={'1girl': .95, 'no_humans': .9}), {}, 'no_reliable_person'),
            (FakeTagger(characters={}, general={'1girl': .95, 'meme': .9}), {}, 'filtered_tag:meme'),
        ]:
            arc = self.archive(tagger)
            result = arc.process(self.request(settings=config))
            self.assertEqual(result['status'], 'rejected')
            self.assertEqual(result['reason'], reason)
            self.assertFalse(list((arc.root / 'SFW').rglob('*.png')))

    def test_unknown_person_nsfw_remains_separate(self):
        arc = self.archive(FakeTagger(characters={}, general={'1girl': .99, 'nipples': .9}, rating={'general': .03, 'sensitive': .93, 'questionable': .03, 'explicit': .01}))
        result = arc.process(self.request())
        self.assertEqual(result['category'], 'NSFW')
        self.assertEqual(Path(result['files'][0]).parts[:2], ('NSFW', '_unknown_character'))

    def test_similar_images_across_character_and_rating_buckets(self):
        arc = self.archive()
        first = arc.process(self.request())
        arc.tagger = FakeTagger(characters={}, rating={'general': .01, 'sensitive': .95, 'questionable': .03, 'explicit': .01})
        encoded = io.BytesIO()
        Image.open(io.BytesIO(self.data)).save(encoded, format='JPEG', quality=80)
        result = arc.process(self.request(encoded.getvalue()))
        self.assertEqual(result['reason'], 'near_duplicate')
        self.assertEqual(result['duplicateOf'], first['sha256'])
        self.assertEqual(arc.tagger.calls, 0)

    def test_database_index_failure_rolls_back_new_image(self):
        arc = self.archive()
        with patch.object(arc, 'index_image', side_effect=OSError('simulated_database_failure')):
            result = arc.process(self.request())
        self.assertEqual(result['status'], 'error')
        self.assertFalse(list((arc.root / 'SFW').rglob('*.png')))
        self.assertEqual(arc.db.execute('SELECT count(*) FROM images').fetchone()[0], 0)

    def test_no_untrusted_local_paths_or_arbitrary_hosts(self):
        for url in ['http://127.0.0.1/image', 'https://qpic.cn.attacker.example/x', 'file:///x', 'https://x:q@qpic.cn/x', 'https://qpic.cn:999/x']:
            with self.assertRaises((w.Rejected, ValueError)):
                w.validate_url(url)
        for source in [{'path': __file__}, {'path': '//server/x', 'trusted': True}]:
            with self.assertRaises(w.Rejected):
                w.read_source(source, w.settings())
        self.assertEqual(w.validate_url('https://multimedia.nt.qq.com/x'), 'https://multimedia.nt.qq.com/x')
        self.assertEqual(w.validate_url('https://multimedia.nt.qq.com.cn/download?appid=1407'), 'https://multimedia.nt.qq.com.cn/download?appid=1407')
        with self.assertRaises(w.Rejected):
            w.validate_url('https://multimedia.nt.qq.com.cn.attacker.example/x')

    def test_rating_scores_not_added_as_probability(self):
        prediction = FakeTagger(rating={'general': .9, 'sensitive': .09, 'questionable': .08, 'explicit': .07}).predict(None)
        self.assertEqual(w.classify(prediction, w.settings())[0], 'SFW')

    def test_redirect_revalidates_destination(self):
        request = w.urllib.request.Request('https://gchat.qpic.cn/image')
        with self.assertRaises(w.Rejected):
            w.SafeRedirect().redirect_request(request, None, 302, '', {}, 'http://127.0.0.1/private')

    def test_preprocess_white_alpha_bgr_scale(self):
        rgb = Image.new('RGB', (2, 2), (10, 20, 30))
        tensor = w.preprocess(rgb, 2)
        self.assertEqual(tensor.shape, (1, 2, 2, 3))
        np.testing.assert_equal(tensor[0, 0, 0], [30, 20, 10])
        self.assertEqual(tensor.dtype, np.float32)

    def test_record_never_reads_or_saves_source(self):
        arc = self.archive()
        result = arc.process({'id': 'r', 'command': 'record', 'status': 'rejected', 'reason': 'queue_full'})
        self.assertEqual(result['reason'], 'queue_full')
        self.assertFalse(list(arc.root.rglob('*.png')))

    def test_windows_character_path_safety(self):
        for tag in ['../bad', 'CON', 'x/y', 'x:y', '..', 'a' * 300]:
            folder = w.folder_name(tag)
            self.assertNotIn('/', folder)
            self.assertNotIn('\\', folder)
            self.assertLess(len(folder), 100)
            self.assertNotEqual(folder.upper(), 'CON')
        self.assertEqual(w.folder_name('hatsune_miku'), 'hatsune_miku')


if __name__ == '__main__':
    unittest.main(verbosity=2)
