"""Package only Git-tracked source files; never bundle libraries or runtime state."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('tag');args=parser.parse_args()
name=(ROOT/'.danbooru-project').read_text(encoding='utf-8').strip()
if not args.tag.startswith('v') or any(c in args.tag for c in '/\\\r\n'):raise ValueError('Invalid release tag')
files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode('utf-8').split('\0')
target=ROOT/'dist'/(name+'-'+args.tag+'.zip');target.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=8) as archive:
    for relative in sorted(name for name in files if name):
        path=ROOT/relative
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):raise ValueError('Unexpected tracked path')
        data=path.read_bytes()
        if relative.endswith('.cmd'):data=data.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
        archive.writestr(relative,data)
digest=hashlib.sha256(target.read_bytes()).hexdigest()
(target.parent/'SHA256SUMS.txt').write_text(digest+'  '+target.name+'\n',encoding='ascii')
print(str(target))
