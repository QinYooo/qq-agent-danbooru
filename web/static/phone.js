'use strict';
const $=id=>document.getElementById(id),items=[];
let token=new URLSearchParams(location.hash.slice(1)).get('token')||'',running=false,connected=false,pollTimer;
try{token=token||sessionStorage.getItem('phone-token')||'';}catch{}
history.replaceState(null,'',location.pathname);
function id(){const bytes=new Uint8Array(16);if(globalThis.crypto?.getRandomValues)crypto.getRandomValues(bytes);else for(let i=0;i<16;i++)bytes[i]=Math.floor(Math.random()*256);return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');}
async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{Authorization:'Bearer '+token,...options.headers},signal:AbortSignal.timeout(180000)});
  const value=await response.json();if(!response.ok){const error=Error(value.error||'请求失败');error.status=response.status;throw error;}return value;
}
async function connect(){
  try{await api('/api/status');connected=true;try{sessionStorage.setItem('phone-token',token);}catch{}
    $('connection').textContent='已连接电脑 · 原图上传后自动标注收录';$('pair').hidden=true;$('controls').hidden=false;
  }catch(error){connected=false;$('connection').textContent=error.message;$('pair').hidden=false;}refresh();
}
function refresh(){const done=items.filter(x=>x.uploaded).length;$('summary').textContent=`共 ${items.length} 张 · 已校验上传 ${done} · 未完成 ${items.length-done}`;$('start').disabled=running||!connected||!items.some(x=>!x.uploaded&&!x.failed);$('retry').disabled=running||!connected||!items.some(x=>x.failed);}
function message(item,text,kind=''){item.text.textContent=text;item.text.className=kind;}
function add(files){let skipped=0;for(const file of files){if(!/\.(jpg|jpeg|png|webp|bmp|gif)$/i.test(file.name)){skipped++;continue;}if(items.length>=20000){skipped++;continue;}const card=document.createElement('li'),name=document.createElement('b'),text=document.createElement('p');name.textContent=file.webkitRelativePath||file.name;text.textContent='等待上传';card.append(name,text);$('queue').append(card);items.push({file,name:name.textContent,text,id:id(),uploaded:false,failed:false});}refresh();if(skipped)$('summary').textContent+=` · 跳过 ${skipped} 个不支持或超量文件`;}
for(const input of ['photos','files','folder'])$(input).onchange=event=>{add(event.target.files);event.target.value='';};
if(!('webkitdirectory' in $('folder')))$('folder-label').hidden=true;
function hashFile(item){return new Promise((resolve,reject)=>{const worker=new Worker('/hash-worker.js');worker.onmessage=event=>{if(event.data.hash){worker.terminate();resolve(event.data.hash);}else if(event.data.error){worker.terminate();reject(Error(event.data.error));}else message(item,'正在校验手机原件 '+Math.round(event.data.progress*100)+'%');};worker.onerror=()=>{worker.terminate();reject(Error('无法校验原件，请改用系统浏览器'));};worker.postMessage({file:item.file});});}
const states={pending:'已上传，等待本地分类',saved:'已收录并移动到图库',duplicate:'重复图片',rejected:'已筛除，原件保留在电脑暂存区',error:'处理异常，原件保留，请在电脑重试'};
function complete(item,value){if(value.sha256!==item.hash||value.size!==item.file.size||value.name!==item.name)throw Error('电脑回执不匹配，请保留手机原件');item.uploaded=true;item.position=value.position;item.failed=false;message(item,states[value.status]||'已校验上传，等待本地分类',value.status==='saved'?'saved':'');}
async function progress(){
  try{const value=await api('/api/progress');for(const row of value.items){const item=items.find(x=>x.uploaded&&x.position===row.position);if(!item)continue;
    let text=states[row.status]||row.status;if(row.status==='duplicate')text=row.reason==='exact_duplicate'?'完全重复，图库已有同一原图，暂存副本已清理':'相似图片，原件保留在电脑暂存区';
    message(item,text,row.status==='error'?'error':row.status==='saved'?'saved':'');}
    if(value.items.some(x=>x.status==='pending')||running)pollTimer=setTimeout(progress,5000);
  }catch(error){$('connection').textContent='进度暂时不可用：'+error.message;}
}
async function run(){if(running||!connected)return;running=true;clearTimeout(pollTimer);refresh();try{
  for(const item of items){if(item.uploaded||item.failed)continue;try{
    if(!item.file.size||item.file.size>25*1024**2)throw Error('图片为空或超过25MiB');
    if(!item.hash)item.hash=await hashFile(item);
    if(item.attempted){try{complete(item,await api('/api/receipt?id='+item.id));continue;}catch(error){if(error.status!==404)throw error;}}
    item.attempted=true;message(item,'正在上传并等待电脑校验…');
    complete(item,await api('/api/upload?name='+encodeURIComponent(item.name),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Upload-ID':item.id,'X-File-SHA256':item.hash},body:item.file}));
  }catch(error){item.failed=true;message(item,error.message+'；保留手机原件，可重试','error');}refresh();}
}finally{running=false;refresh();progress();}}
$('start').onclick=run;$('retry').onclick=async()=>{await connect();if(!connected)return;for(const item of items)if(item.failed)item.failed=false;run();};
$('pair').onsubmit=event=>{event.preventDefault();token=$('token').value.trim();connect();};
window.addEventListener('beforeunload',event=>{if(running||items.some(x=>!x.uploaded)){event.preventDefault();event.returnValue='';}});
if(token)connect();else{$('connection').textContent='请扫描电脑上的二维码，或输入连接码';refresh();}
