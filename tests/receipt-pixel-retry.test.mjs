import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createController,DEFAULTS} from '../plugins/character-gallery/gallery.js';

const root=await fs.mkdtemp(path.join(os.tmpdir(),'qq-gallery-pixel-retry-'));
const original=path.join(root,'original.png'),variant=path.join(root,'variant.png');
await fs.writeFile(original,'original-fixture');await fs.writeFile(variant,'variant-fixture');
const key='a'.repeat(64);
const missing='OneBot send_group_msg 失败: retcode=100 group message delivery was not confirmed: QQ returned no message receipt';
function harness(sendMedia,settings={}) {
  const state=new Map(),calls=[],prepared=[],notices=[];
  const controller=createController({config:()=>({...DEFAULTS,cooldownSeconds:0,...settings}),
    storage:{get:(k,v)=>state.get(k)??v,set:(k,v)=>state.set(k,v)},log(){},warn(){}},{records:{}},{
    retrieve:async()=>({items:[{key,file:original}],tags:['tag'],name:'fixture'}),pause:async()=>{},
    prepareReceiptRetry:async item=>{prepared.push(item);return {key,file:variant,bytes:(await fs.stat(variant)).size};}
  });
  controller.start();
  controller.handle({chatKey:'group:300',event:{post_type:'message',message_type:'group',group_id:300,user_id:200,self_id:100,message_id:1,
    message:[{type:'at',data:{qq:'100'}},{type:'text',data:{text:'来1张莉音'}}]},
    send:async text=>notices.push(text),sender:{sendMedia:async(...args)=>{calls.push(args);return sendMedia(calls.length,...args);}}});
  return {controller,state,calls,prepared,notices};
}

test('a missing receipt submits exactly one modified copy and counts success once',async t=>{
  const h=harness(async n=>{if(n===1)throw new Error(missing);});t.after(()=>h.controller.stop());
  await h.controller.idle();
  assert.equal(h.calls.length,2);assert.equal(h.prepared.length,1);assert.equal(h.prepared[0].file,original);
  const images=h.calls.map(call=>Buffer.from(call[1].find(s=>s.type==='image').data.file.slice(9),'base64').toString());
  assert.deepEqual(images,['original-fixture','variant-fixture']);
  assert.equal(h.calls[1][1].some(s=>s.type==='text'),false);
  assert.notEqual(h.calls[0][2].dedupeKey,h.calls[1][2].dedupeKey);
  const result=h.state.get('lastResult');assert.equal(result.status,'sent');assert.equal(result.count,1);
  assert.equal(result.receiptPixelRetries.length,1);assert.equal(result.receiptPixelRetries[0].status,'sent');
  assert.deepEqual(Object.keys(h.state.get('recentGroupImages')),['group:300|'+key]);
  assert.equal(h.state.has('deliverySkips'),false);assert.equal(await fs.readFile(original,'utf8'),'original-fixture');
});

test('missing receipts on both attempts never cause a third submission',async t=>{
  const h=harness(async()=>{throw new Error(missing);});t.after(()=>h.controller.stop());
  await h.controller.idle();assert.equal(h.calls.length,2);assert.equal(h.prepared.length,1);
  const result=h.state.get('lastResult');assert.equal(result.count,0);assert.equal(result.unconfirmedKeys.length,1);
  assert.equal(result.receiptPixelRetries[0].status,'failed');
  assert.ok(h.state.get('deliverySkips')['group:300|'+key]);
  assert.doesNotMatch(h.notices[0],/没有重发/);
});

test('a normal timeout cannot trigger image modification or a second send',async t=>{
  const h=harness(async()=>{throw new Error('upload timeout');});t.after(()=>h.controller.stop());
  await h.controller.idle();assert.equal(h.calls.length,1);assert.equal(h.prepared.length,0);
});

test('the setting can disable receipt pixel retry',async t=>{
  const h=harness(async()=>{throw new Error('sequence=null');},{receiptPixelRetry:false});t.after(()=>h.controller.stop());
  await h.controller.idle();assert.equal(h.calls.length,1);assert.equal(h.prepared.length,0);
  assert.equal(h.state.get('lastResult').unconfirmedKeys.length,1);
});

test('a transport failure on the modified copy is not retried again',async t=>{
  const h=harness(async n=>{throw new Error(n===1?missing:'highway upload transport failed after 3 attempts: ECONNRESET');});
  t.after(()=>h.controller.stop());await h.controller.idle();assert.equal(h.calls.length,2);
  assert.equal(h.state.get('lastResult').uploadRetries,0);
  assert.equal(h.state.get('lastResult').receiptPixelRetries[0].status,'failed');
});

test.after(async()=>{
  assert.ok(root.startsWith(path.resolve(os.tmpdir())+path.sep));
  assert.ok(path.basename(root).startsWith('qq-gallery-pixel-retry-'));
  await fs.rm(root,{recursive:true,force:true});
});
