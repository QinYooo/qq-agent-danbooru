import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'engine'))
import worker as w


def prediction(tags=None, rating=None):
    return {'general': tags or {}, 'characters': {}, 'rating': rating or {'general':.01,'sensitive':.98,'questionable':.03,'explicit':.001}}


class StrictNsfwPolicyTests(unittest.TestCase):
    def test_body_shape_clothing_and_pose_are_not_explicit_evidence(self):
        for tag in ['breasts','large_breasts','cleavage','pantyhose','black_pantyhose','bare_shoulders','open_mouth','sitting','face','covered_nipples','nude_filter']:
            result = prediction({tag:.99})
            self.assertEqual(w.explicit_evidence(result,w.settings()),{},tag)
            self.assertEqual(w.nsfw_category(result,w.settings()),'SFW',tag)

    def test_reference_strong_tags_and_supported_complements_make_nsfw(self):
        supported = set(json.loads((Path(__file__).parents[1]/'assets/model-tags.json').read_text(encoding='utf-8'))['tags'])
        self.assertTrue(set(w.EXPLICIT_TAGS).issubset(supported))
        for tag in w.EXPLICIT_TAGS:
            self.assertEqual(w.nsfw_category(prediction({tag:.85}),w.settings()),'NSFW',tag)

    def test_old_point_two_sensitive_threshold_no_longer_routes_normal_art_to_nsfw(self):
        result=prediction({'pantyhose':.99},{'general':.81,'sensitive':.33,'questionable':.006,'explicit':.001})
        self.assertEqual(w.nsfw_category(result,w.settings()),'SFW')

    def test_high_adult_rating_without_keyword_is_rejected_not_relabelled_sfw(self):
        for label in ['questionable','explicit']:
            scores={'general':.02,'sensitive':.2,'questionable':.02,'explicit':.02}
            scores[label]=.96
            with self.assertRaisesRegex(w.Rejected,'uncertain_nsfw_evidence'):
                w.nsfw_category(prediction({'pantyhose':.99},scores),w.settings())

    def test_exact_matching_and_keyword_threshold(self):
        self.assertEqual(w.nsfw_category(prediction({'glasses':.99,'not_pussy':.99,'nipples':.59}),w.settings()),'SFW')
        self.assertEqual(w.nsfw_category(prediction({'nipples':.6}),w.settings()),'NSFW')

    def test_clear_keyword_can_resolve_uncertain_rating_but_invalid_outputs_fail(self):
        self.assertEqual(w.nsfw_category(prediction({'pussy':.95},{name:.25 for name in w.RATINGS}),w.settings()),'NSFW')
        with self.assertRaisesRegex(w.Rejected,'invalid_rating_output'):
            w.nsfw_category(prediction({'pussy':.95},{name:float('nan') for name in w.RATINGS}),w.settings())


if __name__ == '__main__':
    unittest.main(verbosity=2)
