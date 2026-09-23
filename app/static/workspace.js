'use strict';
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const TYPES = {incident:'事件', person:'人員', supply:'物資', facility:'設施', custom:'自訂物件'};
const RELATIONS = {assignment:'指派', supplies:'供應', care:'照護', request:'提出需求', related:'相關', custom:'自訂關係'};
const COLORS = {incident:'#c34736', person:'#237caf', supply:'#c57320', facility:'#8b5fa8', custom:'#087f72'};
const STATUS = {active:'啟用', inactive:'停用'};
const state = {id:null, revision:0, graph:{nodes:[],edges:[]}, selected:null, view:'map', dirty:false,
  undo:[], redo:[], hidden:new Set(), report:null, connect:null, preview:null, editVersion:0, documentVersion:0, importVersion:0};
let map, mapLayers, cy;
let principalRoles = ['admin']; // 保守預設：拿到真正的角色前先當作管理員，載入完成後 init() 會校正。
function isAdmin() { return principalRoles.includes('admin'); }
function rememberWorkspace(id){try{if(id)localStorage.setItem('emergency:last-workspace',id);else localStorage.removeItem('emergency:last-workspace');}catch(e){}}
function lastWorkspace(){try{return localStorage.getItem('emergency:last-workspace');}catch(e){return null;}}
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
function changed() { state.dirty=true; state.editVersion++; state.report=null; if(!state.baseline&&state.graph.nodes.length)state.baseline=currentBaseline(); invalidateComparison();invalidateAllocation(); $('save-state').textContent='有未儲存變更'; $('analysis-result').textContent='分析尚未更新'; }
// Baselines are immutable snapshots and can be shared by history entries.
function historySnapshot(){return {graph:structuredClone(state.graph),baseline:state.baseline};}
function checkpoint() { state.undo.push(historySnapshot()); if(state.undo.length>25)state.undo.shift(); state.redo=[]; }
function mutate(fn) { checkpoint(); fn(); changed(); render(); }
function updateHistory() { $('undo').disabled=!state.undo.length; $('redo').disabled=!state.redo.length; }
function undo(redo=false) { const from=redo?state.redo:state.undo, to=redo?state.undo:state.redo; if(!from.length)return;
  to.push(historySnapshot()); const previous=from.pop();state.graph=previous.graph;state.baseline=previous.baseline;state.selected=null; changed(); render(); }
function discardConfirmed() { return (!state.dirty && !inventoryDrafts.size) || confirm('目前有未儲存或待寫回變更，確定離開這個工作區？'); }
function loadDocument(data) {
  inventoryDrafts.clear();updateInventoryCount();
  operationEvents.clear();
  state.documentVersion++;
  if(cy){cy.destroy();cy=null;}
  state.id=data.id; state.revision=data.revision; state.graph=data.graph; state.selected=null; state.report=null;
  state.undo=[];state.redo=[];state.dirty=false;state.editVersion++;state.connect=null;
  $('workspace-name').value=data.name; $('workspace-list').value=data.id||'';
  state.baseline=structuredClone(data.baseline||null)||(state.graph.nodes.length?currentBaseline():null);invalidateComparison();invalidateAllocation();
  $('save-state').textContent=data.id?`已儲存 · 版本 ${data.revision}`:'尚未儲存';
  $('analysis-result').textContent='';render();fit();
  workspaceUrl(data.id);
  rememberWorkspace(data.id);
}
async function listWorkspaces() {
  const items=await api(''); $('workspace-list').innerHTML='<option value="">未儲存工作區</option>'+items.map(w=>`<option value="${escapeHtml(w.id)}">${escapeHtml(w.name)}</option>`).join('');
  $('workspace-list').value=state.id||'';
  return items;
}
async function save(copy=false) {
  const version=state.editVersion, documentVersion=state.documentVersion, graph=structuredClone(state.graph), name=$('workspace-name').value.trim()||'未命名工作區';
  const id=copy?null:state.id;
  const data=await api(id?'/'+id:'',{name,graph,baseline:state.baseline||null,revision:state.revision},id?'PUT':'POST');
  if(documentVersion!==state.documentVersion){await listWorkspaces();message('先前工作區已儲存');return;}
  state.id=data.id;state.revision=data.revision;
  workspaceUrl(data.id);
  rememberWorkspace(data.id);
  if(version===state.editVersion){state.dirty=false;$('save-state').textContent=`已儲存 · 版本 ${data.revision}`;}
  await listWorkspaces();message(copy?'已另存副本':'工作區已儲存');
}
function options(items, value) { return Object.entries(items).map(([k,v])=>`<option value="${escapeHtml(k)}" ${k===value?'selected':''}>${escapeHtml(v)}</option>`).join(''); }
function nodeOptions(value='', placeholder='選擇物件') { return `<option value="">${placeholder}</option>`+state.graph.nodes.map(n=>`<option value="${escapeHtml(n.id)}" ${n.id===value?'selected':''}>${escapeHtml(n.label)} (${TYPES[n.kind]})</option>`).join(''); }
function nodeById(id) { return state.graph.nodes.find(n=>n.id===id); }
function visible(n) { return !state.hidden.has(n.kind); }
function render() {
  const counts={};state.graph.nodes.forEach(n=>counts[n.kind]=(counts[n.kind]||0)+1);
  $('layers').innerHTML=Object.entries(TYPES).map(([kind,label])=>`<div class="layer-row"><label class="layer"><input type="checkbox" data-kind="${kind}" ${state.hidden.has(kind)?'':'checked'}><span class="swatch" style="background:${COLORS[kind]}"></span>${label}<small>${counts[kind]||0}</small></label><button class="layer-add" data-layer-add="${kind}" title="匯入${label}" aria-label="匯入${label}"><i data-lucide="upload"></i></button></div>`).join('');
  $('layers').querySelectorAll('[data-layer-add]').forEach(button=>button.onclick=()=>openImport(button.dataset.layerAdd));
  $('layers').querySelectorAll('input').forEach(input=>input.onchange=()=>{input.checked?state.hidden.delete(input.dataset.kind):state.hidden.add(input.dataset.kind);render();});
  const m=state.report?.metrics||{};
  $('metrics').innerHTML=[['物件',state.graph.nodes.length],['關係',state.graph.edges.length],['人員',counts.person||0],['事件',counts.incident||0],['可用物資',state.graph.nodes.filter(nodeHasSupply).length],['待處理需求',m.open_demands??'—'],['未連結物件',m.unlinked_objects??'—']].map(([label,value],i)=>`<div class="metric ${i===6&&value>0?'alert':''}"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join('');
  renderObjects();renderSelection();renderCanvas();updateHistory();renderOperations();
  $('empty').hidden=state.graph.nodes.length>0;
  $('connect-state').hidden=$('mode').value!=='connect';
  $('connect-state').textContent=state.connect?`起點：${nodeById(state.connect)?.label||''} · 選擇終點`:'選擇連線起點';
  icons();
}
function renderObjects() {
  const query=$('search').value.toLowerCase();const nodes=state.graph.nodes.filter(n=>visible(n)&&(n.label.toLowerCase().includes(query)||n.id.toLowerCase().includes(query)));
  $('object-count').textContent=`${nodes.length} 筆`;
  $('objects').innerHTML=nodes.slice(0,150).map(n=>`<button data-node="${escapeHtml(n.id)}" class="${state.selected?.id===n.id?'selected':''}" title="${escapeHtml(n.label)}"><span class="swatch" style="background:${COLORS[n.kind]}"></span><span class="object-label">${escapeHtml(n.label)}</span>${n.lat===null?'<small>無座標</small>':''}</button>`).join('')+(nodes.length>150?'<div class="muted">顯示前 150 筆</div>':'');
  $('objects').querySelectorAll('[data-node]').forEach(btn=>btn.onclick=()=>{select('node',btn.dataset.node);const n=nodeById(btn.dataset.node);if(n.lat!==null)map.panTo([n.lat,n.lng]);if(cy){const el=cy.getElementById('node:'+n.id);cy.center(el);}});
  renderOperationTasks();
}
function select(type,id) {
  if(type==='node'&&$('mode').value==='connect') {
    if(!state.connect){state.connect=id;render();return;}
    if(state.connect!==id){const a=state.connect;state.connect=null;const edge={id:crypto.randomUUID(),source:a,target:id,kind:'related',label:'新增關係',status:'active',directed:true,provenance:'手動建立',properties:{}};
      mutate(()=>{state.graph.edges.push(edge);state.selected={type:'edge',id:edge.id};});return;}
  }
  state.selected={type,id};renderObjects();renderSelection();renderCanvas();icons();
}
function renderSelection() {
  const selected=state.selected, el=$('selection');
  const item=selected?(selected.type==='node'?nodeById(selected.id):state.graph.edges.find(e=>e.id===selected.id)):null;
  if(!item){el.innerHTML='尚未選取';return;}
  const isNode=selected.type==='node';
  if(item.id.startsWith('db:')){renderOperationalSelection(item,isNode);return;}
  el.innerHTML=`<form id="edit-form"><label>名稱<input id="edit-label" value="${escapeHtml(item.label)}" maxlength="300" required></label>
    <label>類型<select id="edit-kind">${options(isNode?TYPES:RELATIONS,item.kind)}</select></label>
    ${isNode?`<div class="form-grid"><label>緯度<input id="edit-lat" type="number" step="any" min="-90" max="90" value="${item.lat??''}"></label><label>經度<input id="edit-lng" type="number" step="any" min="-180" max="180" value="${item.lng??''}"></label></div><label>${item.kind==='facility'?'已確認可用容量':'數量'}<input id="edit-quantity" type="number" step="any" min="0" max="1000000000" value="${item.quantity}" required></label><label class="checkbox-label"><input id="edit-available" type="checkbox" ${item.available?'checked':''}>納入分析</label>`:
    `<label>起點<select id="edit-source" required>${nodeOptions(item.source)}</select></label><label>終點<select id="edit-target" required>${nodeOptions(item.target)}</select></label><label>狀態<select id="edit-status">${options(STATUS,item.status)}</select></label><label class="checkbox-label"><input id="edit-directed" type="checkbox" ${item.directed?'checked':''}>具方向性</label>`}
    <div class="actions"><button type="submit" class="primary">套用變更</button><button type="button" id="delete-selected" class="danger" title="刪除選取項目" aria-label="刪除選取項目"><i data-lucide="trash-2"></i></button></div>
    ${isNode&&item.properties.operational_status==='unknown'?'<div class="muted">公開地圖設施；營運狀態、容量與庫存未提供。</div>':''}<div class="muted">${escapeHtml(isNode?item.source:item.provenance)}</div><details><summary>原始屬性與識別碼</summary><pre>${escapeHtml(item.id+'\n'+JSON.stringify(item.properties,null,2))}</pre></details></form>`;
  $('edit-form').onsubmit=event=>{event.preventDefault();
    const patch={label:$('edit-label').value,kind:$('edit-kind').value};
    if(isNode){Object.assign(patch,{lat:$('edit-lat').value===''?null:+$('edit-lat').value,lng:$('edit-lng').value===''?null:+$('edit-lng').value,quantity:+$('edit-quantity').value,available:$('edit-available').checked});if((patch.lat===null)!==(patch.lng===null)){message('經緯度必須成對提供',true);return;}}
    else {Object.assign(patch,{source:$('edit-source').value,target:$('edit-target').value,status:$('edit-status').value,directed:$('edit-directed').checked});if(patch.source===patch.target){message('起點與終點不能相同',true);return;}}
    mutate(()=>Object.assign(item,patch));message('已更新，分析結果待重算');};
  $('delete-selected').onclick=()=>mutate(()=>{if(isNode){state.graph.nodes=state.graph.nodes.filter(n=>n.id!==item.id);state.graph.edges=state.graph.edges.filter(e=>e.source!==item.id&&e.target!==item.id);}else state.graph.edges=state.graph.edges.filter(e=>e.id!==item.id);state.selected=null;});
  if(isNode&&(item.kind==='supply'||item.kind==='facility')){const button=document.createElement('button');button.type='button';button.textContent=item.kind==='facility'?'登記到資源點資料庫':'登記到物資資料庫';button.onclick=()=>editInventory(item);el.append(button);}
}
function renderCanvas() {
  if(!map)return;
  mapLayers.clearLayers();
  const nodes=new Map(state.graph.nodes.filter(visible).map(n=>[n.id,n]));
  const critical=new Set(state.report?.critical_edges||[]);
  if(state.view==='map'){
    for(const e of state.graph.edges){const a=nodes.get(e.source),b=nodes.get(e.target);if(!a||!b||a.lat===null||b.lat===null)continue;
      const color=state.selected?.id===e.id?'#eda51c':e.status==='inactive'?'#9ca9a0':critical.has(e.id)?'#9a5b9e':'#2c8fad';
      const line=L.polyline([[a.lat,a.lng],[b.lat,b.lng]],{color,weight:state.selected?.id===e.id?6:2,dashArray:e.status==='inactive'?'5 5':'7 5'}).addTo(mapLayers);
      line.bindTooltip(document.createTextNode(`${e.label} · ${STATUS[e.status]}${e.directed?' · 有方向':''}`));line.on('click',event=>{L.DomEvent.stopPropagation(event);select('edge',e.id);});
    }
    for(const n of nodes.values()){if(n.lat===null)continue;const size=n.kind==='incident'?21:17;
      const marker=L.marker([n.lat,n.lng],{draggable:$('mode').value==='select'&&!n.id.startsWith('db:'),icon:L.divIcon({className:'',html:`<div class="map-dot ${state.selected?.id===n.id?'selected':''} ${n.available?'':'unavailable'}" style="width:${size}px;height:${size}px;background:${COLORS[n.kind]}"></div>`,iconSize:[size,size],iconAnchor:[size/2,size/2]})}).addTo(mapLayers);
      marker.bindTooltip(document.createTextNode(`${n.label} · ${TYPES[n.kind]}`));marker.on('click',event=>{L.DomEvent.stopPropagation(event);select('node',n.id);});
      marker.on('dragend',()=>{const p=marker.getLatLng();mutate(()=>{n.lat=+p.lat.toFixed(7);n.lng=+p.lng.toFixed(7);});});
    }
  }else renderGraph(nodes,critical);
}
function renderGraph(nodes,critical) {
  const initial=!cy;
  const positions=new Map(cy?cy.nodes().map(n=>[n.data('nodeId'),n.position()]):[]);
  const elements=[...nodes.values()].map((n,i)=>({data:{id:'node:'+n.id,nodeId:n.id,label:n.label,color:COLORS[n.kind],size:n.kind==='incident'?32:27,kind:n.kind},position:n.properties._layout||positions.get(n.id)||(n.lat!==null?{x:n.lng*10000,y:-n.lat*10000}:{x:(i%10)*100,y:Math.floor(i/10)*100})}));
  state.graph.edges.forEach(e=>{if(nodes.has(e.source)&&nodes.has(e.target))elements.push({data:{id:'edge:'+e.id,edgeId:e.id,source:'node:'+e.source,target:'node:'+e.target,label:e.label,color:e.status==='inactive'?'#9ca9a0':critical.has(e.id)?'#9a5b9e':'#6b7f73',directed:e.directed?'triangle':'none',style:e.status==='inactive'?'dashed':'solid'}});});
  if(!cy){cy=cytoscape({container:$('graph'),elements:[],minZoom:.001,maxZoom:2,style:[
    {selector:'node',style:{'background-color':'data(color)',label:'data(label)','font-size':11,color:'#36483d','text-valign':'bottom','text-margin-y':6,'text-wrap':'ellipsis','text-max-width':110,width:'data(size)',height:'data(size)'}},
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
function addNode(latlng,position){const kind=$('mode').value;if(!(kind in TYPES))return;const id=crypto.randomUUID();mutate(()=>{state.graph.nodes.push({id,label:`${TYPES[kind]} ${state.graph.nodes.filter(n=>n.kind===kind).length+1}`,kind,lat:latlng?+latlng.lat.toFixed(7):null,lng:latlng?+latlng.lng.toFixed(7):null,quantity:1,available:true,source:'手動建立',properties:position?{_layout:position}:{}});state.selected={type:'node',id};});}
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
  const relation=$('import-kind').value==='relations';const names=relation?{id:'識別碼',label:'名稱',source:'起點識別碼',target:'終點識別碼',kind:'關係類型'}:{id:'識別碼',label:'名稱',lat:'緯度',lng:'經度',quantity:'數量',item:'品項',unit:'單位',priority:'需求優先級',dispatch_limit:'出貨上限'};
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
    $('import-result').innerHTML=`<strong>新增 ${result.added_nodes} 個物件、${result.added_edges} 條連線</strong>${result.warnings.map(w=>`<p>${escapeHtml(w)}</p>`).join('')}<table class="preview-table"><tr><th>名稱</th><th>類型</th><th>經緯度</th></tr>${result.graph.nodes.slice(-5).map(n=>`<tr><td>${escapeHtml(n.label)}</td><td>${TYPES[n.kind]}</td><td>${n.lat??'無'} / ${n.lng??'無'}</td></tr>`).join('')}</table>`;$('apply-import').disabled=false;
  }catch(e){$('import-result').textContent=e.message;}finally{$('preview-import').disabled=false;}}
function exportDocument(){const data={format:'smart-emergency-workspace-v1',name:$('workspace-name').value,graph:state.graph,baseline:state.baseline||null};const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=($('workspace-name').value||'工作區')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function init(){
  if(!window.L||!window.cytoscape||!window.Papa){message('地圖元件載入失敗，請檢查網路後重新整理',true);return;}
  try{const security=await fetch('/api/system/security').then(r=>r.json());principalRoles=security.roles||[];}
  catch(e){/* 查不到身分就維持保守預設（管理員），不擋任何操作——伺服器端還是會照角色擋 */}
  map=L.map('map',{preferCanvas:true}).setView([23.7,121],7);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  mapLayers=L.layerGroup().addTo(map);map.on('click',e=>addNode(e.latlng));
  initComparison();
  initAllocation();
  initOperations();
  run('save',()=>save());run('duplicate',()=>save(true));run('new',()=>{if(discardConfirmed()){loadDocument({id:null,revision:0,name:'未命名工作區',graph:{nodes:[],edges:[]}});message('已建立空白工作區');}});
  run('undo',()=>undo());run('redo',()=>undo(true));run('fit',fit);run('layout',()=>{if(state.view!=='graph')setView('graph');arrangeGraph();checkpoint();cy.nodes().forEach(el=>{nodeById(el.data('nodeId')).properties._layout=el.position();});changed();});
  run('view-map',()=>setView('map'));run('view-graph',()=>setView('graph'));run('import',openImport);run('empty-import',openImport);run('close-import',()=>$('import-dialog').close());
  run('sync-db',syncDatabase);run('layer-db',syncDatabase);run('empty-db',syncDatabase);
  run('export',exportDocument);run('analyze',analyze);
  $('workspace-list').onchange=async()=>{const id=$('workspace-list').value;if(!id){$('workspace-list').value=state.id||'';return;}if(!discardConfirmed()){$('workspace-list').value=state.id||'';return;}try{loadDocument(await api('/'+id));message('已載入工作區');}catch(e){message(e.message,true);}};
  $('workspace-name').oninput=changed;$('search').oninput=renderObjects;$('mode').onchange=()=>{state.connect=null;render();};
  $('locate').onsubmit=e=>{e.preventDefault();map.setView([+$('center-lat').value,+$('center-lng').value],15);};
  $('import-form').onsubmit=previewImport;$('import-content').oninput=mappingFields;$('import-format').onchange=mappingFields;$('import-kind').onchange=mappingFields;$('import-source').oninput=invalidatePreview;$('import-replace').onchange=invalidatePreview;
  $('import-file').onchange=async()=>{const file=$('import-file').files[0];if(!file)return;if(file.size>5_000_000){$('import-result').textContent='檔案上限為 5 MB，請分區匯入';return;}
    const text=await file.text();$('import-content').value=text;$('import-source').value=file.name;
    if(file.name.toLowerCase().endsWith('.csv'))$('import-format').value='csv';else{try{const d=JSON.parse(text);$('import-format').value=d.graph||d.nodes?'json':'geojson';}catch(e){$('import-format').value='geojson';}}mappingFields();};
  run('apply-import',()=>{if(!state.preview)return;if(state.previewVersion!==state.editVersion)throw Error('工作區已變更，請重新預覽');const result=state.preview;mutate(()=>{state.graph=result.graph;state.selected=null;if($('import-replace').checked&&result.baseline)state.baseline=structuredClone(result.baseline);});$('import-dialog').close();fit();message(`已加入 ${result.added_nodes} 個物件、${result.added_edges} 條連線`);state.preview=null;});
  window.addEventListener('beforeunload',event=>{if(state.dirty||inventoryDrafts.size){event.preventDefault();event.returnValue='';}});
  let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{map.invalidateSize();fit();},150);});
  render();try{const items=await listWorkspaces();const explicit=new URLSearchParams(location.search).get('id');const remembered=lastWorkspace();const id=explicit||(items.some(w=>w.id===remembered)?remembered:items[0]?.id||null);if(id){loadDocument(await api('/'+encodeURIComponent(id)));message('已回到工作區快照；更新現況可讀取最新營運資料');}else{rememberWorkspace(null);await refreshOperations();if(state.graph.nodes.length)fit();}}catch(e){message(e.message,true);}
}
init();
