"""Full-frame artwork type classification, independent of character/content tags."""
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

TYPE_MODEL_ID = 'deepghs/anime_classification'
TYPE_REVISION = '5ee62e06f5f4cd68a1c2f3bc5dc9805e827d37df'
TYPE_VARIANT = 'mobilenetv3_v1.3_dist'
TYPE_SHA256 = 'fa233c04467cba4c68ecabd0065ad169c6fe867dc19799627cf5bd566a97d115'
LABELS = ('3d', 'bangumi', 'comic', 'illustration', 'not_painting')


def encode_image(image, size=384):
    # Matches imgutils.generic.classify._img_encode: preserve the full frame,
    # bilinear resize, RGB CHW, scale to [0,1], then mean=.5/std=.5.
    rgb = image.convert('RGB').resize((size, size), Image.Resampling.BILINEAR)
    array = np.asarray(rgb, dtype=np.float32).transpose(2, 0, 1) / 255.
    return ((array - .5) / .5)[None, ...]


def assess_scores(scores, min_score=.65, allow_3d=False):
    if set(scores) != set(LABELS) or any(not np.isfinite(v) or not 0 <= v <= 1 for v in scores.values()):
        raise RuntimeError('invalid_image_type_output')
    predicted = max(scores, key=scores.get)
    allowed = {'illustration', '3d'} if allow_3d else {'illustration'}
    accepted = predicted in allowed and scores[predicted] >= min_score
    return {
        'model': TYPE_MODEL_ID, 'revision': TYPE_REVISION, 'variant': TYPE_VARIANT,
        'predicted': predicted, 'scores': {key: round(value, 5) for key, value in scores.items()},
        'accepted': accepted, 'minimumScore': min_score,
        'reason': 'artwork_type_accepted' if accepted else 'uncertain_artwork_type' if predicted in allowed else 'unsuitable_image_type:' + predicted,
    }


class ImageTypeClassifier:
    def __init__(self, model_dir):
        import onnxruntime as ort
        path = Path(model_dir) / 'model.onnx'
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != TYPE_SHA256:
                raise RuntimeError('image_type_model_checksum_mismatch')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
        self.input = self.session.get_inputs()[0]
        if self.input.shape[1] != 3 or any(isinstance(size, int) and size != 384 for size in self.input.shape[2:]):
            raise RuntimeError('image_type_model_input_mismatch')

    def predict(self, image):
        outputs = dict(zip([out.name for out in self.session.get_outputs()], self.session.run(None, {self.input.name: encode_image(image)})))
        values = outputs['output'][0]
        if len(values) != len(LABELS):
            raise RuntimeError('image_type_model_output_mismatch')
        return dict(zip(LABELS, map(float, values)))
