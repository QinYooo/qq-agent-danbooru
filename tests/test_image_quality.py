import io
import sys
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / 'engine'))
import image_quality as q
import test_worker as fixtures
FakeTagger = fixtures.FakeTagger


def scores(label='illustration', confidence=.94):
    return {name: confidence if name == label else (1 - confidence) / 4 for name in q.LABELS}


class FakeType:
    def __init__(self, label='illustration', confidence=.94):
        self.label, self.confidence = label, confidence
    def predict(self, image):
        return scores(self.label, self.confidence)


class ImageQualityTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.WorkerTests.setUpClass.__func__)
    setUp = fixtures.WorkerTests.setUp
    tearDown = fixtures.WorkerTests.tearDown
    archive = fixtures.WorkerTests.archive
    request = fixtures.WorkerTests.request
    def test_type_gate_prevents_person_in_ui_from_reaching_tagger(self):
        for label in ('not_painting', 'bangumi', 'comic', '3d'):
            arc = self.archive()
            arc.image_type = FakeType(label)
            result = arc.process(self.request())
            self.assertEqual(result['reason'], 'unsuitable_image_type:' + label)
            self.assertEqual(arc.tagger.calls, 0)
            self.assertFalse(list((arc.root / 'SFW').rglob('*.png')))

    def test_unknown_person_can_still_pass_clean_illustration_gate(self):
        arc = self.archive(FakeTagger(characters={}))
        arc.image_type = FakeType()
        result = arc.process(self.request())
        self.assertEqual(result['status'], 'saved')
        self.assertEqual(result['characterStatus'], 'unidentified')
        self.assertTrue(result['typeAssessment']['accepted'])

    def test_weak_illustration_rejected_and_3d_requires_option(self):
        arc = self.archive()
        arc.image_type = FakeType('illustration', .55)
        self.assertEqual(arc.process(self.request())['reason'], 'uncertain_artwork_type')
        arc.image_type = FakeType('3d')
        result = arc.process(self.request(settings={'allow3d': True}))
        self.assertEqual(result['status'], 'saved')

    def test_screenshot_meta_blocks_even_known_character_or_explicit_tags(self):
        for tag in ('game_screenshot', 'anime_screenshot', 'screenshot'):
            tagger = FakeTagger(general={'1girl': .99, 'pussy': .98})
            original = tagger.predict
            def predict(image, tag=tag):
                prediction = original(image)
                prediction['meta'] = {tag: .84}
                return prediction
            tagger.predict = predict
            arc = self.archive(tagger)
            arc.image_type = FakeType()  # Second independent model can disagree.
            result = arc.process(self.request())
            self.assertEqual(result['reason'], 'screenshot_tag:' + tag)
            self.assertFalse(list((arc.root / 'NSFW').rglob('*.png')))

    def test_explicit_tag_adds_nsfw_signal_but_clothing_does_not(self):
        for general, group in [({'1girl': .99, 'pussy': .9}, 'NSFW'), ({'1girl': .99, 'pantyhose': .95}, 'SFW'), ({'1girl': .99, 'glasses': .98}, 'SFW')]:
            arc = self.archive(FakeTagger(general=general))
            arc.image_type = FakeType()
            self.assertEqual(arc.process(self.request())['category'], group)


class InputAndDecisionTests(unittest.TestCase):
    def test_full_frame_is_preserved_and_normalized(self):
        image = Image.new('RGB', (1024, 2048), 'white')
        image.paste((0, 0, 0), (0, 0, 1024, 256))
        tensor = q.encode_image(image)
        self.assertEqual(tensor.shape, (1, 3, 384, 384))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertLess(tensor[0, 0, 0].mean(), -.99)
        self.assertGreater(tensor[0, 0, -1].mean(), .99)

    def test_invalid_type_output_is_never_accepted(self):
        for prediction in ({}, {**scores(), 'illustration': float('nan')}, {**scores(), 'illustration': 5}):
            with self.assertRaises(RuntimeError):
                q.assess_scores(prediction)


if __name__ == '__main__':
    unittest.main(verbosity=2)
