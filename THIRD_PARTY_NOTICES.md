# Third-party components

The MIT license at the repository root applies to this project's code. Models,
upstream datasets and installed Python packages retain their own licenses.

| Component | Source | Handling |
| --- | --- | --- |
| QR Code generator | https://github.com/nayuki/QR-Code-generator/blob/bcfe1b018cec5f1f91b5705eca5bb4cdd34e9f8d/python/qrcodegen.py | MIT, copyright and full license retained in `web/qrcodegen.py`. Unmodified vendored source, SHA-256 `9f4ed1dd201dcb92b1bc0d6e14f46c754bcff0ce48580c5d7e8ace8f6926c8ef`. QR codes are generated locally. |
| PixAI Tagger v1.0 | https://huggingface.co/pixai-labs/pixai-tagger-v1.0 | Apache-2.0. Weights and the reviewed architecture are downloaded at the pinned revision in `model-manifest.json`, not stored in Git. SHA-256 is checked before use. |
| Anime classification | https://huggingface.co/deepghs/anime_classification | MIT. The selected ONNX model is downloaded at a pinned revision, not stored in Git. |
| Tag Chinese translations | https://github.com/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table | MIT. `assets/tag-zh.json.gz` is a compressed projection of the translation data. Copyright and license text are retained in `third_party/TAG-TRANSLATIONS-LICENSE.txt`. |
| Character names | https://github.com/Jannchie/danbooru-tag-index | The upstream corpus is not redistributed in this repository or its release ZIP. Setup downloads JSON data from upstream and builds a local search index. The upstream data is not relicensed as part of this project. Project-maintained starter aliases are provided separately. |

The character display-name inputs use revision
`f7bb19c2d52ab0154d848344a91250e52da5e944`. Optional broader wiki aliases are fetched
from the upstream `data-latest` release as data only. They are search candidates,
not authoritative identity mappings. No downloaded name-index code is executed.

The model's architecture file is executable Python, so it is accepted only when
its content matches the pinned hash. Apache license text is included at
`third_party/APACHE-2.0.txt`. Model cards and other upstream notices remain in the
downloaded model folders. PyTorch and all other packages are installed from their
official package indexes, under their respective licenses.
