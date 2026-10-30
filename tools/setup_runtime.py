"""Download verified model assets and configure a portable installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

PROJECT=Path(__file__).resolve().parents[1]


def dump(file,value):
    file.parent.mkdir(parents=True,exist_ok=True)
    temporary=file.with_suffix(file.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,file)


def sha256(file):
    with file.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def download(url,target,expected=None,size=None,maximum=None):
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.is_file() and (not expected or sha256(target)==expected):return
    part=target.with_suffix(target.suffix+'.part')
    if expected and part.is_file() and sha256(part)==expected:
        os.replace(part,target);return
    system_curl=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/curl.exe'
    curl=str(system_curl) if os.name=='nt' and system_curl.is_file() else None
    if curl:
        if size and part.exists() and part.stat().st_size>=size:part.unlink()
        command=[curl,'-4','--fail','--location','--show-error','--connect-timeout','20',
            '--speed-time','60','--speed-limit','1024','--retry','2','--continue-at','-',
            '--output',str(part)]
        if maximum or size:command+=['--max-filesize',str(maximum or size)]
        subprocess.run([*command,url],check=True)
        if size is not None and part.stat().st_size!=size:raise ValueError('Downloaded file size mismatch: '+target.name)
        if expected and sha256(part)!=expected:part.unlink();raise ValueError('SHA256 mismatch: '+target.name)
        os.replace(part,target);return
    for attempt in range(3):
        try:
            offset=part.stat().st_size if part.exists() else 0
            if size and offset>size:part.unlink();offset=0
            headers={'User-Agent':'DanbooruToolkit/1.0'}
            if offset:headers['Range']='bytes='+str(offset)+'-'
            with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=45) as response:
                if response.status!=206:offset=0
                last=offset
                with part.open('ab' if offset else 'wb') as stream:
                    while chunk:=response.read(4*1024*1024):
                        stream.write(chunk);offset+=len(chunk)
                        if maximum and offset>maximum:raise ValueError('Upstream data exceeds size limit')
                        if offset-last>=64*1024*1024:
                            print(f'  {target.name}: {offset//1024**2} MiB'+(f' / {size//1024**2} MiB' if size else ''),flush=True);last=offset
            if size is not None and offset!=size:raise ValueError('Downloaded file size mismatch: '+target.name)
            if expected and sha256(part)!=expected:part.unlink();raise ValueError('SHA256 mismatch: '+target.name)
            os.replace(part,target);return
        except Exception:
            if attempt==2:raise
            print('  Retry download:',target.name,flush=True);time.sleep(2*(attempt+1))


def build_names(skip=False):
    assets=PROJECT/'assets';seed=json.loads((assets/'characters.seed.json').read_text(encoding='utf-8'))
    official={};manual={};pool={}
    if not skip:
        try:
            for item in json.loads((PROJECT/'name-sources.json').read_text(encoding='utf-8')):
                file=assets/'upstream'/item['name'];download(item['url'],file,item['sha256'],maximum=32*1024**2)
                value=json.loads(file.read_text(encoding='utf-8-sig'))
                if item['name']=='character_official.json':official=value
                else:manual=value
            file=assets/'upstream'/'character_names.json'
            try:
                download('https://github.com/Jannchie/danbooru-tag-index/releases/download/data-latest/character_names.json',file,maximum=64*1024**2)
                pool=json.loads(file.read_text(encoding='utf-8-sig'))
            except Exception:print('  Broad alias pool unavailable; using pinned names and starter aliases.',flush=True)
        except Exception as error:
            print('  Optional upstream names unavailable:',type(error).__name__,flush=True)
    records={}
    for tag in sorted(set(official)|set(manual)|set(pool)|set(seed['characters'])):
        names=official.get(tag,{})
        if not isinstance(names,dict):names={}
        trusted=[name for name in names.values() if isinstance(name,str) and name.strip()]
        custom=manual.get(tag)
        if isinstance(custom,str) and custom.strip():trusted.append(custom)
        title=names.get('zh_hans') or names.get('zh_hant') or custom or tag
        hints=[]
        candidate=pool.get(tag,{})
        if isinstance(candidate,dict):
            for language in ('zh_hans','zh_hant','en','ja'):
                values=candidate.get(language,[])
                if isinstance(values,list):hints.extend(name for name in values if isinstance(name,str) and 2<=len(name)<=60)
        starter=seed['characters'].get(tag)
        if starter:
            trusted.extend(starter['aliases'])
            if title==tag:title=starter['name']
        records[tag]={'name':title,'aliases':sorted(set(trusted)),'searchAliases':sorted(set(hints)-set(trusted))}
    dump(assets/'characters.json',{'characters':records,'source':'Upstream JSON plus project starter aliases'})
    print('  Character records:',len(records),flush=True)


def install_plugins(agent,config):
    directory=Path(agent).expanduser().resolve(strict=True)
    app=directory/'resources'/'app' if (directory/'resources'/'app'/'plugins').is_dir() else directory
    if not (app/'plugins').is_dir() or not (app/'src'/'plugin-loader.js').is_file():
        raise RuntimeError('QQ Agent app directory not found. Set -QQAgentDir to its installation directory.')
    runtime=Path(config['runtimeRoot']);stage=runtime/'install-staging'/uuid.uuid4().hex
    backup=runtime/'backups'/time.strftime('%Y%m%d-%H%M%S')
    for identifier in ('danbooru-archive','character-gallery'):
        source=PROJECT/'plugins'/identifier;candidate=stage/identifier
        shutil.copytree(source,candidate,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        manifest=json.loads((candidate/'plugin.json').read_text(encoding='utf-8'))
        values={'pythonPath':config['python'],'outputRoot':config['root']}
        if identifier=='danbooru-archive':values.update(modelDir=config['modelDir'],imageTypeModelDir=config['imageTypeModelDir'])
        for key,value in values.items():
            manifest['settings'][key]=value
            if key in manifest.get('configSchema',{}):manifest['configSchema'][key]['default']=value
        dump(candidate/'plugin.json',manifest)
        if identifier=='character-gallery':shutil.copyfile(PROJECT/'assets'/'characters.json',candidate/'characters.json')
        destination=app/'plugins'/identifier
        if destination.exists():
            backup.mkdir(parents=True,exist_ok=True)
            shutil.copytree(destination,backup/identifier)
            # Existing runtime state belongs to the host's data directory.
            for file in candidate.rglob('*'):
                if file.is_file():
                    relative=file.relative_to(candidate);target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
        else:
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(candidate),str(destination))
        print('  Installed:',destination,flush=True)
    print('Open QQ Agent > Plugins, trust the two modules, then enable them. Existing trust settings are preserved.',flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-dir',required=True);parser.add_argument('--library',required=True)
    parser.add_argument('--qq-agent-dir');parser.add_argument('--browse-only',action='store_true');parser.add_argument('--skip-downloads',action='store_true')
    parser.add_argument('--no-launch',action='store_true');parser.add_argument('--no-shortcut',action='store_true')
    args=parser.parse_args();runtime=Path(args.runtime_dir).expanduser().resolve();runtime.mkdir(parents=True,exist_ok=True)
    root=Path(args.library).expanduser().resolve();root.mkdir(parents=True,exist_ok=True)
    config={'root':str(root),'host':'127.0.0.1','port':8766,'python':sys.executable,'runtimeRoot':str(runtime),
        'modelDir':str(runtime/'models'/'pixai-tagger-v1.0'),'imageTypeModelDir':str(runtime/'models'/'anime-image-type'),
        'classificationEnabled':not args.browse_only}
    if not args.browse_only:
        for model in json.loads((PROJECT/'model-manifest.json').read_text(encoding='utf-8')):
            folder=Path(config['imageTypeModelDir'] if 'variant' in model else config['modelDir'])
            for item in model['files']:
                print('Model asset:',item['name'],flush=True)
                if args.skip_downloads:
                    if not (folder/item['name']).is_file() or sha256(folder/item['name'])!=item['sha256']:raise RuntimeError('Verified model asset missing: '+item['name'])
                else:download(item['url'],folder/item['name'],item['sha256'],item['bytes'])
    build_names(args.skip_downloads)
    dump(PROJECT/'config.json',config)
    if (PROJECT/'plugins').is_dir():
        if not args.qq_agent_dir:raise RuntimeError('QQ Agent directory is required for the plugin distribution.')
        install_plugins(args.qq_agent_dir,config)
    if not args.no_shortcut and os.name=='nt':
        subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(PROJECT/'tools'/'shortcut.ps1'),
            '-PythonPath',sys.executable,'-ProjectDir',str(PROJECT)],check=True)
    print('Installation complete. Library:',root,flush=True)
    if not args.no_launch:subprocess.Popen([sys.executable,'-B',str(PROJECT/'start.py')])


if __name__=='__main__':main()
