'use strict';
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const TYPES = {incident:'事件', person:'人員', supply:'物資', facility:'設施', custom:'自訂物件'};
const RELATIONS = {assignment:'指派', supplies:'供應', care:'照護', request:'提出需求', related:'相關', custom:'自訂關係'};
const COLORS = {incident:'#c34736', person:'#237caf', supply:'#c57320', facility:'#8b5fa8', custom:'#087f72'};
// Inline SVG paths (from lucide) rather than data-lucide + lucide.createIcons(): map markers must
// render correctly even if the unpkg.com CDN script is blocked (ad blocker/firewall) or slow to load.
const ICON_PATHS = {
  incident: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path>',
  person: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle>',
  supply: '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"></path><path d="M12 22V12"></path><path d="m3.3 7 7.703 4.734a2 2 0 0 0 1.994 0L20.7 7"></path><path d="m7.5 4.27 9 5.15"></path>',
  facility: '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"></path><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"></path><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"></path><path d="M10 6h4"></path><path d="M10 10h4"></path><path d="M10 14h4"></path><path d="M10 18h4"></path>',
  custom: '<path d="M8.3 10a.7.7 0 0 1-.626-1.079L11.4 3a.7.7 0 0 1 1.198-.043L16.3 8.9a.7.7 0 0 1-.572 1.1Z"></path><rect x="3" y="14" width="7" height="7" rx="1"></rect><circle cx="17.5" cy="17.5" r="3.5"></circle>',
};
function iconSvg(kind) { return svgIcon(ICON_PATHS[kind]); }
function svgIcon(paths) { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">${paths||''}</svg>`; }
// 人員依角色區分：志工與居民在地圖上一眼要分得出來，不必點開看「角色」。
// 同時是居民與志工的人算志工（他是能出門送物資的人）。
const ROLE_LOOK = {
  volunteer: {label:'志工', color:'#2f855a', shape:'round-rectangle',
              icon:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><polyline points="16 11 18 13 22 9"></polyline>'},
  resident:  {label:'居民', color:'#237caf', shape:'ellipse', icon:ICON_PATHS.person},
  family:    {label:'家屬', color:'#64748b', shape:'ellipse', icon:ICON_PATHS.person},
};
function personRole(n) {
  if (n.kind !== 'person') return null;
  const roles = n.properties?.roles || [];
  if (roles.includes('volunteer') || roles.includes('field_staff')) return 'volunteer';
  if (roles.includes('elderly')) return 'resident';
  if (roles.includes('family')) return 'family';
  return null;
}
function nodeColor(n) { return ROLE_LOOK[personRole(n)]?.color || COLORS[n.kind]; }
function nodeIcon(n) { const look = ROLE_LOOK[personRole(n)]; return look ? svgIcon(look.icon) : iconSvg(n.kind); }
function nodeTypeLabel(n) { return ROLE_LOOK[personRole(n)]?.label || TYPES[n.kind]; }
const STATUS = {active:'啟用', inactive:'停用'};
const LIVE_WORKSPACE_ID='__live__';
const state = {id:null, live:false, zone:'', revision:0, graph:{nodes:[],edges:[]}, selected:null, view:'map', dirty:false,
  undo:[], redo:[], hidden:new Set(), report:null, connect:null, preview:null, editVersion:0, documentVersion:0, importVersion:0};
let map, mapLayers, markerCluster, cy;
let oneShotAdd = false;
let principalRoles = ['admin']; // 保守預設：拿到真正的角色前先當作管理員，載入完成後 init() 會校正。
function isAdmin() { return principalRoles.includes('admin'); }
function rememberWorkspace(id){try{if(id)localStorage.setItem('emergency:last-workspace',id);else localStorage.removeItem('emergency:last-workspace');}catch(e){}}
function lastWorkspace(){try{return localStorage.getItem('emergency:last-workspace');}catch(e){return null;}}
function workspaceSelection(){return state.live?LIVE_WORKSPACE_ID:(state.id||'');}
function icons() { if (window.lucide) lucide.createIcons(); }
function message(text, error=false) { $('status').textContent=text; $('status').classList.toggle('error',error); }
// Promise-based confirm dialog — replaces confirm() so the prompt can show which
// volunteer/resource an action actually affects, not just a generic yes/no question.
function askConfirm(title, bodyHtml, okLabel='確認') {
  return new Promise(resolve => {
    $('confirm-dialog-title').textContent = title;
    $('confirm-dialog-body').innerHTML = bodyHtml;
    $('confirm-dialog-ok').textContent = okLabel;
    const dialog = $('confirm-dialog');
    const cleanup = result => { dialog.close(); okBtn.onclick = null; cancelBtn.onclick = null; resolve(result); };
    const okBtn = $('confirm-dialog-ok'), cancelBtn = $('confirm-dialog-cancel');
    okBtn.onclick = () => cleanup(true);
    cancelBtn.onclick = () => cleanup(false);
    dialog.addEventListener('cancel', () => resolve(false), {once:true});
    dialog.showModal();
    ($('confirm-dialog-body').querySelector('input, textarea') || okBtn).focus();
  });
}
async function api(path, body, method='POST') {
  const response = await fetch('/api/workspaces'+path, body === undefined ? {} : {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : '資料欄位無效，請檢查座標、數量與連線');
  return data;
}
function run(id, fn) { $(id).addEventListener('click', async () => {
  const button=$(id); button.disabled=true;
  try { await fn(); } catch(e) { message(e.message,true); } finally { button.disabled=false; updateHistory(); }
}); }
function changed() { state.dirty=true; state.editVersion++; state.report=null; if(!state.baseline&&state.graph.nodes.length)state.baseline=currentBaseline(); invalidateAllocation(); $('save-state').textContent='有未儲存變更'; $('analysis-result').textContent='分析尚未更新'; }
// Baselines are immutable snapshots and can be shared by history entries.
function historySnapshot(){return {graph:structuredClone(state.graph),baseline:state.baseline};}
function checkpoint() { state.undo.push(historySnapshot()); if(state.undo.length>25)state.undo.shift(); state.redo=[]; }
function mutate(fn) { checkpoint(); fn(); changed(); render(); }
function updateHistory() { $('undo').disabled=!state.undo.length; $('redo').disabled=!state.redo.length;
  // 即時現況還沒動過時，復原／重做／刪除工作區都沒有意義，先收起來，版面留給現況
  document.body.classList.toggle('live-clean',!!state.live&&!state.dirty); }
function undo(redo=false) { const from=redo?state.redo:state.undo, to=redo?state.undo:state.redo; if(!from.length)return;
  to.push(historySnapshot()); const previous=from.pop();state.graph=previous.graph;state.baseline=previous.baseline;state.selected=null; changed(); render(); }
async function discardConfirmed() { return (!state.dirty && !inventoryDrafts.size) || await askConfirm('離開這個工作區？','目前有未儲存或待寫回的變更，離開後會遺失。','離開'); }
function loadDocument(data) {
  inventoryDrafts.clear();updateInventoryCount();
  operationEvents.clear();
  state.documentVersion++;
  if(cy){cy.destroy();cy=null;}
  state.id=data.id; state.live=false; state.revision=data.revision; state.graph=data.graph;
  state.zone=data.zone_id&&data.zone_id!=='general'?data.zone_id:''; if($('zone-select'))$('zone-select').value=state.zone; state.selected=null; state.report=null;
  state.undo=[];state.redo=[];state.dirty=false;state.editVersion++;state.connect=null;
  $('workspace-name').value=data.name; $('workspace-folder').value=data.folder||''; $('workspace-list').value=data.id||'';
  state.baseline=structuredClone(data.baseline||null)||(state.graph.nodes.length?currentBaseline():null);invalidateAllocation();
  $('save-state').textContent=data.id?`已儲存 · 版本 ${data.revision}`:'尚未儲存';
  $('analysis-result').textContent='';render();fit();
  workspaceUrl(data.id);
  rememberWorkspace(data.id);
}
function groupByFolder(items) {
  const groups=new Map();
  items.forEach(w=>{const key=w.folder||''; if(!groups.has(key))groups.set(key,[]); groups.get(key).push(w);});
  const unfiled=groups.get('')||[]; groups.delete('');
  const folders=[...groups.keys()].sort((a,b)=>a.localeCompare(b,'zh-Hant'));
  return {folders, groups, unfiled};
}
async function listWorkspaces() {
  const items=await api('');
  const {folders, groups, unfiled}=groupByFolder(items);
  const optionsFor=list=>list.map(w=>`<option value="${escapeHtml(w.id)}">${escapeHtml(w.name)}</option>`).join('');
  const groupedHtml=folders.map(name=>`<optgroup label="${escapeHtml(name)}">${optionsFor(groups.get(name))}</optgroup>`).join('');
  const unfiledHtml=unfiled.length?(folders.length?`<optgroup label="未分類">${optionsFor(unfiled)}</optgroup>`:optionsFor(unfiled)):'';
  $('workspace-list').innerHTML='<option value="__live__">即時營運現況（正式資料）</option><option value="">未儲存工作區</option>'+groupedHtml+unfiledHtml;
  $('workspace-list').value=workspaceSelection();
  $('workspace-folder-options').innerHTML=folders.map(name=>`<option value="${escapeHtml(name)}">`).join('');
  return items;
}
async function loadLiveWorkspace() {
  inventoryDrafts.clear();updateInventoryCount();operationEvents.clear();
  state.documentVersion++;
  if(cy){cy.destroy();cy=null;}
  state.id=null;state.live=true;state.revision=0;state.graph={nodes:[],edges:[]};state.selected=null;state.report=null;
  state.undo=[];state.redo=[];state.dirty=false;state.editVersion++;state.connect=null;state.baseline=null;
  $('workspace-name').value='即時營運現況';$('workspace-folder').value='正式資料';$('workspace-list').value=LIVE_WORKSPACE_ID;
  $('save-state').textContent='即時資料 · 自動更新';$('analysis-result').textContent='';
  invalidateAllocation();workspaceUrl(null);rememberWorkspace(LIVE_WORKSPACE_ID);render();
  await refreshOperations();
  const hasSos=operationalNodes().some(n=>n.properties.status==='open'&&n.properties.need_type==='sos');
  operationStage=hasSos?'sos':'open';setCatalog('tasks');renderOperations();
  state.undo=[];state.redo=[];state.dirty=false;state.baseline=null;
  $('save-state').textContent='即時資料 · 自動更新';updateHistory();
  if(state.graph.nodes.length)fit();
}
async function save(copy=false) {
  const version=state.editVersion, documentVersion=state.documentVersion, graph=structuredClone(state.graph), name=$('workspace-name').value.trim()||'未命名工作區';
  const folder=$('workspace-folder').value.trim();
  const id=copy?null:state.id;
  const data=await api(id?'/'+id:'',{name,graph,baseline:state.baseline||null,revision:state.revision,folder,zone_id:state.zone||'general'},id?'PUT':'POST');
  if(documentVersion!==state.documentVersion){await listWorkspaces();message('先前工作區已儲存');return;}
  state.id=data.id;state.live=false;state.revision=data.revision;
  workspaceUrl(data.id);
  rememberWorkspace(data.id);
  if(version===state.editVersion){state.dirty=false;$('save-state').textContent=`已儲存 · 版本 ${data.revision}`;}
  await listWorkspaces();message(copy?'已另存副本':'工作區已儲存');
}
async function deleteWorkspace() {
  if(state.live||!state.id){message('請先從左上選單切到要刪除的已儲存工作區',true);return;}
  const name=$('workspace-name').value||'未命名工作區';
  if(!await askConfirm('刪除工作區？',`將刪除「<strong>${escapeHtml(name)}</strong>」的圖資料與快照。<br>平台上的需求、物資與人員不受影響；刪除後無法復原。`,'刪除'))return;
  const response=await fetch('/api/workspaces/'+encodeURIComponent(state.id),{method:'DELETE'});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw Error(data.error||data.detail||'刪除失敗');
  state.dirty=false;inventoryDrafts.clear();
  await loadLiveWorkspace();await listWorkspaces();message(data.message);
}
// 選擇地區：搜尋地點 → 選範圍 → 載入周邊公開設施，地圖移過去，未命名的工作區順便以地區命名。
let regionPick=null,regionTimer=null,regionSeq=0;
const TW_PLACES=[
  ['臺南市永康區',23.0264,120.2573,'鄉鎮'],['花蓮縣光復鄉',23.669,121.423,'鄉鎮'],['花蓮縣瑞穗鄉',23.4972,121.376,'鄉鎮'],['花蓮縣富里鄉',23.1797,121.2482,'鄉鎮'],
  ['臺東縣臺東市',22.7563,121.144,'鄉鎮'],['臺東縣成功鎮',23.0998,121.3765,'鄉鎮'],['臺東縣長濱鄉',23.3155,121.4513,'鄉鎮'],['臺東縣關山鎮',23.0474,121.1631,'鄉鎮'],['臺東縣池上鄉',23.1225,121.2195,'鄉鎮'],
  ['臺北市',25.0375,121.5637,'縣市'],['新北市',25.012,121.4658,'縣市'],['基隆市',25.1276,121.7392,'縣市'],['桃園市',24.9936,121.301,'縣市'],['新竹市',24.8138,120.9675,'縣市'],['新竹縣',24.8387,121.0177,'縣市'],
  ['苗栗縣',24.5602,120.8214,'縣市'],['臺中市',24.1477,120.6736,'縣市'],['彰化縣',24.0518,120.5161,'縣市'],['南投縣',23.9609,120.9719,'縣市'],['雲林縣',23.7092,120.4313,'縣市'],['嘉義市',23.4801,120.4491,'縣市'],
  ['嘉義縣',23.4518,120.2555,'縣市'],['臺南市',22.9999,120.227,'縣市'],['高雄市',22.6273,120.3014,'縣市'],['屏東縣',22.5519,120.5487,'縣市'],['宜蘭縣',24.7021,121.7378,'縣市'],['花蓮縣',23.9872,121.6015,'縣市'],
  ['臺東縣',22.7583,121.1444,'縣市'],['澎湖縣',23.5711,119.5793,'縣市'],['金門縣',24.4493,118.3767,'縣市'],['連江縣',26.1602,119.9517,'縣市'],
].map(([name,lat,lng,tag])=>({name,lat,lng,tag}));
const sameTai=s=>String(s).replace(/台/g,'臺');
// 候選地區：分區、需求所在的鄉鎮市區、事件，不用打字就能選。
async function regionCandidates(){
  let nodes=state.graph.nodes;
  if(!nodes.some(n=>n.properties.db==='need')){try{nodes=nodes.concat((await api('/operational-data')).graph.nodes);}catch(e){}}
  const out=zoneList.filter(z=>z.center_lat!=null).map(z=>({name:z.name,lat:z.center_lat,lng:z.center_lng,tag:'分區'}));
  const towns=new Map();
  for(const n of nodes){
    if(n.properties.db!=='need'||n.lat==null)continue;
    const m=sameTai(n.properties.address||'').match(/([一-鿿]{2}[縣市])([一-鿿]{1,3}?[鄉鎮市區])?/);
    if(!m)continue;
    const key=m[0],t=towns.get(key)||{name:key,lat:0,lng:0,count:0};
    t.lat+=n.lat;t.lng+=n.lng;t.count++;towns.set(key,t);
  }
  [...towns.values()].sort((a,b)=>b.count-a.count).forEach(t=>out.push({name:t.name,lat:t.lat/t.count,lng:t.lng/t.count,tag:`${t.count} 筆需求`}));
  nodes.filter(n=>n.kind==='incident'&&n.lat!=null).forEach(n=>out.push({name:n.label,lat:n.lat,lng:n.lng,tag:'事件'}));
  const seen=new Set(out.map(p=>sameTai(p.name)));
  return out.concat(TW_PLACES.filter(p=>!seen.has(p.name)));
}
function showRegionChoices(places){
  $('region-results').innerHTML=places.length?places.map((p,i)=>`<button type="button" role="radio" aria-checked="false" data-i="${i}"><span>${escapeHtml(p.name)}</span>${p.tag?`<small>${escapeHtml(p.tag)}</small>`:''}</button>`).join(''):'<p class="muted">找不到</p>';
  $('region-results').querySelectorAll('button').forEach(b=>b.onclick=()=>{regionPick=places[+b.dataset.i];$('region-results').querySelectorAll('button').forEach(x=>{x.classList.toggle('selected',x===b);x.setAttribute('aria-checked',String(x===b));});$('region-load').disabled=false;});
}
async function openRegion(){
  regionPick=null;$('region-query').value='';$('region-load').disabled=true;
  $('region-results').textContent='';$('region-dialog').showModal();$('region-query').focus();
  showRegionChoices(await regionCandidates());
}
async function searchRegion(){
  const q=$('region-query').value.trim(),seq=++regionSeq;
  regionPick=null;$('region-load').disabled=true;
  const all=await regionCandidates();
  if(!q){showRegionChoices(all);return;}
  const local=all.filter(p=>sameTai(p.name).includes(sameTai(q)));
  showRegionChoices(local);
  if(q.length<2)return;
  const data=await api('/places?q='+encodeURIComponent(q)).catch(()=>({places:[]}));
  if(seq===regionSeq)showRegionChoices(local.concat((data.places||[]).map(p=>({...p,tag:'搜尋'}))));
}
async function loadRegion(){
  if(!regionPick)return;
  const radius=+$('region-radius').value;
  const data=await api('/area-facilities',{lat:regionPick.lat,lng:regionPick.lng,radius_m:radius});
  const existing=new Set(state.graph.nodes.map(n=>n.id));
  const fresh=data.nodes.filter(n=>!existing.has(n.id));
  const short=regionPick.name.split(',')[0].trim();
  mutate(()=>{state.graph.nodes.push(...fresh);});
  if($('workspace-name').value==='未命名工作區'||!$('workspace-name').value.trim())$('workspace-name').value=`${short} 周邊 ${radius>=1000?radius/1000+' 公里':radius+' 公尺'}`;
  $('region-dialog').close();
  if(state.view==='map')map.setView([regionPick.lat,regionPick.lng],radius<=500?16:radius<=1000?15:14);
  message(`${short}：加入 ${fresh.length} 個設施`);
}
// 選擇分區：只看這個分區的需求與物資（派遣本來就只在同分區內配對），地圖移到分區中心。
let zoneList=[];
async function loadZoneOptions(){
  try{zoneList=await fetch('/api/zones').then(r=>r.ok?r.json():[]);}catch(e){zoneList=[];}
  $('zone-select').innerHTML='<option value="">全部分區</option>'+zoneList.map(z=>`<option value="${escapeHtml(z.id)}">${escapeHtml(z.name)}</option>`).join('');
  $('zone-select').value=zoneList.some(z=>z.id===state.zone)?state.zone:'';
  // 只有預設分區時選單沒有意義，收起來。
  $('zone-select').closest('.zone-picker').hidden=zoneList.length<=1&&!state.zone;
}
async function selectZone(){
  state.zone=$('zone-select').value;
  const zone=zoneList.find(z=>z.id===state.zone);
  if(zone&&zone.center_lat!=null&&state.view==='map')map.setView([zone.center_lat,zone.center_lng],zone.radius_km<=3?14:zone.radius_km<=8?13:12);
  if(!state.live&&state.id){state.dirty=true;$('save-state').textContent='有未儲存變更';}
  await refreshOperations();
  message(zone?`分區：${zone.name}`:'全部分區');
}
function options(items, value) { return Object.entries(items).map(([k,v])=>`<option value="${escapeHtml(k)}" ${k===value?'selected':''}>${escapeHtml(v)}</option>`).join(''); }
function nodeOptions(value='', placeholder='選擇物件') { return `<option value="">${placeholder}</option>`+state.graph.nodes.map(n=>`<option value="${escapeHtml(n.id)}" ${n.id===value?'selected':''}>${escapeHtml(n.label)} (${nodeTypeLabel(n)})</option>`).join(''); }
function nodeById(id) { return state.graph.nodes.find(n=>n.id===id); }
function visible(n) { return !state.hidden.has(n.kind); }
function personLegend() {
  const tally = {};
  state.graph.nodes.forEach(n => { const r = personRole(n); if (r) tally[r] = (tally[r] || 0) + 1; });
  return `<div class="role-legend">${Object.entries(ROLE_LOOK).map(([r, look]) =>
    `<span><i class="swatch" style="background:${look.color}"></i>${look.label} ${tally[r] || 0}</span>`).join('')}</div>`;
}
function render() {
  document.body.classList.toggle('live-mode',!!state.live);
  const counts={};state.graph.nodes.forEach(n=>counts[n.kind]=(counts[n.kind]||0)+1);
  const liveNeeds=state.graph.nodes.filter(n=>n.properties.db==='need');
  const sosCount=liveNeeds.filter(n=>n.properties.status==='open'&&n.properties.need_type==='sos').length;
  const supplyCount=state.graph.nodes.filter(n=>n.properties.db==='resource').length;
  $('layers').innerHTML=Object.entries(TYPES).map(([kind,label])=>`<div class="layer-row"><label class="layer"><input type="checkbox" data-kind="${kind}" ${state.hidden.has(kind)?'':'checked'}><span class="swatch" style="background:${COLORS[kind]}"></span>${label}<small>${counts[kind]||0}</small></label><button class="layer-add" data-layer-add="${kind}" title="匯入${label}" aria-label="匯入${label}"><i data-lucide="upload"></i></button></div>${kind==='person'?personLegend():''}`).join('');
  $('layers').querySelectorAll('[data-layer-add]').forEach(button=>button.onclick=()=>openImport(button.dataset.layerAdd));
  $('layers').querySelectorAll('input').forEach(input=>input.onchange=()=>{input.checked?state.hidden.delete(input.dataset.kind):state.hidden.add(input.dataset.kind);render();});
  const m=state.report?.metrics||{};
  // 求救與待處理需求已經在上方狀態列，這裡不重複；關聯分析跑過才顯示「未連結物件」
  $('metrics').innerHTML=[...(state.live?[]:[['緊急求救',sosCount,sosCount>0]]),['物資回報',supplyCount,false],['人員',counts.person||0,false],['事件',counts.incident||0,false],['可用物資',state.graph.nodes.filter(nodeHasSupply).length,false],...(m.unlinked_objects!=null?[['未連結物件',m.unlinked_objects,m.unlinked_objects>0]]:[])].map(([label,value,alert])=>`<div class="metric ${alert?'alert':''}"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join('');
  renderObjects();renderSelection();renderCanvas();updateHistory();renderOperations();
  $('empty').hidden=state.graph.nodes.length>0;
  $('connect-state').hidden=$('mode').value!=='connect';
  $('connect-state').textContent=state.connect?`起點：${nodeById(state.connect)?.label||''} · 選擇終點`:'選擇連線起點';
  icons();
}
function renderObjects() {
  const query=$('search').value.toLowerCase();const nodes=state.graph.nodes.filter(n=>visible(n)&&(n.label.toLowerCase().includes(query)||n.id.toLowerCase().includes(query)));
  $('object-count').textContent=`${nodes.length} 筆`;
  $('objects').innerHTML=nodes.slice(0,150).map(n=>`<button data-node="${escapeHtml(n.id)}" class="${state.selected?.id===n.id?'selected':''}" title="${escapeHtml(n.label)}"><span class="swatch" style="background:${nodeColor(n)}"></span><span class="object-label">${escapeHtml(n.label)}</span>${n.lat===null?'<small>無座標</small>':''}</button>`).join('')+(nodes.length>150?'<div class="muted">顯示前 150 筆</div>':'');
  $('objects').querySelectorAll('[data-node]').forEach(btn=>btn.onclick=()=>{select('node',btn.dataset.node);const n=nodeById(btn.dataset.node);if(n.lat!==null)map.panTo([n.lat,n.lng]);if(cy){const el=cy.getElementById('node:'+n.id);cy.center(el);}});
  renderOperationTasks();renderOperationResources();renderRollcall();
}
function select(type,id) {
  if(type==='node'&&$('mode').value==='connect') {
    if(!state.connect){state.connect=id;render();return;}
    if(state.connect!==id){const a=state.connect;state.connect=null;const edge={id:crypto.randomUUID(),source:a,target:id,kind:'related',label:'新增關係',status:'active',directed:true,provenance:'手動建立',properties:{}};
      mutate(()=>{state.graph.edges.push(edge);state.selected={type:'edge',id:edge.id};});return;}
  }
  state.selected={type,id};renderObjects();renderSelection();renderCanvas();icons();
}
function setInspectorOpen(open) {
  document.body.classList.toggle('inspector-open', open);
  $('inspector-backdrop').hidden=!open;
}
function clearSelection() {
  state.selected=null;
  setInspectorOpen(false);
  renderObjects();renderSelection();renderCanvas();icons();
}
function renderSelection() {
  const selected=state.selected, el=$('selection');
  const item=selected?(selected.type==='node'?nodeById(selected.id):state.graph.edges.find(e=>e.id===selected.id)):null;
  setInspectorOpen(!!item);
  if(!item){el.innerHTML='尚未選取';return;}
  const isNode=selected.type==='node';
  if(item.id.startsWith('db:')){renderOperationalSelection(item,isNode);return;}
  el.innerHTML=`<form id="edit-form"><label>名稱<input id="edit-label" value="${escapeHtml(item.label)}" maxlength="300" required></label>
    <label>類型<select id="edit-kind">${options(isNode?TYPES:RELATIONS,item.kind)}</select></label>
    ${isNode?`<p class="muted">位置：${item.lat==null?'尚未定位':'已定位，直接拖曳地圖上的圖示即可調整'}</p><label>${item.kind==='facility'?'已確認可用容量':'數量'}<input id="edit-quantity" type="number" step="any" min="0" max="1000000000" value="${item.quantity}" required></label><label class="checkbox-label"><input id="edit-available" type="checkbox" ${item.available?'checked':''}>納入分析</label>`:
    `<label>起點<select id="edit-source" required>${nodeOptions(item.source)}</select></label><label>終點<select id="edit-target" required>${nodeOptions(item.target)}</select></label><label>狀態<select id="edit-status">${options(STATUS,item.status)}</select></label><label class="checkbox-label"><input id="edit-directed" type="checkbox" ${item.directed?'checked':''}>具方向性</label>`}
    <div class="actions"><button type="submit" class="primary">套用變更</button><button type="button" id="delete-selected" class="danger" title="刪除選取項目" aria-label="刪除選取項目"><i data-lucide="trash-2"></i></button></div>
    ${isNode&&item.properties.operational_status==='unknown'?'<div class="muted">公開地圖設施；營運狀態、容量與庫存未提供。</div>':''}<div class="muted">${escapeHtml(isNode?item.source:item.provenance)}</div><details><summary>原始屬性與識別碼</summary><pre>${escapeHtml(item.id+'\n'+JSON.stringify(item.properties,null,2))}</pre></details></form>`;
  $('edit-form').onsubmit=event=>{event.preventDefault();
    const patch={label:$('edit-label').value,kind:$('edit-kind').value};
    // 位置不在這張表單裡改：拖曳地圖上的圖示（見 marker 的 dragend）才是人的用法，
    // 手填小數點座標沒有人看得懂，填錯了也沒有人查得出來。
    if(isNode){Object.assign(patch,{quantity:+$('edit-quantity').value,available:$('edit-available').checked});}
    else {Object.assign(patch,{source:$('edit-source').value,target:$('edit-target').value,status:$('edit-status').value,directed:$('edit-directed').checked});if(patch.source===patch.target){message('起點與終點不能相同',true);return;}}
    mutate(()=>Object.assign(item,patch));message('已更新，分析結果待重算');};
  $('delete-selected').onclick=()=>mutate(()=>{if(isNode){state.graph.nodes=state.graph.nodes.filter(n=>n.id!==item.id);state.graph.edges=state.graph.edges.filter(e=>e.source!==item.id&&e.target!==item.id);}else state.graph.edges=state.graph.edges.filter(e=>e.id!==item.id);state.selected=null;});
  if(isNode&&(item.kind==='supply'||item.kind==='facility')){const button=document.createElement('button');button.type='button';button.textContent=item.kind==='facility'?'登記到資源點資料庫':'登記到物資資料庫';button.onclick=()=>editInventory(item);el.append(button);}
}
function renderCanvas() {
  if(!map)return;
  mapLayers.clearLayers();
  markerCluster.clearLayers();
  const nodes=new Map(state.graph.nodes.filter(visible).map(n=>[n.id,n]));
  const critical=new Set(state.report?.critical_edges||[]);
  if(state.view==='map'){
    // 「需求歸到哪個事件」的連線在地圖上只是從事件中心拉到每個點的一團虛線，看不出東西；
    // 地圖上只在選到那個事件或那個點時才畫，關係圖照樣全畫。
    const sel=state.selected?.id;
    for(const e of state.graph.edges){const a=nodes.get(e.source),b=nodes.get(e.target);if(!a||!b||a.lat===null||b.lat===null)continue;
      if(e.properties?.binding==='incident'&&![e.id,e.source,e.target].includes(sel))continue;
      const color=state.selected?.id===e.id?'#eda51c':e.status==='inactive'?'#9ca9a0':critical.has(e.id)?'#9a5b9e':'#2c8fad';
      const line=L.polyline([[a.lat,a.lng],[b.lat,b.lng]],{color,weight:state.selected?.id===e.id?6:2,dashArray:e.status==='inactive'?'5 5':'7 5'}).addTo(mapLayers);
      line.bindTooltip(document.createTextNode(`${e.label} · ${STATUS[e.status]}${e.directed?' · 有方向':''}`));line.on('click',event=>{L.DomEvent.stopPropagation(event);select('edge',e.id);});
    }
    const markers=[];
    for(const n of nodes.values()){if(n.lat===null)continue;// 24px 是 WCAG 2.5.8 的觸控目標下限。群集打開時看不出差別，但 zoom 到底、
// 群集停用後每個標記都是獨立的點——那正是調度者要用手指點它的時候。
// 未結案的求救：大一號、紅色、沒人受理時會脈動，而且不併進群集，縮到全台灣也看得到。
const sos=n.properties?.db==='need'&&n.properties.need_type==='sos'&&n.properties.status==='open';
const size=sos?34:n.kind==='incident'?28:24;
// 緊急模式點名時，長者外圈顯示狀態：還沒回虛線、需要協助紅、不舒服橘、平安綠
const rc=typeof rollcallOf==='function'?rollcallOf(n):null;
// 收容所：滿了紅圈、八成以上橘圈，派人過去前一眼看得到
const cap=n.properties?.db==='point'&&n.properties.capacity?n.properties.current_load/n.properties.capacity:null;
      const marker=L.marker([n.lat,n.lng],{zIndexOffset:sos?1000:0,draggable:$('mode').value==='select'&&!n.id.startsWith('db:'),icon:L.divIcon({className:'',html:`<div class="map-dot ${sos?'sos-dot '+(n.properties.responder?'taken':'waiting'):''} ${rc?'rc-'+rc.status:''} ${cap>=1?'cap-full':cap>=0.8?'cap-high':''} ${state.selected?.id===n.id?'selected':''} ${n.available?'':'unavailable'}" style="width:${size}px;height:${size}px;background:${sos?'#c62828':nodeColor(n)}">${sos?'<b>SOS</b>':nodeIcon(n)}</div>`,iconSize:[size,size],iconAnchor:[size/2,size/2]})});
      marker.bindTooltip(document.createTextNode(sos?`${n.label} · ${n.properties.responder?'處理中：'+n.properties.responder:'尚未受理'}`:`${n.label} · ${nodeTypeLabel(n)}${rc?' · 點名：'+rc.status_label:''}${cap!==null?` · 收容 ${n.properties.current_load}/${n.properties.capacity}${cap>=1?'（已滿）':''}`:''}`));marker.on('click',event=>{L.DomEvent.stopPropagation(event);select('node',n.id);});
      marker.on('dragend',()=>{const p=marker.getLatLng();mutate(()=>{n.lat=+p.lat.toFixed(7);n.lng=+p.lng.toFixed(7);});});
      if(sos)marker.addTo(mapLayers);else markers.push(marker);
    }
    markerCluster.addLayers(markers);
  }else renderGraph(nodes,critical);
}
function renderGraph(nodes,critical) {
  const initial=!cy;
  const positions=new Map(cy?cy.nodes().map(n=>[n.data('nodeId'),n.position()]):[]);
  const elements=[...nodes.values()].map((n,i)=>({data:{id:'node:'+n.id,nodeId:n.id,label:n.label,color:nodeColor(n),shape:ROLE_LOOK[personRole(n)]?.shape||'ellipse',size:n.kind==='incident'?32:27,kind:n.kind},position:n.properties._layout||positions.get(n.id)||(n.lat!==null?{x:n.lng*10000,y:-n.lat*10000}:{x:(i%10)*100,y:Math.floor(i/10)*100})}));
  state.graph.edges.forEach(e=>{if(nodes.has(e.source)&&nodes.has(e.target))elements.push({data:{id:'edge:'+e.id,edgeId:e.id,source:'node:'+e.source,target:'node:'+e.target,label:e.label,color:e.status==='inactive'?'#9ca9a0':critical.has(e.id)?'#9a5b9e':'#6b7f73',directed:e.directed?'triangle':'none',style:e.status==='inactive'?'dashed':'solid'}});});
  if(!cy){cy=cytoscape({container:$('graph'),elements:[],minZoom:.001,maxZoom:2,style:[
    {selector:'node',style:{'background-color':'data(color)',shape:'data(shape)',label:'data(label)','font-size':12,color:'#36483d','text-valign':'bottom','text-margin-y':6,'text-wrap':'ellipsis','text-max-width':130,width:'data(size)',height:'data(size)'}},
    {selector:'edge',style:{width:2,'line-color':'data(color)','target-arrow-color':'data(color)','target-arrow-shape':'data(directed)','line-style':'data(style)','curve-style':'bezier'}},
    {selector:'node:selected',style:{'border-width':3,'border-color':'#e9a126'}},
    {selector:'edge:selected',style:{'line-color':'#e9a126',width:5}},
  ]});cy.on('tap','node',event=>select('node',event.target.data('nodeId')));cy.on('tap','edge',event=>select('edge',event.target.data('edgeId')));
    cy.on('tap',event=>{if(event.target===cy)addNode(null,event.position);});
    cy.on('dragfree','node',event=>{const n=nodeById(event.target.data('nodeId'));checkpoint();n.properties._layout=event.target.position();changed();updateHistory();});}
  cy.batch(()=>{cy.elements().remove();cy.add(elements);if(state.selected)cy.getElementById((state.selected.type==='edge'?'edge:':'node:')+state.selected.id).select();});
  if(initial&&nodes.size&&nodes.size<=600&&![...nodes.values()].some(n=>n.properties._layout)){
    arrangeGraph();
  }
}
function arrangeGraph(){cy.layout({name:cy.nodes().length>600?'grid':'cose',animate:false,randomize:false,nodeRepulsion:8000,idealEdgeLength:95,avoidOverlap:true,nodeDimensionsIncludeLabels:true}).run();fit();}
function fit(){if(state.view==='map'){const nodes=state.graph.nodes.filter(n=>visible(n)&&n.lat!==null);if(nodes.length)map.fitBounds(nodes.map(n=>[n.lat,n.lng]),{padding:[35,35],maxZoom:16,animate:false});}else if(cy){cy.resize();cy.fit(undefined,45);if(cy.zoom()>1.3){cy.zoom(1.3);cy.center();}}}
function setView(view){state.view=view;$('map').hidden=view!=='map';$('graph').hidden=view!=='graph';$('locate').hidden=view!=='map';
  for(const v of ['map','graph']){$('view-'+v).classList.toggle('active',v===view);$('view-'+v).setAttribute('aria-pressed',String(v===view));}renderCanvas();if(view==='map')map.invalidateSize();fit();}
function closeObjectMenu(){$('object-menu').hidden=true;$('add-object').setAttribute('aria-expanded','false');}
function beginAddObject(kind){
  if(!(kind in TYPES))return;
  oneShotAdd=true;$('mode').value=kind;state.connect=null;closeObjectMenu();setView('map');
  document.body.classList.add('placing-object');
  message(`新增${TYPES[kind]}：請在地圖上點選位置`);
}
function cancelAddObject(){oneShotAdd=false;$('mode').value='select';document.body.classList.remove('placing-object');renderCanvas();}
function addNode(latlng,position){
  const kind=$('mode').value;if(!(kind in TYPES))return;
  const id=crypto.randomUUID();
  mutate(()=>{state.graph.nodes.push({id,label:`${TYPES[kind]} ${state.graph.nodes.filter(n=>n.kind===kind).length+1}`,kind,lat:latlng?+latlng.lat.toFixed(7):null,lng:latlng?+latlng.lng.toFixed(7):null,quantity:1,available:true,source:'手動建立',properties:position?{_layout:position}:{}});state.selected={type:'node',id};});
  if(oneShotAdd){cancelAddObject();message(`${TYPES[kind]}已新增，可在物件詳情修改名稱與數量`);}
}
async function analyze(){const version=state.editVersion;message('正在分析關聯…');const report=await api('/analyze',{graph:state.graph});
  if(version!==state.editVersion){message('資料已變更，請重新分析');return;}state.report=report;render();
  const names=report.unlinked_objects.slice(0,30).map(id=>`<button data-focus="${escapeHtml(id)}">${escapeHtml(nodeById(id)?.label||id)}</button>`).join('');
  $('analysis-result').innerHTML=`<strong>未連結物件 ${report.metrics.unlinked_objects} 筆</strong><div>${names}</div><p>資料群組 ${report.metrics.components} 個 · 橋接關係 ${report.critical_edges.length} 條 · 關鍵物件 ${report.articulation_nodes.length} 個</p>${report.inactive_relations.length?`<p>停用關係 ${report.inactive_relations.length} 條。</p>`:''}<details><summary>分析假設</summary><ul>${report.assumptions.map(a=>`<li>${escapeHtml(a)}</li>`).join('')}</ul></details>`;
  $('analysis-result').querySelectorAll('[data-focus]').forEach(b=>b.onclick=()=>select('node',b.dataset.focus));message('關聯分析完成 · 紫色連線為單點依賴');}
async function syncDatabase(){
  return refreshOperations();
}
function invalidatePreview(){state.importVersion++;state.preview=null;$('apply-import').disabled=true;$('import-result').textContent='';}
function mappingFields(){invalidatePreview();const format=$('import-format').value,text=$('import-content').value;let fields=[];
  try{if(format==='csv')fields=Papa.parse(text,{header:true,preview:1,skipEmptyLines:true}).meta.fields||[];
    else if(format==='geojson'){const data=JSON.parse(text);fields=Object.keys((data.features?.[0]||data).properties||{});}}catch(e){fields=[];}
  const relation=$('import-kind').value==='relations';const names=relation?{id:'識別碼',label:'名稱',source:'起點識別碼',target:'終點識別碼',kind:'關係類型'}:{id:'識別碼',label:'名稱',lat:'緯度（匯入檔欄位）',lng:'經度（匯入檔欄位）',quantity:'數量',item:'品項',unit:'單位',priority:'需求優先級',dispatch_limit:'出貨上限'};
  const aliases={id:['id','ID','識別碼','編號'],label:['label','name','名稱','姓名','物資名稱','機構名稱'],lat:['lat','latitude','緯度','緯度座標'],lng:['lng','lon','longitude','經度','經度座標'],quantity:['quantity','數量','庫存'],item:['item','品項'],unit:['unit','單位'],priority:['priority','優先級','需求優先級'],dispatch_limit:['dispatch_limit','出貨上限'],source:['source','from','起點'],target:['target','to','終點'],kind:['kind','type','類型']};
  $('field-mapping').innerHTML=format==='json'?'':Object.entries(names).filter(([k])=>format==='csv'||!['lat','lng'].includes(k)).map(([key,label])=>{const guess=fields.find(f=>aliases[key].includes(f));return `<label>${label}<select data-field="${key}"><option value="">使用預設值</option>${fields.map(f=>`<option value="${escapeHtml(f)}" ${f===guess?'selected':''}>${escapeHtml(f)}</option>`).join('')}</select></label>`;}).join('');
  $('field-mapping').querySelectorAll('select').forEach(s=>s.onchange=invalidatePreview);
}
function openImport(kind){invalidatePreview();if(kind&&TYPES[kind]){$('import-kind').value=kind;mappingFields();}$('import-dialog').showModal();}
async function previewImport(event){event.preventDefault();$('preview-import').disabled=true;invalidatePreview();const importVersion=state.importVersion;$('import-result').textContent='正在解析資料…';
  try{const mapping={};$('field-mapping').querySelectorAll('select').forEach(s=>{if(s.value)mapping[s.dataset.field]=s.value;});
    const result=await api('/import-preview',{format:$('import-format').value,kind:$('import-kind').value,content:$('import-content').value,source:$('import-source').value||'匯入資料',mapping,base:$('import-replace').checked?{nodes:[],edges:[]}:state.graph});
    if(importVersion!==state.importVersion)return;
    state.preview=result;state.previewVersion=state.editVersion;
    $('import-result').innerHTML=`<strong>新增 ${result.added_nodes} 個物件、${result.added_edges} 條連線</strong>${result.warnings.map(w=>`<p>${escapeHtml(w)}</p>`).join('')}<table class="preview-table"><tr><th>名稱</th><th>類型</th><th>定位</th></tr>${result.graph.nodes.slice(-5).map(n=>`<tr><td>${escapeHtml(n.label)}</td><td>${TYPES[n.kind]}</td><td>${n.lat==null?'⚠️ 無位置，不會被媒合':'✅ 已定位'}</td></tr>`).join('')}</table>`;$('apply-import').disabled=false;
  }catch(e){$('import-result').textContent=e.message;}finally{$('preview-import').disabled=false;}}
function exportDocument(){const data={format:'smart-emergency-workspace-v1',name:$('workspace-name').value,graph:state.graph,baseline:state.baseline||null};const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=($('workspace-name').value||'工作區')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function init(){
  if(!window.L||!window.cytoscape||!window.Papa){message('地圖元件載入失敗，請檢查網路後重新整理',true);return;}
  try{const security=await fetch('/api/system/security').then(r=>r.json());principalRoles=security.roles||[];}
  catch(e){/* 查不到身分就維持保守預設（管理員），不擋任何操作——伺服器端還是會照角色擋 */}
  map=L.map('map',{preferCanvas:true}).setView([23.7,121],7);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,referrerPolicy:'strict-origin-when-cross-origin',attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  mapLayers=L.layerGroup().addTo(map);
  // Thousands of individual markers (e.g. legacy road-node meshes) get slow to render and
  // pan/zoom once on the map at once; cluster them so only nearby markers group into one
  // DOM element until you zoom in. Edges stay in mapLayers (lines aren't clusterable).
  markerCluster=L.markerClusterGroup({chunkedLoading:true, maxClusterRadius:60, spiderfyOnMaxZoom:true, disableClusteringAtZoom:19}).addTo(map);
  map.on('click',e=>addNode(e.latlng));
  initAllocation();
  initOperations();
  $('zone-select').onchange=()=>selectZone().catch(e=>message(e.message,true));loadZoneOptions();
  $('open-region').onclick=openRegion;$('close-region').onclick=()=>$('region-dialog').close();
  const regionSearch=()=>searchRegion().catch(e=>{$('region-results').textContent=e.message;});
  $('region-form').onsubmit=event=>{event.preventDefault();clearTimeout(regionTimer);regionSearch();};
  $('region-query').oninput=()=>{clearTimeout(regionTimer);regionTimer=setTimeout(regionSearch,450);};
  run('region-load',()=>loadRegion());
  run('save',()=>save());run('duplicate',()=>save(true));run('delete-workspace',()=>deleteWorkspace());run('new',async()=>{if(await discardConfirmed()){loadDocument({id:null,revision:0,name:'未命名工作區',graph:{nodes:[],edges:[]}});message('已建立空白工作區');}});
  run('undo',()=>undo());run('redo',()=>undo(true));run('fit',fit);run('layout',()=>{if(state.view!=='graph')setView('graph');arrangeGraph();checkpoint();cy.nodes().forEach(el=>{nodeById(el.data('nodeId')).properties._layout=el.position();});changed();});
  run('view-map',()=>setView('map'));run('view-graph',()=>setView('graph'));run('import',openImport);run('empty-import',openImport);run('close-import',()=>$('import-dialog').close());
  $('auto-dispatch').hidden=!isAdmin();run('auto-dispatch',runAutoDispatch);
  run('sync-db',syncDatabase);run('layer-db',syncDatabase);run('empty-db',syncDatabase);
  run('export',exportDocument);run('analyze',analyze);
  run('close-inspector',clearSelection);$('inspector-backdrop').onclick=clearSelection;
  document.addEventListener('keydown',event=>{if(event.key!=='Escape'||document.querySelector('dialog[open]'))return;if(oneShotAdd){cancelAddObject();message('已取消新增物件');return;}if(state.selected)clearSelection();});
  $('workspace-list').onchange=async()=>{const id=$('workspace-list').value;if(!id){$('workspace-list').value=workspaceSelection();return;}if(!await discardConfirmed()){$('workspace-list').value=workspaceSelection();return;}try{if(id===LIVE_WORKSPACE_ID){await loadLiveWorkspace();message('已載入正式資料；需求、物資與任務會自動更新');}else{loadDocument(await api('/'+encodeURIComponent(id)));message('已載入工作區快照');}}catch(e){message(e.message,true);}};
  $('workspace-name').oninput=changed;$('search').oninput=renderObjects;$('mode').onchange=()=>{oneShotAdd=false;document.body.classList.remove('placing-object');state.connect=null;render();};
  $('locate').onsubmit=async e=>{
    e.preventDefault();
    const q=$('locate-place').value.trim(); const box=$('locate-status');
    if(!q)return;
    const say=(text,bad)=>{box.textContent=text;box.hidden=false;box.classList.toggle('error',!!bad);};
    say('搜尋中…');
    try{
      const res=await fetch('/api/workspaces/places?q='+encodeURIComponent(q));
      const places=(await res.json()).places||[];
      if(!res.ok||!places.length){say(`找不到「${q}」。試試加上縣市鄉鎮，或改用附近明顯的地標。`,true);return;}
      const top=places[0];
      map.setView([top.lat,top.lng],15);
      say(`已跳到：${top.name}`);
    }catch(err){say('地點查詢暫時無法使用，稍後再試。',true);}
  };
  $('import-form').onsubmit=previewImport;$('import-content').oninput=mappingFields;$('import-format').onchange=mappingFields;$('import-kind').onchange=mappingFields;$('import-source').oninput=invalidatePreview;$('import-replace').onchange=invalidatePreview;
  $('import-file').onchange=async()=>{const file=$('import-file').files[0];if(!file)return;if(file.size>5_000_000){$('import-result').textContent='檔案上限為 5 MB，請分區匯入';return;}
    const text=await file.text();$('import-content').value=text;$('import-source').value=file.name;
    if(file.name.toLowerCase().endsWith('.csv'))$('import-format').value='csv';else{try{const d=JSON.parse(text);$('import-format').value=d.graph||d.nodes?'json':'geojson';}catch(e){$('import-format').value='geojson';}}mappingFields();};
  run('apply-import',()=>{if(!state.preview)return;if(state.previewVersion!==state.editVersion)throw Error('工作區已變更，請重新預覽');const result=state.preview;mutate(()=>{state.graph=result.graph;state.selected=null;if($('import-replace').checked&&result.baseline)state.baseline=structuredClone(result.baseline);});$('import-dialog').close();fit();message(`已加入 ${result.added_nodes} 個物件、${result.added_edges} 條連線`);state.preview=null;});
  window.addEventListener('beforeunload',event=>{if(state.dirty||inventoryDrafts.size){event.preventDefault();event.returnValue='';}});
  let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{map.invalidateSize();fit();},150);});
  render();try{const items=await listWorkspaces();const explicit=new URLSearchParams(location.search).get('id');if(explicit&&items.some(w=>w.id===explicit)){loadDocument(await api('/'+encodeURIComponent(explicit)));message('已載入指定工作區快照');}else{await loadLiveWorkspace();message('已載入正式資料；需求、物資與任務會自動更新');}}catch(e){message(e.message,true);}
  setInterval(()=>{if(state.live&&!state.dirty&&!inventoryDrafts.size&&document.visibilityState==='visible')refreshOperations({silent:true}).catch(e=>message(e.message,true));},30000);
}
init();
