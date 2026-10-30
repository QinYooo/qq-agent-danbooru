import test from 'node:test';
import assert from 'node:assert/strict';
import {catalogFor,buildInverted,applyFilter,directFilter,createRetriever} from '../plugins/character-gallery/retrieval.js';
const rows=[
  {key:'a',file:'a',tags:{'rio_(blue_archive)':{category:'character'},pantyhose:{category:'general'},glasses:{category:'general'}}},
  {key:'b',file:'b',tags:{'rio_(armed)_(blue_archive)':{category:'character'},thighhighs:{category:'general'}}},
  {key:'c',file:'c',tags:{'other':{category:'character'},pantyhose:{category:'general'}}}
];
const index={records:{'rio_(blue_archive)':{name:'调月莉音',aliases:['莉音']},'rio_(armed)_(blue_archive)':{name:'战斗服莉音',aliases:['战斗服莉音']},other:{name:'其他角色',aliases:['别人']}}};
const catalog=catalogFor(rows,index,''),inverted=buildInverted(rows);
test('exact name expands role costumes but explicit costume never broadens',()=>{
  assert.deepEqual(directFilter('莉音',catalog).groups,[['rio_(blue_archive)','rio_(armed)_(blue_archive)']]);
  assert.deepEqual(directFilter('战斗服莉音',catalog).groups,[['rio_(armed)_(blue_archive)']]);
  assert.equal(directFilter('穿丝袜的莉音',catalog),null);
});
test('inverted AND/OR/NOT matches the original scan exactly',()=>{
  const args={groups:[['rio_(blue_archive)','rio_(armed)_(blue_archive)'],['pantyhose','thighhighs']],exclude:['glasses']};
  assert.deepEqual(applyFilter(rows,catalog,args,inverted).items,applyFilter(rows,catalog,args).items);
  assert.deepEqual(applyFilter(rows,catalog,args,inverted).items.map(r=>r.key),['b']);
});
test('unambiguous local lookup never calls model and repeated complex plans reuse cache',async()=>{
  let reads=0,calls=0;
  const retrieve=createRetriever(index,{log(){}},{readDatabase:async()=>{reads++;return rows;},complete:async()=>{
    calls++;return {message:{tool_calls:[{id:'one',function:{name:'select_images',arguments:JSON.stringify({groups:[['rio_(blue_archive)'],['pantyhose']]})}}]}};
  }});
  const cfg={outputRoot:process.cwd(),aliases:''};
  assert.equal((await retrieve('莉音',cfg)).metrics.source,'local_exact');assert.equal(calls,0);
  assert.equal((await retrieve('穿丝袜的莉音',cfg)).metrics.source,'agent');
  assert.equal((await retrieve('穿丝袜的莉音',cfg)).metrics.source,'plan_cache');
  assert.equal(reads,1);assert.equal(calls,1);
});
test('ambiguous trusted aliases stay with Agent instead of choosing a character',()=>{
  const ambiguous=catalogFor(rows,{records:{'rio_(blue_archive)':{name:'春',aliases:['小春']},other:{name:'春',aliases:['小春']}}},'');
  assert.equal(directFilter('小春',ambiguous),null);
});
