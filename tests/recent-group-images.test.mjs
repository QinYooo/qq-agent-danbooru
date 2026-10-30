import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createSentImageHistory,selectRandom,createController,DEFAULTS} from '../plugins/character-gallery/gallery.js';

const hour=3600000;
const a={key:'a'.repeat(64)},b={key:'b'.repeat(64)},c={key:'c'.repeat(64)};
function storage() {
  const values=new Map();return {values,get:(key,fallback)=>values.get(key)??fallback,set:(key,value)=>values.set(key,structuredClone(value))};
}

test('recent images have lower priority only in the group where they were sent',()=>{
  const data=storage(),history=createSentImageHistory(data,{now:()=>100000000});
  history.remember('group:300',[a],24*hour);
  assert.deepEqual(selectRandom([a,b],1,history.keysFor('group:300',[a,b],24*hour),()=>0),[b]);
  assert.deepEqual(history.keysFor('group:301',[a,b],24*hour),[]);
});

test('short pools still return all requested available images without duplicates',()=>{
  const data=storage(),history=createSentImageHistory(data,{now:()=>100000000});
  history.remember('group:300',[a,b],24*hour);
  const result=selectRandom([a,b,c],3,history.keysFor('group:300',[a,b,c],24*hour),()=>0);
  assert.equal(result[0].key,c.key);assert.equal(new Set(result.map(item=>item.key)).size,3);
});

test('history survives reload and expires at the configured window',()=>{
  let time=100000000;const data=storage();
  createSentImageHistory(data,{now:()=>time}).remember('group:300',[a],24*hour);
  const reloaded=createSentImageHistory(data,{now:()=>time});
  time+=23*hour;assert.deepEqual(reloaded.keysFor('group:300',[a,b],24*hour),[a.key]);
  time+=hour;assert.deepEqual(reloaded.keysFor('group:300',[a,b],24*hour),[]);
});

test('zero disables recording and storage stays bounded',()=>{
  const data=storage(),history=createSentImageHistory(data,{now:()=>100000000});
  history.remember('group:300',[a],0);assert.equal(data.values.has('recentGroupImages'),false);
  history.remember('group:300',Array.from({length:6010},(_,i)=>({key:String(i).padStart(64,'0')})),24*hour);
  assert.equal(Object.keys(data.values.get('recentGroupImages')).length,6000);
  assert.ok(Buffer.byteLength(JSON.stringify(data.values.get('recentGroupImages')))<600000);
  assert.deepEqual(history.keysFor('group:300',[a],0),[]);
});

test('character and whole-library requests share confirmed history; failed images are not recorded',async t=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'qq-gallery-history-'));
  t.after(async()=>{
    assert.ok(root.startsWith(path.resolve(os.tmpdir())+path.sep));
    assert.ok(path.basename(root).startsWith('qq-gallery-history-'));
    await fs.rm(root,{recursive:true,force:true});
  });
  const first={...a,file:path.join(root,'a.png')},second={...b,file:path.join(root,'b.png')},failed={...c,file:path.join(root,'c.png')};
  for(const item of [first,second,failed])await fs.writeFile(item.file,item.key);
  const data=storage(),sent=[];
  let candidates=[first],fail=false;
  const api={config:()=>({...DEFAULTS,cooldownSeconds:0}),storage:data,log(){},warn(){}};
  const options={retrieve:async()=>({items:candidates,tags:['fixture'],name:'fixture'}),listAll:async()=>candidates,pause:async()=>{}};
  let controller=createController(api,{records:{}},options);controller.start();
  const request=async(id,text)=>{
    controller.handle({chatKey:'group:300',event:{post_type:'message',message_type:'group',group_id:300,self_id:100,user_id:200,message_id:id,
      message:[{type:'at',data:{qq:'100'}},{type:'text',data:{text}}]},send:async()=>{},
      sender:{sendMedia:async(_chat,segments)=>{if(fail)throw new Error('network timeout');sent.push(Buffer.from(segments.find(s=>s.type==='image').data.file.slice(9),'base64').toString());}}});
    await controller.idle();
  };
  t.after(()=>controller.stop());
  await request(1,'来1张莉音');assert.deepEqual(sent,[a.key]);
  controller.stop();controller=createController(api,{records:{}},options);controller.start();
  candidates=[first,second];await request(2,'随机来1张图');assert.deepEqual(sent,[a.key,b.key]);
  candidates=[failed];fail=true;await request(3,'来1张丝袜');
  assert.equal(Object.hasOwn(data.values.get('recentGroupImages'),'group:300|'+c.key),false);
  assert.equal(Object.keys(data.values.get('recentGroupImages')).length,2);
});
