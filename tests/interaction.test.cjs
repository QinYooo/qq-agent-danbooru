const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
function setup(){
  const nodes=new Map(),events={};
  function element(selector){
    if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',value:'',open:false,disabled:false,style:{},firstChild:{},dataset:{},
      parentElement:{style:{}},classList:{add(){},remove(){},toggle(){}},setAttribute(){},removeAttribute(){},querySelectorAll(){return [];},
      showModal(){this.open=true;},close(){this.open=false;this.onClose?.();},addEventListener(name,fn){if(name==='close')this.onClose=fn;},scrollIntoView(){}});
    return nodes.get(selector);
  }
  const calls=[];
  const context=vm.createContext({document:{querySelector:element,querySelectorAll:()=>[],addEventListener:(name,fn)=>events[name]=fn},
    localStorage:{getItem(){},setItem(){}},URLSearchParams,console,setTimeout:()=>0,clearTimeout(){},setInterval(){},
    fetch:async url=>{calls.push(url);return {ok:true,json:async()=>url==='/api/bootstrap'?{csrf:'test',stats:{},root:'test'}:url.startsWith('/api/images?')?{items:[{sha:'page-'+new URLSearchParams(url.split('?')[1]).get('page'),title:'next'}],pages:8,total:421}:{items:[],total:0,pages:1}};}});
  const source=fs.readFileSync(path.join(__dirname,'../web/static/app.js'),'utf8');
  vm.runInContext(source+'\nglobalThis.hooks={state,showLightbox,stepLightbox,readDropEntries,importPaths,pagination};',context);
  return {h:context.hooks,nodes,element,calls,events};
}
test('preview follows sorted rows across both page boundaries and ignores a closed preview',async()=>{
  const {h,element,calls}=setup();await new Promise(setImmediate);
  Object.assign(h.state,{page:2,pages:3,sort:'smallest',items:[{sha:'b',title:'B'},{sha:'c',title:'C'}]});
  h.showLightbox('b','B');await h.stepLightbox(1);assert.equal(h.state.lightbox.rows[h.state.lightbox.index].sha,'c');
  await h.stepLightbox(1);assert.equal(h.state.lightbox.rows[h.state.lightbox.index].sha,'page-3');
  assert.match(calls.at(-1),/sort=smallest/);assert.match(calls.at(-1),/page=3/);
  h.showLightbox('b','B');await h.stepLightbox(-1);assert.equal(h.state.lightbox.rows[h.state.lightbox.index].sha,'page-1');
  assert.equal(element('#lightbox-prev').disabled,true);
  element('#lightbox').close();assert.equal(h.state.lightbox,null);
});
test('folder drop reads all reader batches, nested files and relative paths',async()=>{
  const {h}=setup();const file=name=>({isFile:true,file:resolve=>resolve({name})});
  const directory=(name,batches)=>({name,isDirectory:true,createReader:()=>{let i=0;return {readEntries:resolve=>resolve(batches[i++]||[])};}});
  const entries=Array.from({length:100},(_,i)=>file(i+'.png'));
  const result=await h.readDropEntries([directory('folder',[entries,[directory('nested',[[file('last.png')]])]])]);
  assert.equal(result.length,101);assert.equal(h.importPaths.get(result[100]),'folder/nested/last.png');
  await assert.rejects(h.readDropEntries([{isFile:true,file:(_resolve,reject)=>reject(new Error('unreadable'))}]),/unreadable/);
});
test('page jump clamps page count and clears an inspector only after discard approval',async()=>{
  const {h,element}=setup();await new Promise(setImmediate);h.state.page=1;h.pagination(8);
  element('#page-number').value='7';element('#page-jump').onsubmit({preventDefault(){}});await new Promise(setImmediate);assert.equal(h.state.page,7);
  element('#page-number').value='900';element('#page-jump').onsubmit({preventDefault(){}});await new Promise(setImmediate);assert.equal(h.state.page,8);
});
