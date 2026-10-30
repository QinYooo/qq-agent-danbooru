'use strict';
const $=selector=>document.querySelector(selector);
const paths={
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  review:'<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 3h6v4H9zM9 13l2 2 4-4"/>',
  star:'<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z"/>',
  trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
  tags:'<path d="M3 3h8l10 10-8 8L3 11z"/><circle cx="7.5" cy="7.5" r="1"/>',
  person:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  upload:'<path d="M12 16V3m-4 4 4-4 4 4M4 15v5h16v-5"/>',
  images:'<rect x="4" y="5" width="17" height="15" rx="2"/><path d="M1 3v14m4-1 5-5 4 4 2-2 4 4"/><circle cx="16" cy="9" r="1.5"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  refresh:'<path d="M20 9a8 8 0 0 0-14-4L3 8m0-5v5h5M4 15a8 8 0 0 0 14 4l3-3m0 5v-5h-5"/>',
  sort:'<path d="M4 6h16M4 12h11M4 18h6m7-2 3 3 3-3M20 10v9"/>',
  filter:'<path d="M4 6h16M7 12h10M10 18h4"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6"/>',
  arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  expand:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  download:'<path d="M12 3v13m-4-4 4 4 4-4M4 18v3h16v-3"/>',
  eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12"/><circle cx="12" cy="12" r="3"/>',
  unknown:'<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-1 .5-1 1.2-1 2.2M12 17h.01"/>',
  folder:'<path d="M3 7V4h7l2 3h9v13H3z"/>',
  check:'<path d="m5 12 4 4L20 5"/>'
};
function icon(name){return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]||paths.grid}</svg>`;}
document.querySelectorAll('[data-icon]').forEach(node=>node.innerHTML=icon(node.dataset.icon));
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const bytes=n=>n>=1024**3?(n/1024**3).toFixed(1)+' GiB':n>=1024**2?(n/1024**2).toFixed(1)+' MiB':Math.max(1,Math.round(n/1024))+' KiB';
const number=n=>Number(n||0).toLocaleString('zh-CN');
const date=n=>new Date(n*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
const state={csrf:'',stats:{},view:'all',q:'',tags:[],page:1,sort:'newest',rating:'all',items:[],selected:new Set(),
  selectMode:false,pages:1,lightbox:null,dropSequence:0,detail:null,edit:null,dirty:false,dense:localStorage.getItem('dense')==='1',reveal:localStorage.getItem('reveal')==='1',importTab:'files',files:[],jobs:[],load:0};
const views={all:['全部图片','YOUR VISUAL LIBRARY','收集好作品，也让每一张图都各归其位。'],today:['最近收录','RECENTLY COLLECTED','今天新收录的图片，在这里逐张查看与整理。'],
  review:['待整理','A LITTLE MORE CURATION','检查识别结果，补充标签，让图库更准确。'],starred:['我的收藏','WORTH KEEPING CLOSE','把格外喜欢的作品，放在随时能找到的地方。'],
  unknown:['角色未识别','FIND THEIR NAMES','这些作品已经收录，等待你为角色补上名字。'],trash:['回收站','ROOM TO RECONSIDER','移出图库的图片暂存在这里，可以恢复。'],
  tags:['标签浏览','EXPLORE BY DETAILS','从颜色、服饰到姿态，按画面细节探索收藏。'],characters:['角色目录','A FAMILIAR FACE','所有已收录角色，按图片数量排列。'],
  jobs:['导入任务','BRING IT ALL TOGETHER','跟踪本地分类进度，查看收录、筛除与去重结果。']};
async function api(url,body,method='POST'){
  const options=body===undefined?{}:{method,headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(body)};
  const response=await fetch(url,options);const data=await response.json();
  if(!response.ok)throw new Error(data.error||'请求失败');return data;
}
let toastTimer;
function toast(message,action){
  clearTimeout(toastTimer);const node=$('#toast');node.innerHTML=`<span>${esc(message)}</span>`;
  if(action){const button=document.createElement('button');button.textContent=action.text;button.onclick=()=>{node.classList.add('hidden');action.run();};node.append(button);}
  node.classList.remove('hidden');toastTimer=setTimeout(()=>node.classList.add('hidden'),action?9000:4500);
}
function confirmAction(title,message,label='确认',danger=false){
  return new Promise(resolve=>{
    const dialog=$('#confirm-dialog');$('#confirm-body').innerHTML=`<h2>${esc(title)}</h2><p>${esc(message)}</p><div class="dialog-footer"><button class="button subtle" data-answer="no">取消</button><button class="button ${danger?'danger':'primary'}" data-answer="yes">${esc(label)}</button></div>`;
    const finish=value=>{dialog.close();resolve(value);};
    dialog.querySelectorAll('[data-answer]').forEach(button=>button.onclick=()=>finish(button.dataset.answer==='yes'));
    dialog.oncancel=event=>{event.preventDefault();finish(false);};dialog.showModal();
  });
}
async function discardChanges(){return !state.dirty||await confirmAction('放弃未保存的修改？','这张图片的标签修改尚未保存。','放弃修改');}
function renderNavigation(){
  const items=[['all','grid','全部图片','total'],['today','clock','最近收录','today'],['review','review','待整理','unreviewed'],['starred','star','我的收藏','starred'],['trash','trash','回收站','trash']];
  $('#navigation').innerHTML=items.map(([view,name,label,count])=>`<button class="side-link ${state.view===view?'active':''}" data-view="${view}">${icon(name)}<span>${label}</span><b>${number(state.stats[count])}</b></button>`).join('');
  $('#collections-nav').innerHTML=[['tags','tags','标签浏览'],['characters','person','角色目录'],['unknown','unknown','角色未识别'],['jobs','upload','导入任务']].map(([view,name,label])=>`<button class="side-link ${state.view===view?'active':''}" data-view="${view}">${icon(name)}<span>${label}</span>${view==='unknown'?`<b>${number(state.stats.unknown)}</b>`:''}</button>`).join('');
  document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>navigate(button.dataset.view));
  $('#storage-line').textContent=`${number(state.stats.total)} 张图片 · ${bytes(state.stats.bytes||0)}`;
}
async function refreshStats(){try{state.stats=await api('/api/stats');renderNavigation();}catch{}}
async function shortcuts(){
  try{const data=await api('/api/tags?kind=character&scope=library&limit=5');
    $('#character-shortcuts').innerHTML=data.items.map(row=>`<button class="shortcut" data-tag="${esc(row.tag)}" title="${esc(row.tag)}"><i class="dot"></i><span>${esc(row.label)}</span><b>${row.count}</b></button>`).join('');
    $('#character-shortcuts').querySelectorAll('button').forEach((button,i)=>button.onclick=()=>browseTag(data.items[i]));
  }catch{}
}
async function navigate(view){
  if(!await discardChanges())return;closeInspector();state.view=view;state.selectMode=false;state.page=1;state.q='';state.tags=[];state.selected.clear();$('#search').value='';
  renderNavigation();renderSelection();loadContent();
}
async function browseTag(row){
  if(!await discardChanges())return;closeInspector();state.view='all';state.tags=[{tag:row.tag,label:row.label}];state.q='';state.page=1;$('#search').value='';renderNavigation();loadContent();
}
function renderFilters(){
  $('#active-filters').innerHTML=state.tags.map((row,i)=>`<span class="filter-chip" title="${esc(row.tag)}">${esc(row.label||row.tag)}<button data-remove="${i}" aria-label="移除筛选">×</button></span>`).join('');
  $('#active-filters').querySelectorAll('button').forEach(button=>button.onclick=()=>{state.tags.splice(Number(button.dataset.remove),1);state.page=1;loadContent();});
}
function loading(){ $('#content').className='photo-grid';$('#content').innerHTML=Array.from({length:8},()=>'<div class="skeleton"></div>').join('');$('#pagination').innerHTML='';}
function empty(title,description,name='images'){
  return `<div class="empty-state">${icon(name)}<h3>${esc(title)}</h3><p>${esc(description)}</p></div>`;
}
async function loadContent(quiet=false){
  const serial=++state.load;const [title,eyebrow,description]=views[state.view];
  $('#page-title').firstChild.textContent=title+' ';$('#breadcrumb-current').textContent=title;$('#eyebrow').textContent=eyebrow;$('#page-description').textContent=description;
  renderFilters();if(!quiet)loading();
  const imagesView=!['tags','characters','jobs','trash'].includes(state.view);
  $('#rating-filter').style.display=imagesView?'':'none';$('#sort').parentElement.style.display=imagesView?'':'none';$('#open-filter').style.display=imagesView?'':'none';
  try{
    if(state.view==='jobs'){
      const data=await api('/api/jobs');if(serial!==state.load)return;state.jobs=data.items;renderJobs();return;
    }
    if(state.view==='trash'){
      const data=await api('/api/trash');if(serial!==state.load)return;state.items=data.items;renderPhotos(data,true);return;
    }
    if(state.view==='tags'||state.view==='characters'){
      const data=await api('/api/tags?'+new URLSearchParams({kind:state.view==='characters'?'character':'general',scope:'library',q:state.q,limit:60,offset:(state.page-1)*60}));
      if(serial!==state.load)return;renderCatalog(data);return;
    }
    const data=await api('/api/images?'+new URLSearchParams({view:state.view,q:state.q,tags:state.tags.map(row=>row.tag).join(','),rating:state.rating,sort:state.sort,page:state.page,limit:60}));
    if(serial!==state.load)return;if(state.page>data.pages){state.page=data.pages;return loadContent();}state.pages=data.pages;state.items=data.items;renderPhotos(data);
  }catch(error){if(serial===state.load)$('#content').innerHTML=`<div class="load-error">${esc(error.message)}<br>可以点击右上方刷新后重试。</div>`;}
}
function renderPhotos(data,trash=false){
  $('#page-count').textContent=number(data.total);$('#result-caption').textContent=`${number(data.total)} 张${state.tags.length?'符合筛选条件的':''}图片${trash?' · 可恢复':''}`;
  $('#content').className='photo-grid'+(state.dense?' dense':'')+(state.selected.size?' select-mode':'');
  $('#content').innerHTML=data.items.map(row=>{
    const sensitive=row.category==='NSFW'&&!state.reveal;
    return `<article class="photo-card ${state.detail?.sha===row.sha?'current':''} ${state.selected.has(row.sha)?'selected':''}" data-sha="${row.sha}" tabindex="0" aria-label="查看 ${esc(row.title)}">
      <div class="photo-visual"><img loading="lazy" decoding="async" class="${sensitive?'sensitive':''}" src="/media/${row.sha}/thumb?size=480${trash?'&trash='+row.trashId:''}" alt="${sensitive?'敏感图片预览已模糊':esc(row.title)}">
      ${sensitive?`<div class="sensitive-cover">${icon('eye')}<span>敏感图片</span></div>`:''}
      <div class="card-top">${trash?'':`<input type="checkbox" class="card-checkbox" ${state.selected.has(row.sha)?'checked':''} aria-label="选择 ${esc(row.title)}">`}<span class="rating-badge ${row.category==='NSFW'?'nsfw':''}">${row.category}</span></div>
      ${row.starred?`<div class="card-star">${icon('star')}</div>`:''}
      ${trash?'<button class="trash-restore">恢复到图库</button>':''}</div>
      <div class="photo-info"><div style="min-width:0"><div class="photo-title">${esc(row.title)}</div><div class="photo-subtitle">${row.width} × ${row.height}${trash?' · '+date(row.deleted):' · '+bytes(row.bytes)}</div></div>${trash?'':`<i class="review-dot ${row.reviewed?'done':''}" title="${row.reviewed?'已整理':'待整理'}"></i>`}</div></article>`;
  }).join('')||empty(trash?'回收站是空的':'这里还没有图片',trash?'移出图库的图片会暂存在这里。':'试着换个筛选条件，或者导入一些新图片。');
  $('#content').querySelectorAll('.photo-card').forEach((card,i)=>{
    const row=data.items[i];
    card.onclick=event=>{if(trash){if(event.target.closest('.trash-restore'))restoreTrash(row.trashId);else showTrashPreview(row);return;}if(event.target.matches('.card-checkbox')){selectCard(row.sha,event.target.checked);return;}if(state.selectMode||event.ctrlKey||event.metaKey){selectCard(row.sha,!state.selected.has(row.sha));return;}openInspector(row.sha);};
    card.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();trash?showTrashPreview(row):openInspector(row.sha);}};
  });
  pagination(data.pages||1);renderSelection();$('#footer-summary').textContent=`本页 ${data.items.length} 张 · ${trash?'回收站':'原图保存在本地图库'}`;
}
function renderCatalog(data){
  $('#page-count').textContent=number(data.total);$('#result-caption').textContent=`${number(data.total)} 个${state.view==='characters'?'角色':'画面标签'} · 点击浏览关联图片`;
  $('#content').className='catalog-grid';
  $('#content').innerHTML=data.items.map(row=>`<button class="collection-card" title="${esc(row.tag)}"><div class="collection-image"><img loading="lazy" class="${row.rating==='NSFW'&&!state.reveal?'sensitive':''}" src="/media/${row.cover}/thumb?size=480" alt="${esc(row.label)}"></div><div class="collection-description"><strong>${esc(row.label)}</strong><small>${number(row.count)} 张图片</small></div></button>`).join('')||empty('没有找到分类','试试角色中文名，或者输入原始标签。','tags');
  $('#content').querySelectorAll('.collection-card').forEach((button,i)=>button.onclick=()=>browseTag(data.items[i]));pagination(Math.max(1,Math.ceil(data.total/60)));
}
function pagination(pages){
  $('#pagination').innerHTML=pages>1?`<button id="previous-page" ${state.page<=1?'disabled':''}>上一页</button><span>${state.page} / ${pages}</span><button id="next-page" ${state.page>=pages?'disabled':''}>下一页</button><form id="page-jump"><label>跳至 <input id="page-number" type="number" min="1" max="${pages}" value="${state.page}" aria-label="跳转页码"> 页</label><button>前往</button></form>`:'';
  const jump=async page=>{if(!await discardChanges())return;closeInspector();state.page=Math.max(1,Math.min(pages,page));$('#content-scroll').scrollTop=0;loadContent();};
  for(const [selector,delta] of [['#previous-page',-1],['#next-page',1]])if($(selector))$(selector).onclick=()=>jump(state.page+delta);
  if($('#page-jump'))$('#page-jump').onsubmit=event=>{event.preventDefault();const value=Number($('#page-number').value);if(Number.isInteger(value))jump(value);};
}
function selectCard(sha,selected){selected?state.selected.add(sha):state.selected.delete(sha);renderSelection();}
function renderSelection(){
  $('#toggle-selection').setAttribute('aria-pressed',String(state.selectMode));
  $('#selection-bar').classList.toggle('hidden',!state.selected.size&&!state.selectMode);$('#selection-count').textContent=`已选择 ${state.selected.size} 张`;
  $('#content').classList.toggle('select-mode',state.selectMode||!!state.selected.size);
  $('#content').querySelectorAll('.photo-card').forEach(card=>{const selected=state.selected.has(card.dataset.sha);card.classList.toggle('selected',selected);const input=card.querySelector('input');if(input)input.checked=selected;});
}
class TagPicker{
  constructor(container,items,kind,onChange){this.container=container;this.items=items.map(row=>({...row}));this.kind=kind;this.onChange=onChange;this.timer=null;this.sequence=0;this.render();}
  render(){
    this.container.innerHTML=`<div class="tagbox">${this.items.map((row,i)=>`<span class="tag-pill ${this.kind==='character'?'character':''}" title="${esc(row.tag)}"><span>${esc(row.label||row.tag)}</span>${this.kind==='character'&&i===0?'<b>主</b>':''}${this.kind==='character'&&i>0?`<button data-primary="${i}" title="设为主要角色">↑</button>`:''}<button data-delete="${i}" aria-label="移除 ${esc(row.label||row.tag)}">×</button></span>`).join('')}<input class="tag-input" placeholder="${this.kind==='character'?'+ 搜索或添加角色':this.kind==='any'?'+ 搜索角色或画面标签':'+ 搜索或添加标签'}" autocomplete="off"><div class="tag-suggestions hidden"></div></div>`;
    this.input=this.container.querySelector('input');this.suggestions=this.container.querySelector('.tag-suggestions');
    this.container.querySelectorAll('[data-delete]').forEach(button=>button.onclick=()=>{this.items.splice(Number(button.dataset.delete),1);this.change();});
    this.container.querySelectorAll('[data-primary]').forEach(button=>button.onclick=()=>{const [row]=this.items.splice(Number(button.dataset.primary),1);this.items.unshift(row);this.change();});
    this.input.oninput=()=>{clearTimeout(this.timer);this.timer=setTimeout(()=>this.search(),200);};
    this.input.onfocus=()=>{if(this.input.value.trim())this.search();};
    this.input.onkeydown=event=>{
      if(event.key==='Escape'){this.suggestions.classList.add('hidden');event.stopPropagation();}
      if(event.key==='Enter'){event.preventDefault();const text=this.input.value.trim();if(!text)return;
        const exact=(this.matches||[]).find(row=>row.tag.toLowerCase()===text.toLowerCase()||row.label===text);
        if(exact)this.add(exact);else if(/[\u3400-\u9fff]/.test(text))toast('请选择建议中的对应标签，或输入英文标签创建。');else this.add({tag:text.replace(/\s+/g,'_'),label:text});}
    };
  }
  change(){this.render();this.onChange(this.items);}
  add(row){if(!this.items.some(value=>value.tag===row.tag)){this.items.push(row);this.change();}else{this.input.value='';this.suggestions.classList.add('hidden');}}
  async search(){
    const query=this.input.value.trim();if(!query){this.suggestions.classList.add('hidden');return;}
    const sequence=++this.sequence;
    try{const data=await api('/api/tags?'+new URLSearchParams({q:query,kind:this.kind,limit:16}));if(sequence!==this.sequence)return;
      this.matches=data.items.filter(row=>!this.items.some(item=>item.tag===row.tag));
      this.suggestions.innerHTML=this.matches.map(row=>`<button class="suggestion"><span><strong>${esc(row.label)}</strong><small>${esc(row.tag)}</small></span><b>${row.count||'新'}</b></button>`).join('')||'<div class="field-help" style="padding:8px">未找到匹配，英文标签可按 Enter 添加</div>';
      this.suggestions.classList.remove('hidden');this.suggestions.querySelectorAll('button').forEach((button,i)=>button.onclick=()=>this.add(this.matches[i]));
    }catch(error){toast(error.message);}
  }
}
function closeInspector(){state.detail=null;state.edit=null;state.dirty=false;$('#inspector').classList.remove('open');$('#inspector').innerHTML='';$('#content').querySelectorAll('.current').forEach(node=>node.classList.remove('current'));}
async function openInspector(sha){
  if(state.detail?.sha===sha)return;if(!await discardChanges())return;
  try{const detail=await api('/api/images/'+sha);state.detail=detail;state.edit={tags:detail.tags.map(row=>({...row})),characters:detail.characters.map(row=>({...row})),category:detail.category,note:detail.note,reviewed:true};state.dirty=false;
    renderInspector();$('#content').querySelectorAll('.photo-card').forEach(node=>node.classList.toggle('current',node.dataset.sha===sha));
  }catch(error){toast(error.message);}
}
function renderInspector(){
  const detail=state.detail,edit=state.edit;const title=edit.characters[0]?.label||'角色未识别';const sensitive=edit.category==='NSFW'&&!state.reveal;
  $('#inspector').innerHTML=`<div class="inspector-header"><span>图片详情</span><div class="tools"><button id="detail-star" class="button icon-button star-button ${detail.starred?'active':''}" title="收藏图片">${icon('star')}</button><button class="close-button" id="close-inspector" aria-label="关闭图片详情">×</button></div></div>
    <div class="inspector-scroll"><div class="detail-preview"><img class="${sensitive?'sensitive':''}" src="/media/${detail.sha}/thumb?size=1200" alt="${esc(title)}">${sensitive?'<div class="sensitive-cover">敏感图片，点击放大预览查看</div>':''}<button class="zoom-button" id="zoom-image" title="放大预览">${icon('expand')}</button></div>
    <div class="detail-main"><div class="detail-title">${esc(title)}</div><div class="detail-properties"><span>${detail.width} × ${detail.height}</span><span>${date(detail.created)}</span><a href="/media/${detail.sha}/download" title="下载原图">${icon('download')}</a></div>
    <label class="field-label">角色分类<span class="light">首个角色决定保存目录</span></label><div id="characters-picker"></div><p class="field-help">支持中文名与原始角色 tag，点击 ↑ 调整主要角色。</p>
    <label class="field-label">画面标签<span class="light">${edit.tags.length} 个标签</span></label><div id="tags-picker"></div>
    <label class="field-label">图片分级</label><div class="detail-category"><button data-category="SFW" class="${edit.category==='SFW'?'active':''}">SFW · 普通图片</button><button data-category="NSFW" class="nsfw ${edit.category==='NSFW'?'active':''}">NSFW · 敏感图片</button></div>
    <label class="field-label" for="detail-note">备注</label><textarea class="note-input" id="detail-note" placeholder="留下一点关于这张图的记录…">${esc(edit.note)}</textarea>
    <label class="review-toggle"><input type="checkbox" id="detail-reviewed" ${edit.reviewed?'checked':''}>保存后标记为已整理</label>
    <div class="detail-divider"></div><span class="detail-code">${esc(detail.files[0]||'')}</span><p class="field-help">${esc(detail.model)} · 保存更改后同步图库索引</p></div></div>
    <div class="detail-actions"><button class="button subtle danger-text" id="delete-image">${icon('trash')}移出图库</button><button class="button primary" id="save-image">保存更改 ${icon('check')}</button></div>`;
  $('#inspector').classList.add('open');
  new TagPicker($('#characters-picker'),edit.characters,'character',items=>{state.edit.characters=items;state.dirty=true;});
  new TagPicker($('#tags-picker'),edit.tags,'general',items=>{state.edit.tags=items;state.dirty=true;});
  $('#close-inspector').onclick=async()=>{if(await discardChanges())closeInspector();};
  $('#detail-note').oninput=event=>{edit.note=event.target.value;state.dirty=true;};
  $('#detail-reviewed').onchange=event=>{edit.reviewed=event.target.checked;state.dirty=true;};
  document.querySelectorAll('[data-category]').forEach(button=>button.onclick=()=>{edit.category=button.dataset.category;state.dirty=true;document.querySelectorAll('[data-category]').forEach(node=>node.classList.toggle('active',node===button));});
  $('#detail-star').onclick=async()=>{try{await api('/api/images/'+detail.sha+'/flags',{starred:!detail.starred});detail.starred=!detail.starred;$('#detail-star').classList.toggle('active',detail.starred);refreshStats();}catch(error){toast(error.message);}};
  $('#zoom-image').onclick=()=>showLightbox(detail.sha,title);
  $('#delete-image').onclick=()=>deleteImage(detail);
  $('#save-image').onclick=saveImage;
}
async function saveImage(){
  const button=$('#save-image');button.disabled=true;button.innerHTML='<span class="spinner"></span>正在保存';
  try{
    const detail=await api('/api/images/'+state.detail.sha+'/edit',{version:state.detail.version,tags:state.edit.tags.map(row=>row.tag),characters:state.edit.characters.map(row=>row.tag),category:state.edit.category});
    await api('/api/images/'+detail.sha+'/flags',{reviewed:state.edit.reviewed,note:state.edit.note});detail.note=state.edit.note;detail.reviewed=state.edit.reviewed;
    state.detail=detail;state.edit={tags:detail.tags,characters:detail.characters,category:detail.category,note:detail.note,reviewed:detail.reviewed};state.dirty=false;
    renderInspector();toast('已保存，角色与标签已同步到图库');loadContent(true);refreshStats();shortcuts();
  }catch(error){toast(error.message);button.disabled=false;button.innerHTML='保存更改 '+icon('check');}
}
async function deleteImage(detail){
  if(!await confirmAction('移出这张图片？','它会从图库及浏览结果中移除，并存入回收站，之后仍可恢复。','移入回收站',true))return;
  try{const result=await api('/api/images/'+detail.sha+'/delete',{});closeInspector();loadContent(true);refreshStats();toast('已移入回收站',{text:'撤销',run:()=>restoreTrash(result.trashId)});}catch(error){toast(error.message);}
}
async function restoreTrash(id){try{await api('/api/trash/'+id+'/restore',{});toast('图片已恢复到图库');refreshStats();loadContent(true);}catch(error){toast(error.message);}}
function showLightbox(sha,title,trash){
  const rows=state.items.slice();let index=rows.findIndex(row=>row.sha===sha);
  if(index<0){rows.push({sha,title,trashId:trash});index=rows.length-1;}
  state.lightbox={rows,index,firstPage:state.page,lastPage:state.page,pages:state.pages,busy:false,trash:!!trash,
    query:{view:state.view,q:state.q,tags:state.tags.map(row=>row.tag).join(','),rating:state.rating,sort:state.sort,limit:60}};
  if(!$('#lightbox').open)$('#lightbox').showModal();renderLightbox();
}
function renderLightbox(){
  const box=state.lightbox;if(!box)return;const row=box.rows[box.index];
  $('#lightbox-image').src='/media/'+row.sha+'/original'+(box.trash?'?trash='+encodeURIComponent(row.trashId):'');
  $('#lightbox-caption').textContent=(row.title||'图片')+' · '+(box.trash?box.index+1:(box.firstPage-1)*60+box.index+1);
  $('#lightbox-prev').disabled=box.busy||(box.index===0&&(box.trash||box.firstPage<=1));
  $('#lightbox-next').disabled=box.busy||(box.index===box.rows.length-1&&(box.trash||box.lastPage>=box.pages));
  $('#lightbox-strip').innerHTML=box.rows.map((item,i)=>`<button class="${i===box.index?'active':''}" aria-label="预览 ${esc(item.title)}" aria-current="${i===box.index}" data-preview="${i}"><img loading="lazy" class="${item.category==='NSFW'&&!state.reveal?'sensitive':''}" src="/media/${item.sha}/thumb?size=160${box.trash?'&trash='+encodeURIComponent(item.trashId):''}" alt="${esc(item.title)}"></button>`).join('');
  $('#lightbox-strip').querySelectorAll('button').forEach(button=>button.onclick=()=>{box.index=Number(button.dataset.preview);renderLightbox();});
  $('#lightbox-strip .active')?.scrollIntoView({block:'nearest',inline:'center'});
}
async function stepLightbox(delta){
  const box=state.lightbox;if(!box||box.busy)return;
  const next=box.index+delta;
  if(next>=0&&next<box.rows.length){box.index=next;renderLightbox();return;}
  const page=delta<0?box.firstPage-1:box.lastPage+1;
  if(box.trash||page<1||page>box.pages)return;
  box.busy=true;$('#lightbox-prev').disabled=$('#lightbox-next').disabled=true;
  try{
    const data=await api('/api/images?'+new URLSearchParams({...box.query,page}));
    if(state.lightbox!==box)return;
    if(!data.items.length){toast('该页图片已变化，请刷新图库');return;}
    if(delta<0){box.rows.unshift(...data.items);box.index=data.items.length-1;box.firstPage=page;}
    else{box.index=box.rows.length;box.rows.push(...data.items);box.lastPage=page;}
  }catch(error){toast(error.message);}finally{box.busy=false;if(state.lightbox===box)renderLightbox();}
}
$('#lightbox-prev').onclick=()=>stepLightbox(-1);$('#lightbox-next').onclick=()=>stepLightbox(1);
$('#lightbox').addEventListener('close',()=>{state.lightbox=null;$('#lightbox-image').removeAttribute('src');$('#lightbox-strip').innerHTML='';});
$('#lightbox-strip').addEventListener('wheel',event=>{if(Math.abs(event.deltaY)>Math.abs(event.deltaX)){event.preventDefault();$('#lightbox-strip').scrollLeft+=event.deltaY;}},{passive:false});
function showTrashPreview(row){showLightbox(row.sha,row.title,row.trashId);}
let filterPicker;
$('#open-filter').onclick=()=>{filterPicker=new TagPicker($('#filter-picker'),state.tags,'any',()=>{});$('#filter-dialog').showModal();setTimeout(()=>filterPicker.input.focus(),100);};
$('#apply-filter').onclick=()=>{state.tags=filterPicker.items;state.page=1;$('#filter-dialog').close();loadContent();};
$('#rating-filter').querySelectorAll('button').forEach(button=>button.onclick=()=>{state.rating=button.dataset.rating;state.page=1;$('#rating-filter').querySelectorAll('button').forEach(node=>node.classList.toggle('active',node===button));loadContent();});
let searchTimer;
$('#search').oninput=event=>{state.q=event.target.value;state.page=1;clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadContent(),300);};
$('#sort').onchange=event=>{state.sort=event.target.value;state.page=1;loadContent();};
$('#refresh-library').onclick=()=>{refreshStats();loadContent();shortcuts();};
$('#toggle-density').onclick=()=>{state.dense=!state.dense;localStorage.setItem('dense',state.dense?'1':'0');$('#content').classList.toggle('dense',state.dense);};
$('#all-characters').onclick=()=>navigate('characters');
$('#reveal-nsfw').checked=state.reveal;$('#reveal-nsfw').onchange=event=>{state.reveal=event.target.checked;localStorage.setItem('reveal',state.reveal?'1':'0');loadContent(true);if(state.detail)renderInspector();};
$('#clear-selection').onclick=()=>{state.selected.clear();state.selectMode=false;renderSelection();};
$('#toggle-selection').onclick=()=>{state.selectMode=!state.selectMode;renderSelection();};
$('#select-page').onclick=()=>{for(const row of state.items)state.selected.add(row.sha);renderSelection();};
document.querySelectorAll('[data-batch]').forEach(button=>button.onclick=async()=>{
  const action=button.dataset.batch;
  if(action==='delete'&&!await confirmAction(`移出选中的 ${state.selected.size} 张图片？`,'这些图片会进入回收站，可以恢复。','移入回收站',true))return;
  if(!state.selected.size)return;
  const ids=[...state.selected];document.querySelectorAll('[data-batch]').forEach(node=>node.disabled=true);
  try{let failed=0;
    for(let i=0;i<ids.length;i+=120){
      const result=await api('/api/batch',{ids:ids.slice(i,i+120),action:action==='delete'?'delete':'flags',flags:action==='review'?{reviewed:true}:{starred:true}});
      for(const row of result.items){if(row.ok)state.selected.delete(row.sha);else failed++;}
    }
    toast(failed?`${failed} 张未完成，已保留选择，可重试`:'批量操作已完成');
  }catch(error){toast(error.message);}finally{
    document.querySelectorAll('[data-batch]').forEach(node=>node.disabled=false);renderSelection();if(action==='delete')closeInspector();loadContent(true);refreshStats();
  }
});

// Local import controls: browser files or a directly read local directory.
$('#open-import').onclick=()=>$('#import-dialog').showModal();
document.querySelectorAll('[data-import-tab]').forEach(button=>button.onclick=()=>{
  state.importTab=button.dataset.importTab;document.querySelectorAll('[data-import-tab]').forEach(node=>node.classList.toggle('active',node===button));
  $('#import-files-tab').classList.toggle('hidden',state.importTab!=='files');$('#import-folder-tab').classList.toggle('hidden',state.importTab!=='folder');
  $('#import-status').textContent=state.importTab==='folder'?'来源文件不会被移动':state.files.length?`已选择 ${state.files.length} 张图片`:'尚未选择图片';
});
function chooseFiles(files){state.files=[...files].filter(file=>/\.(jpg|jpeg|png|webp|bmp|gif)$/i.test(file.name));$('#chosen-files').textContent=state.files.length?`已选择 ${number(state.files.length)} 张：${state.files.slice(0,3).map(file=>file.name).join('、')}${state.files.length>3?'…':''}`:'所选内容中没有支持的图片';$('#import-status').textContent=`已选择 ${number(state.files.length)} 张图片`;}
$('#choose-files').onclick=event=>{event.stopPropagation();$('#file-input').click();};
$('#choose-directory').onclick=event=>{event.stopPropagation();$('#directory-input').click();};
$('#file-input').onchange=event=>chooseFiles(event.target.files);$('#directory-input').onchange=event=>chooseFiles(event.target.files);
$('#drop-zone').onclick=event=>{if(!event.target.closest('button'))$('#file-input').click();};
// Capture entries during the drop event, then recursively read every directory batch.
const importPaths=new WeakMap();
async function readDropEntries(entries){
  const files=[];
  async function walk(entry,prefix=''){
    if(entry.isFile){const file=await new Promise((resolve,reject)=>entry.file(resolve,reject));importPaths.set(file,prefix+file.name);files.push(file);if(files.length>20000)throw new Error('一次最多导入20000个文件，请分批拖入');}
    else if(entry.isDirectory){const reader=entry.createReader();while(true){const batch=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));if(!batch.length)break;for(const child of batch)await walk(child,prefix+entry.name+'/');}}
  }
  for(const entry of entries)await walk(entry);return files;
}
function isFileDrag(event){return Array.from(event.dataTransfer?.types||[]).includes('Files');}
document.addEventListener('dragover',event=>{if(isFileDrag(event)){event.preventDefault();event.dataTransfer.dropEffect='copy';$('#drop-zone').classList.add('drag-over');}});
document.addEventListener('dragleave',event=>{if(!event.relatedTarget)$('#drop-zone').classList.remove('drag-over');});
document.addEventListener('drop',async event=>{
  if(!isFileDrag(event))return;event.preventDefault();$('#drop-zone').classList.remove('drag-over');
  const sequence=++state.dropSequence;
  const entries=Array.from(event.dataTransfer.items||[]).map(item=>item.webkitGetAsEntry?.()).filter(Boolean);
  const fallback=Array.from(event.dataTransfer.files);
  if(state.uploading){toast('正在提交导入任务，请稍候再拖入');return;}
  if($('#open-import').disabled){toast('当前为浏览模式，需先安装分类环境');return;}
  if(!$('#import-dialog').open)$('#import-dialog').showModal();
  document.querySelector('[data-import-tab="files"]').click();
  $('#start-import').disabled=true;$('#import-status').textContent='正在读取拖入的文件夹…';
  try{const files=entries.length?await readDropEntries(entries):fallback;if(sequence===state.dropSequence)chooseFiles(files);}
  catch(error){if(sequence===state.dropSequence){chooseFiles([]);toast(error.message);}}
  finally{if(sequence===state.dropSequence)$('#start-import').disabled=false;}
});
$('#start-import').onclick=async()=>{
  const button=$('#start-import');button.disabled=true;state.uploading=true;const files=state.files.slice();
  try{
    let result;
    if(state.importTab==='folder'){
      const value=$('#folder-path').value.trim();if(!value)throw new Error('请输入图片来源文件夹');result=await api('/api/import/folder',{path:value});
    }else{
      if(!files.length)throw new Error('请先选择图片');if(files.length>20000)throw new Error('一次最多导入20000张图片');for(const file of files)if(file.size>25*1024**2)throw new Error(file.name+' 超过25MiB');const uploaded=[];
      for(let i=0;i<files.length;i++){
        const file=files[i];$('#import-status').textContent=`正在准备 ${i+1} / ${files.length}`;
        if(file.size>25*1024**2)throw new Error(file.name+' 超过25MiB，请移除后重试');
        const response=await fetch('/api/upload?name='+encodeURIComponent(importPaths.get(file)||file.webkitRelativePath||file.name),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':state.csrf},body:file});
        const value=await response.json();if(!response.ok)throw new Error(value.error);uploaded.push(value);
      }
      result=await api('/api/import/files',{files:uploaded});
    }
    $('#import-dialog').close();toast(`${number(result.total)} 张图片已加入本地导入队列`);state.files=[];$('#chosen-files').textContent='';$('#import-status').textContent='尚未选择图片';navigate('jobs');
  }catch(error){toast(error.message);}finally{button.disabled=false;state.uploading=false;}
};
const jobStatus={queued:'等待中',running:'分类中',done:'已完成',cancelled:'已取消',error:'遇到问题',interrupted:'已中断'};
function reason(value){
  const map={exact_duplicate:'完全重复',near_duplicate:'高度相似',low_resolution:'分辨率不足',uncertain_rating:'分级不确定',uncertain_nsfw_evidence:'敏感内容证据不确定',uncertain_artwork_type:'画面类型不确定',flat_or_strongly_blurred_image:'过于模糊或缺少细节',invalid_or_unsafe_image:'图片无法安全读取',file_too_large:'图片过大',extreme_aspect_ratio:'画面比例不合适',animated_image:'动画图片',no_reliable_person:'未识别到可靠人物',accepted_training_candidate:'已收录',accepted_unknown_character_candidate:'已收录，角色待补充',gpu_out_of_memory:'显卡内存不足',insufficient_disk_space:'磁盘空间不足'};
  if(map[value])return map[value];if(value?.startsWith('unsuitable_image_type:'))return '画面类型不适合训练';if(value?.startsWith('filtered_tag:'))return '命中筛除标签';if(value?.startsWith('screenshot_tag:'))return '截图图片';return value||'等待处理';
}
function renderJobs(){
  $('#page-count').textContent=state.jobs.length;$('#result-caption').textContent='按任务查看导入进度 · 低质量及重复图片自动跳过';$('#content').className='jobs-list';$('#pagination').innerHTML='';
  $('#content').innerHTML=state.jobs.map(job=>{
    const progress=Math.min(100,Math.round(job.processed/Math.max(1,job.total)*100));return `<article class="job-card" data-job="${job.id}"><div class="job-top"><div><div class="job-title">${number(job.total)} 张图片 · ${date(job.created)}</div><div class="job-source">${esc(job.source)}</div></div><span class="job-badge ${job.status}">${jobStatus[job.status]||job.status}</span></div><div class="job-progress ${job.status}"><span style="width:${progress}%"></span></div><div class="job-phase">${esc(job.phase)}${job.error?' · '+esc(reason(job.error)):''}</div><div class="job-counts"><span>进度<b>${job.processed}/${job.total}</b></span><span>收录<b>${job.saved}</b></span><span>筛除<b>${job.rejected}</b></span><span>去重<b>${job.duplicate}</b></span><span>异常<b>${job.errors}</b></span></div><button class="job-detail-toggle" data-job-details="${job.id}">查看处理明细 ↓</button>${['queued','running'].includes(job.status)?`<button class="button subtle danger-text" style="float:right" data-job-cancel="${job.id}">取消任务</button>`:''}<div class="job-items hidden" id="job-items-${job.id}"></div></article>`;
  }).join('')||empty('还没有导入任务','选择一个本地文件夹，开始扩充你的图库。','upload');
  document.querySelectorAll('[data-job-cancel]').forEach(button=>button.onclick=async()=>{await api('/api/jobs/'+button.dataset.jobCancel+'/cancel',{});toast('正在完成当前图片，随后停止');loadContent(true);});
  document.querySelectorAll('[data-job-details]').forEach(button=>button.onclick=async()=>{
    const pane=$('#job-items-'+button.dataset.jobDetails);if(!pane.classList.contains('hidden')){pane.classList.add('hidden');return;}
    try{const job=await api('/api/jobs/'+button.dataset.jobDetails);pane.innerHTML=job.items.map(item=>`<div class="job-item"><span title="${esc(item.name)}">${esc(item.name)}</span><span>${esc(reason(item.reason))}</span></div>`).join('')||'<span class="muted">等待处理第一张图片</span>';pane.classList.remove('hidden');}catch(error){toast(error.message);}
  });
}
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>$('#'+button.dataset.close).close());
document.addEventListener('click',event=>{if(!event.target.closest('.tagbox'))document.querySelectorAll('.tag-suggestions').forEach(node=>node.classList.add('hidden'));});
document.addEventListener('keydown',async event=>{
  if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName))return;
  if($('#lightbox').open&&(event.key==='ArrowLeft'||event.key==='ArrowRight')){event.preventDefault();stepLightbox(event.key==='ArrowRight'?1:-1);return;}
  if(event.key==='/'&&!document.querySelector('dialog[open]')){event.preventDefault();$('#search').focus();}
  if(event.key==='Escape'&&state.detail&&!document.querySelector('dialog[open]')){if(await discardChanges())closeInspector();}
  if((event.key==='ArrowLeft'||event.key==='ArrowRight')&&state.detail&&!document.querySelector('dialog[open]')){
    const index=state.items.findIndex(item=>item.sha===state.detail.sha),next=state.items[index+(event.key==='ArrowRight'?1:-1)];if(next)openInspector(next.sha);
  }
});
async function initialize(){
  try{const bootstrap=await api('/api/bootstrap');state.csrf=bootstrap.csrf;state.stats=bootstrap.stats;$('#root-label').textContent=bootstrap.root;
    if(bootstrap.classificationEnabled===false){$('#open-import').disabled=true;$('#open-import').title='当前为浏览模式，重新运行安装器可配置本地分类环境';$('#footer-summary').textContent='浏览模式 · 本地分类环境尚未安装';}
    renderNavigation();loadContent();shortcuts();
    setInterval(async()=>{await refreshStats();if(state.view==='jobs')loadContent(true);},5000);
  }catch(error){$('#content').innerHTML=`<div class="load-error">无法连接本地图库：${esc(error.message)}<br>请检查服务是否正在运行，然后刷新页面。</div>`;}
}
initialize();
