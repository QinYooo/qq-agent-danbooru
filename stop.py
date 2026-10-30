import json
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parent/'web'))
from runtime_config import load_config
config=load_config();url='http://127.0.0.1:'+str(config['port'])
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(url+'/api/bootstrap',timeout=5) as response:token=json.load(response)['csrf']
    request=urllib.request.Request(url+'/api/shutdown',data=b'{}',headers={'Content-Type':'application/json','X-CSRF-Token':token},method='POST')
    with opener.open(request,timeout=5) as response:response.read()
except OSError:pass
