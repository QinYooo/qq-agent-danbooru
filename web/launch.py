"""Start the loopback server and open its URL in the default browser."""
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import webbrowser
import os
from runtime_config import load_config


def main():
    config=load_config();project=Path(__file__).resolve().parents[1]
    url='http://127.0.0.1:'+str(config['port'])
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def healthy():
        try:
            with opener.open(url+'/api/health',timeout=1) as response:
                return json.load(response).get('app')=='danbooru-manager'
        except Exception:return False
    if not healthy():
        logdir=Path(config['root'])/'_state'/'manager';logdir.mkdir(parents=True,exist_ok=True)
        with (logdir/'server.log').open('a',encoding='utf-8') as log:
            subprocess.Popen([config['python'],'-B','-u','-X','utf8',str(project/'web'/'app.py')],cwd=project,
                stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        for _ in range(60):
            if healthy():break
            time.sleep(.5)
    if healthy():webbrowser.open(url)
    else:raise RuntimeError('Server did not start. See '+str(Path(config['root'])/'_state'/'manager'/'server.log'))


if __name__=='__main__':main()
