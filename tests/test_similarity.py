import base64
import importlib.util
import io
from pathlib import Path
import unittest

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

SPEC = importlib.util.spec_from_file_location('similarity', Path(__file__).parents[1] / 'engine' / 'similarity.py')
s = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s)


def artwork(seed=1):
    image = Image.new('RGB', (768, 1024), (228, 220, 204))
    draw = ImageDraw.Draw(image)
    rng = np.random.default_rng(seed)
    for _ in range(28):
        x, y = rng.integers(0, 650), rng.integers(0, 900)
        w, h = rng.integers(30, 200), rng.integers(30, 200)
        color = tuple(int(c) for c in rng.integers(30, 230, 3))
        draw.ellipse((x, y, x + w, y + h), fill=color, outline=(30, 40, 50), width=6)
    draw.rectangle((160, 550, 500, 850), outline=(90, 40, 120), width=18)
    return image


class SimilarityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = artwork()
        cls.fp = s.fingerprint(cls.image)

    def test_fingerprint_contract(self):
        self.assertEqual(set(self.fp), {'phash', 'dhash', 'thumbnail', 'width', 'height'})
        self.assertRegex(self.fp['phash'], r'^[0-9a-f]{16}$')
        self.assertRegex(self.fp['dhash'], r'^[0-9a-f]{16}$')
        self.assertEqual(len(base64.b64decode(self.fp['thumbnail'])), 32 * 32 * 3)

    def test_jpeg_recompression(self):
        output = io.BytesIO()
        self.image.save(output, format='JPEG', quality=65)
        compressed = Image.open(io.BytesIO(output.getvalue())).convert('RGB')
        self.assertTrue(s.is_similar(self.fp, s.fingerprint(compressed)))

    def test_smaller_and_larger_versions(self):
        for size in [(384, 512), (1536, 2048)]:
            resized = self.image.resize(size, Image.Resampling.LANCZOS)
            self.assertTrue(s.is_similar(self.fp, s.fingerprint(resized)), size)

    def test_small_brightness_and_color_change(self):
        adjusted = ImageEnhance.Brightness(self.image).enhance(1.025)
        adjusted = ImageEnhance.Color(adjusted).enhance(.96)
        self.assertTrue(s.is_similar(self.fp, s.fingerprint(adjusted)))

    def test_different_picture_same_dimensions(self):
        self.assertFalse(s.is_similar(self.fp, s.fingerprint(artwork(7))))

    def test_hash_collision_not_enough(self):
        different = s.fingerprint(artwork(8))
        different.update(phash=self.fp['phash'], dhash=self.fp['dhash'])
        self.assertTrue(s.coarse_match(self.fp, different))
        self.assertFalse(s.is_similar(self.fp, different))

    def test_same_luminance_different_palette(self):
        # These palettes have almost the same luminance but very different hue.
        mask = np.asarray(self.image.convert('L')) > 170
        a = np.empty((*mask.shape, 3), np.uint8)
        b = np.empty_like(a)
        a[mask], a[~mask] = (230, 0, 0), (0, 120, 0)
        b[mask], b[~mask] = (0, 117, 0), (235, 0, 0)
        af, bf = s.fingerprint(Image.fromarray(a)), s.fingerprint(Image.fromarray(b))
        bf.update(phash=af['phash'], dhash=af['dhash'])
        self.assertFalse(s.is_similar(af, bf))

    def test_different_aspect_even_if_stretched(self):
        stretched = self.image.resize((900, 1024), Image.Resampling.LANCZOS)
        self.assertFalse(s.is_similar(self.fp, s.fingerprint(stretched)))

    def test_flat_color_and_gradient_no_hash_only_dedupe(self):
        flat = s.fingerprint(Image.new('RGB', (768, 1024), (130, 130, 130)))
        self.assertFalse(s.is_similar(flat, flat))
        ramp = np.broadcast_to(np.linspace(0, 255, 768, dtype=np.uint8)[None, :, None], (1024, 768, 3)).copy()
        gradient = s.fingerprint(Image.fromarray(ramp))
        self.assertFalse(s.is_similar(gradient, gradient))

    def test_zero_disables_near_duplicate_filter(self):
        self.assertFalse(s.coarse_match(self.fp, self.fp, 0))
        self.assertFalse(s.is_similar(self.fp, self.fp, 0))

    def test_malformed_data_fails_closed(self):
        cases = [None, [], {}, {**self.fp, 'phash': 'garbage'}, {**self.fp, 'dhash': 'f' * 100},
                 {**self.fp, 'width': float('nan')}, {**self.fp, 'height': 0},
                 {**self.fp, 'width': True}, {**self.fp, 'thumbnail': 'not-base64'},
                 {**self.fp, 'thumbnail': '!' * 4096}, {**self.fp, 'thumbnail': '界' * 4096}]
        for value in cases:
            self.assertFalse(s.is_similar(self.fp, value), str(value)[:70])
        for limit in [None, float('nan'), float('inf'), -1, 0, 99, 'oops', True]:
            self.assertFalse(s.is_similar(self.fp, self.fp, limit), str(limit))


if __name__ == '__main__':
    unittest.main(verbosity=2)
