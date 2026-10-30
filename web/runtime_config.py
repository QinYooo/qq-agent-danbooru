"""Portable defaults shared by the source checkout and installed distributions."""
import json
import os
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[1]


def load_config(filename=None):
    location=Path(filename) if filename else PROJECT/'config.json'
    raw=json.loads(location.read_text(encoding='utf-8-sig')) if location.exists() else {}
    cache=Path(raw.get('runtimeRoot') or os.getenv('DANBOORU_RUNTIME') or
        (Path(os.getenv('LOCALAPPDATA',Path.home()/'.local'/'share'))/'DanbooruToolkit')).expanduser().resolve()
    defaults={'root':str(Path.home()/'Pictures'/'Danbooru'),'host':'127.0.0.1','port':8766,
        'python':sys.executable,'workerDir':str(PROJECT/'engine'),'modelDir':str(cache/'models'/'pixai-tagger-v1.0'),
        'imageTypeModelDir':str(cache/'models'/'anime-image-type'),'dictionaryDir':str(PROJECT/'assets'),
        'importSettings':str(PROJECT/'settings.default.json'),'runtimeRoot':str(cache)}
    defaults.update(raw)
    if 'classificationEnabled' not in raw:
        defaults['classificationEnabled'] = (Path(defaults['modelDir'])/'model.safetensors').is_file() and (Path(defaults['imageTypeModelDir'])/'model.onnx').is_file()
    defaults['root']=str(Path(defaults['root']).expanduser().resolve())
    # Code/data paths belong to this checkout; user data and models may be shared.
    for key,name in (('workerDir','engine'),('dictionaryDir','assets')):
        defaults[key]=str(PROJECT/name)
    defaults['importSettings']=str(PROJECT/'settings.default.json')
    return defaults
