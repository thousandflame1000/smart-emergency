'use strict';
const NEED_STATUS=window.NEED_STATUS_LABEL;   // 單一來源見 /static/labels.js
const OPERATION_STAGES={sos:'緊急求救',...NEED_STATUS};
const CHECKIN_STATUS={ok:'平安',safe:'平安',pending:'待回應',no_response:'未回應',help_needed:'需要協助',confirmed:'已確認'};
const ROLE_NAMES={elderly:'長者',volunteer:'志工',family:'家屬',admin:'管理員'};
const INVENTORY_FIELDS={name:'名稱',quantity:'數量／單位',lat:'位置',lng:'位置',address:'地址',is_available:'可用',
  capacity:'容量',phone:'電話',operating_hours:'開放時間'};
const POINT_TYPE_LABELS={shelter:'避難收容所',community:'里民活動中心',hospital:'醫療院所',fire_station:'消防分隊',
  police:'警察局/派出所',store:'物資分發點',warehouse:'物資倉庫',clinic:'衛生所',other:'其他'};
const inventoryDrafts=new Map(), operationEvents=new Map();
let operationOwners=[], operationRequest=null, operationStage='open', catalogTab='objects', inventoryReview=null;

function workspaceUrl(id){const url=new URL(location.href);id?url.searchParams.set('id',id):url.searchParams.delete('id');history.replaceState(null,'',url.pathname+url.search+url.hash);}
function focusOperational(id){const node=nodeById(id);if(!node)return;$('mode').value='select';state.hidden.delete(node.kind);select('node',id);if(node.lat!==null)map.panTo([node.lat,node.lng]);if(cy)cy.center(cy.getElementById('node:'+id));}
function operationalNodes(){return state.graph.nodes.filter(n=>n.properties.db==='need');}
function operationalResources(){return state.graph.nodes.filter(n=>['resource','point'].includes(n.properties.db));}
function taskInStage(node,stage){
  if(stage==='sos')return node.properties.status==='open'&&node.properties.need_type==='sos';
  if(stage==='open')return node.properties.status==='open'&&node.properties.need_type!=='sos';
  return node.properties.status===stage;
}
function setCatalog(tab){
  catalogTab=tab;$('objects').hidden=tab!=='objects';$('operation-tasks').hidden=tab!=='tasks';$('operation-resources').hidden=tab!=='resources';
  for(const key of ['objects','tasks','resources'])$('catalog-'+key).classList.toggle('active',tab===key);
  $('catalog-title').textContent={objects:'物件',tasks:'需求與任務',resources:'物資回報'}[tab];renderObjects();
}
function renderOperations(){
  const tasks=operationalNodes();
  $('operation-stages').innerHTML=Object.entries(OPERATION_STAGES).map(([key,label])=>`<button data-stage="${key}" class="${key==='sos'?'sos-stage ':''}${operationStage===key&&catalogTab==='tasks'?'active':''}" aria-pressed="${operationStage===key&&catalogTab==='tasks'}">${label}<strong>${tasks.filter(n=>taskInStage(n,key)).length}</strong></button>`).join('');
  $('operation-stages').querySelectorAll('button').forEach(button=>button.onclick=()=>{operationStage=button.dataset.stage;setCatalog('tasks');renderOperations();});
  const stamp=state.graph.nodes.find(n=>n.properties.observed_at)?.properties.observed_at;
  $('observed-at').textContent=stamp?'現況快照 · '+new Date(stamp).toLocaleString('zh-TW'):'尚未讀取平台現況';
  renderOperationTasks();
}
function renderOperationTasks(){
  const query=$('search').value.toLowerCase();
  const tasks=operationalNodes().filter(n=>taskInStage(n,operationStage)&&(n.label+' '+n.id+' '+n.properties.description).toLowerCase().includes(query)).sort((a,b)=>b.properties.urgency-a.properties.urgency);
  $('operation-tasks').innerHTML=tasks.map(n=>`<button data-task="${escapeHtml(n.id)}" class="${state.selected?.id===n.id?'selected':''}"><span class="task-priority ${n.properties.need_type==='sos'?'urgent':''}">${n.properties.need_type==='sos'?'SOS':'P'+n.properties.urgency}</span><span class="task-text">${escapeHtml(n.label)}<small>${escapeHtml(n.properties.quantity_text||'數量未填')} · ${n.lat===null?'位置未知':escapeHtml(n.properties.address||n.properties.location_source)}</small></span></button>`).join('')||'<p class="muted">此狀態沒有需求</p>';
  $('operation-tasks').querySelectorAll('button').forEach(b=>b.onclick=()=>focusOperational(b.dataset.task));
  if(catalogTab==='tasks')$('object-count').textContent=tasks.length+' 筆';
}
function renderOperationResources(){
  const query=$('search').value.toLowerCase();
  const resources=operationalResources().filter(n=>(n.label+' '+n.id+' '+(n.properties.owner||'')).toLowerCase().includes(query));
  $('operation-resources').innerHTML=resources.map(n=>`<button data-resource="${escapeHtml(n.id)}" class="${state.selected?.id===n.id?'selected':''}"><span class="swatch" style="background:${COLORS[n.kind]}"></span><span class="task-text">${escapeHtml(n.label)}<small>${escapeHtml(n.properties.quantity_text||('資源點 · '+(n.properties.capacity??'容量未填')))} · ${escapeHtml(n.properties.owner||n.properties.address||'提供者未填')}</small></span><strong class="resource-state ${n.available?'':'unavailable'}">${n.available?'可用':'保留／停用'}</strong></button>`).join('')||'<p class="muted">尚無物資回報或資源點</p>';
  $('operation-resources').querySelectorAll('button').forEach(b=>b.onclick=()=>focusOperational(b.dataset.resource));
  if(catalogTab==='resources')$('object-count').textContent=resources.length+' 筆';
}
// Same rule as workspace_bridge.bind_to_incidents: each live need joins its nearest incident,
// resource points within 20 km join as in-range facilities, and a workspace without any
// incident gets one live event node so needs never float unattached.
function bindToIncidents(nodes){
  const located=n=>n.lat!==null&&n.lat!==undefined&&n.lng!==null&&n.lng!==undefined;
  const km=(a,b)=>{const r=Math.PI/180,dLat=(b.lat-a.lat)*r,dLng=(b.lng-a.lng)*r;const h=Math.sin(dLat/2)**2+Math.cos(a.lat*r)*Math.cos(b.lat*r)*Math.sin(dLng/2)**2;return 12742*Math.asin(Math.sqrt(h));};
  let incidents=nodes.filter(n=>n.kind==='incident');
  const needs=nodes.filter(n=>n.properties.db==='need'&&n.properties.status!=='cancelled');
  const extra=[],edges=[];
  if(needs.length&&!incidents.length){
    const spots=needs.filter(located);
    const event={id:'db:incident:live',label:'即時通報事件',kind:'incident',lat:spots.length?spots.reduce((s,n)=>s+n.lat,0)/spots.length:null,lng:spots.length?spots.reduce((s,n)=>s+n.lng,0)/spots.length:null,quantity:0,available:true,source:'平台需求單彙整',properties:{db:'event',description:'工作區沒有事件物件時，彙整即時需求'},logistics:[]};
    incidents=[event];extra.push(event);
  }
  if(!incidents.length)return{nodes:extra,edges};
  const nearest=node=>{const placed=incidents.filter(located);if(!located(node)||!placed.length)return[incidents[0],null];let best=placed[0],d=km(node,best);for(const i of placed.slice(1)){const x=km(node,i);if(x<d){best=i;d=x;}}return[best,d];};
  const edge=(incident,node,label)=>({id:'db:edge:event:'+node.id,source:incident.id,target:node.id,label,kind:'related',directed:true,status:'active',provenance:'平台資料庫',properties:{db:true,binding:'incident'}});
  for(const need of needs)edges.push(edge(nearest(need)[0],need,need.properties.need_type==='sos'?'緊急求助':'事件需求'));
  for(const point of nodes.filter(n=>n.properties.db==='point')){const[incident,d]=nearest(point);if(d!==null&&d<=20)edges.push(edge(incident,point,'範圍內資源點'));}
  return{nodes:extra,edges};
}
async function refreshOperations(options={}){
  if(operationRequest)return operationRequest;
  const preserveLiveClean=state.live&&!state.dirty&&!inventoryDrafts.size;
  const documentVersion=state.documentVersion;
  for(const id of ['sync-db','layer-db'])$(id).disabled=true;
  operationRequest=(async()=>{
    const snapshot=await api('/operational-data'+(state.zone?'?zone_id='+encodeURIComponent(state.zone):''));
    if(documentVersion!==state.documentVersion)return;
    operationOwners=snapshot.owners;
    operationEvents.clear();
    const fresh=new Map(snapshot.graph.nodes.map(n=>[n.id,n]));
    for(const node of state.graph.nodes){if(fresh.has(node.id)&&node.properties._layout)fresh.get(node.id).properties._layout=node.properties._layout;}
    const merged=state.graph.nodes.filter(n=>!n.id.startsWith('db:')).concat([...fresh.values()]);
    const bound=bindToIncidents(merged);
    const nodes=merged.concat(bound.nodes);
    const ids=new Set(nodes.map(n=>n.id));
    const edges=state.graph.edges.filter(e=>!e.id.startsWith('db:edge:')&&ids.has(e.source)&&ids.has(e.target)).concat(snapshot.graph.edges,bound.edges);
    mutate(()=>{state.graph={nodes,edges};if(state.selected&&!ids.has(state.selected.id))state.selected=null;});
    if(preserveLiveClean){state.undo=[];state.dirty=false;state.baseline=null;$('save-state').textContent='即時資料 · 自動更新';updateHistory();}
    if(!options.silent)message(`現況已更新：${snapshot.counts.elders} 位長者、${snapshot.counts.volunteers} 位志工、${snapshot.counts.demands} 筆需求、${snapshot.counts.supplies} 筆物資`);
  })();
  try{return await operationRequest;}finally{operationRequest=null;for(const id of ['sync-db','layer-db'])$(id).disabled=false;}
}
async function runAutoDispatch(){
  if(!await askConfirm('自動派遣','<p>依緊急度、脆弱度、距離與志工負荷排序所有待派需求。</p><p class="muted">個人物資只產生「待核准」建議，核准後才通知志工。</p>','開始'))return;
  const response=await fetch('/api/resources/dispatch',{method:'POST'});
  const data=await response.json();
  if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'自動派遣失敗');
  await refreshOperations({silent:true});
  if(data.suggested){operationStage='suggested';setCatalog('tasks');renderOperations();}
  const reasons={};for(const d of data.details||[])if(d.result==='skipped')reasons[d.reason]=(reasons[d.reason]||0)+1;
  const skipped=Object.entries(reasons).map(([r,n])=>`${r} ${n}`).join('、');
  message(`自動派遣：待核准 ${data.suggested} 筆、資源點媒合 ${data.matched} 筆、略過 ${data.skipped} 筆${skipped?'（'+skipped+'）':''}`);
}
function renderOperationalSelection(item,isNode){
  const p=item.properties,el=$('selection');
  const fields=[];
  if(p.db==='user')fields.push(['角色',(p.roles||[]).map(r=>ROLE_NAMES[r]||r).join('、')],['最近打卡',p.checkin?(CHECKIN_STATUS[p.checkin.status]||p.checkin.status)+' · '+p.checkin.date:'尚無紀錄'],['打卡回覆',p.checkin?.note||'未提供'],['未解除警報',p.active_alerts],['脆弱度',p.vulnerability??'未評估']);
  if(p.db==='resource')fields.push(['擁有者',p.owner],['登記數量',p.quantity_text||'未知'],['可用狀態',item.available?'可用':'保留中或不可用']);
  if(p.db==='point')fields.push(['收容容量',p.capacity??'未知'],['目前人數',p.current_load??'未知'],['庫存','未提供'],
    ['電話',p.base_values?.phone||'未提供'],['開放時間',p.base_values?.operating_hours||'未提供']);
  if(p.db==='need')fields.push(['狀態',NEED_STATUS[p.status]||p.status],['優先級',p.urgency],['登記數量',p.quantity_text||'未知'],['需求',p.description||'未填'],['定位依據',p.location_source]);
  // 原始經緯度是系統內部表示（而且會露出浮點誤差），摘要只講定位結果；
  // 要精確數值的人是在編輯，那邊本來就有緯度／經度欄位。
  if(isNode)fields.push(['地址',p.address||p.base_values?.address||'未提供'],['地圖定位',item.lat===null?'未定位':'已定位']);
  const related=isNode?state.graph.edges.filter(e=>e.id.startsWith('db:edge:')&&(e.source===item.id||e.target===item.id)):[];
  el.innerHTML=`<div class="object-heading"><strong>${escapeHtml(item.label)}</strong><span class="source-label">${escapeHtml(isNode?item.source:item.provenance)}</span></div><dl class="object-facts">${fields.map(([k,v])=>`<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('')}</dl>${p.quantity_verified===false?'<p class="error">數量或單位待確認，未納入分配試算。</p>':''}
    ${p.db==='need'&&p.need_type!=='sos'&&['open','suggested'].includes(p.status)?'<div id="need-candidate-summary" class="candidate-summary muted">候選載入中…</div>':''}
    <div id="operational-actions" class="actions"></div>${related.length?'<h2>關聯物件</h2><div class="related-objects">'+related.map(e=>{const other=e.source===item.id?e.target:e.source;return `<button data-related="${escapeHtml(other)}"><span>${escapeHtml(e.label)}</span>${escapeHtml(nodeById(other)?.label||other)}</button>`;}).join('')+'</div>':''}
    ${p.db==='need'?'<h2 class="event-heading">任務紀錄</h2><div id="operation-events" class="muted">讀取中</div>':''}<p class="muted">${p.observed_at?escapeHtml(new Date(p.observed_at).toLocaleString('zh-TW')):''}</p><details><summary>來源識別碼</summary><pre>${escapeHtml(item.id)}</pre></details>`;
  el.querySelectorAll('[data-related]').forEach(b=>b.onclick=()=>focusOperational(b.dataset.related));
  const actions=$('operational-actions');
  function button(label,icon,fn,primary=false){const b=document.createElement('button');b.type='button';b.className=primary?'primary':'';b.innerHTML=`<i data-lucide="${icon}"></i>${label}`;b.onclick=fn;actions.append(b);return b;}
  if(p.db==='resource'||p.db==='point')button('編輯正式資料','pencil',()=>editInventory(item));
  if(p.db==='need'){
    if(p.status==='suggested'){
      loadOperationCandidates(item);
      if(isAdmin()){button('核准並發 LINE 任務卡','send',()=>actOnNeed(item,'confirm_dispatch'),true);button('退回','undo-2',()=>actOnNeed(item,'decline_suggestion'));}
      else actions.insertAdjacentHTML('beforeend','<p class="muted">核准派遣僅限管理員，請聯絡管理員處理。</p>');
    }
    if(p.status==='open'&&p.need_type==='sos'){
      if(isAdmin())button('確認已處理','check-check',()=>actOnNeed(item,'resolve_sos'),true);
      else actions.insertAdjacentHTML('beforeend','<p class="muted">標記已處理僅限管理員。</p>');
    }
    if(p.status==='open'&&p.need_type!=='sos'){
      let proposeButton=null;
      if(isAdmin()){proposeButton=button('正在搜尋候選','user-round-search',()=>{},true);proposeButton.disabled=true;}
      button('完整分配試算','package-check',()=>{try{openAllocation();}catch(e){message(e.message,true);}});
      loadOperationCandidates(item,proposeButton);
    }
    if(['suggested','matched'].includes(p.status)&&isAdmin())button('傳訊息給志工','message-square-text',()=>messageAssignee(item));
    loadOperationEvents(item);
  }
}
const operationCandidates=new Map();
async function loadOperationCandidates(node,proposeButton=null){
  const key=node.id+':'+node.properties.version;
  try{
    if(!operationCandidates.has(key))operationCandidates.set(key,resourceRequest('/needs/'+encodeURIComponent(node.id.slice(8))+'/candidates'));
    const data=await operationCandidates.get(key);
    if(state.selected?.id!==node.id||!$('need-candidate-summary'))return;
    const top=(data.candidates||[])[0];
    if(!top){$('need-candidate-summary').innerHTML='<span class="error">目前沒有可派候選。請確認志工物資、分區、數量與位置。</span>';if(proposeButton){proposeButton.textContent='沒有可派候選';proposeButton.disabled=true;}return;}
    const notify=top.notify_channel==='auto'?'🏢 自動完成（不透過 LINE）'
      :top.notify_channel==='line'?'📱 會發 LINE 任務卡'
      :'⚠️ 未綁定 LINE，核准後需自行聯繫';
    $('need-candidate-summary').innerHTML=`系統建議 <strong>${escapeHtml(top.vol)}</strong>（${escapeHtml(top.name)}・評分 ${top.score}${top.dist_km!=null?'・'+top.dist_km+' km':''}）<br>${notify}`;
    if(proposeButton){
      if(top.source==='resource'){proposeButton.innerHTML='<i data-lucide="list-checks"></i>建立待核准建議';proposeButton.disabled=false;proposeButton.onclick=()=>proposeBest(node,top);icons();}
      else{proposeButton.textContent='候選為固定資源點';proposeButton.disabled=true;}
    }
  }catch(e){operationCandidates.delete(key);if(state.selected?.id===node.id&&$('need-candidate-summary'))$('need-candidate-summary').textContent=e.message;}
}
async function proposeBest(node,candidate){
  const body=`保留 <strong>${escapeHtml(candidate.vol)}</strong> 的「${escapeHtml(candidate.name)}」，核准後才發 LINE。`;
  if(!await askConfirm('建立派遣建議？',body,'建立建議'))return;
  const buttons=[...$('operational-actions').querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
  try{
    await resourceRequest('/needs/'+encodeURIComponent(node.id.slice(8))+'/propose_dispatch?resource_id='+encodeURIComponent(candidate.id),'POST');
    operationEvents.clear();operationCandidates.clear();await refreshOperations();operationStage='suggested';setCatalog('tasks');renderOperations();focusOperational(node.id);
    message('建議已建立；請核准後發送 LINE 任務卡');
  }catch(e){message(e.message,true);}finally{buttons.forEach(b=>b.disabled=false);}
}
async function resourceRequest(path,method='GET'){
  const r=await fetch('/api/resources'+path,{method});const data=await r.json();
  if(!r.ok||data.error)throw Error(data.error||data.detail||'操作失敗');return data;
}
async function loadOperationEvents(node){
  const key=node.id+':'+node.properties.version;
  try{
    if(!operationEvents.has(key))operationEvents.set(key,resourceRequest('/needs/'+encodeURIComponent(node.id.slice(8))+'/events'));
    const events=await operationEvents.get(key);
    if(state.selected?.id!==node.id||nodeById(node.id)?.properties.observed_at!==node.properties.observed_at||!$('operation-events'))return;
    const names={propose_dispatch:'建立建議',confirm_dispatch:'核准派遣',decline_suggestion:'退回建議',task_report:'現場回報',accept_task:'志工接單',mark_delivered:'配送完成',resolve_sos:'求助已處理',cancel_need:'取消需求',admin_message:'管理員指示'};
    $('operation-events').innerHTML=events.length?events.map(e=>`<div class="event-row"><strong>${escapeHtml(names[e.action]||e.action)}</strong><span>${escapeHtml(NEED_STATUS[e.new_status]||e.outcome)}</span>${e.details.note||e.details.text?`<p>${escapeHtml(e.details.note||e.details.text)}</p>`:''}<small>${escapeHtml(e.actor_label||'系統')} · ${escapeHtml(e.created_at)}</small></div>`).join(''):'尚無派遣紀錄';
  }catch(e){operationEvents.delete(key);if(state.selected?.id===node.id&&$('operation-events'))$('operation-events').textContent=e.message;}
}
async function messageAssignee(node){
  const body='<textarea id="assignee-message" rows="4" maxlength="500"></textarea>';
  if(!await askConfirm('傳訊息給志工',body,'傳送 LINE'))return;
  const text=($('assignee-message')?.value||'').trim();
  if(!text){message('沒有輸入內容，未傳送',true);return;}
  try{
    const r=await fetch('/api/resources/needs/'+encodeURIComponent(node.id.slice(8))+'/message_assignee',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
    const data=await r.json();if(!r.ok||data.error)throw Error(data.error||data.detail||'傳送失敗');
    operationEvents.clear();message(data.message);if(state.selected?.id===node.id)loadOperationEvents(node);
  }catch(e){message(e.message,true);}
}
async function actOnNeed(node,action){
  const titles={confirm_dispatch:'核准派遣？',decline_suggestion:'退回此建議？',resolve_sos:'確認已處理？'};
  const okLabels={confirm_dispatch:'核准並發 LINE',decline_suggestion:'退回',resolve_sos:'確認已處理'};
  let body={confirm_dispatch:'核准後會真正通知志工與需求者。',decline_suggestion:'物資會恢復可用，需求退回待媒合。',resolve_sos:'當事人會收到已處理的 LINE 通知。'}[action];
  if(action==='confirm_dispatch'){
    const cached=operationCandidates.get(node.id+':'+node.properties.version);
    const data=cached&&await cached.catch(()=>null);
    const top=data&&(data.candidates||[])[0];
    if(top)body=`將派給 <strong>${escapeHtml(top.vol)}</strong>（${escapeHtml(top.name)}），系統評分 ${top.score} 分${top.dist_km!=null?'，距離 '+top.dist_km+' km':''}。<br>核准後會真正通知志工與需求者。`;
  }
  if(!await askConfirm(titles[action]||'確認？',body,okLabels[action]))return;
  const buttons=[...$('operational-actions').querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
  try{const result=await resourceRequest('/needs/'+encodeURIComponent(node.id.slice(8))+'/'+action+'?expected_version='+encodeURIComponent(node.properties.version),'POST');operationEvents.clear();await refreshOperations();message(result.message||'操作完成');}
  catch(e){message(e.message,true);}finally{buttons.forEach(b=>b.disabled=false);}
}
function updateInventoryCount(){$('inventory-count').textContent=inventoryDrafts.size;inventoryReview=null;$('apply-inventory').disabled=true;}
async function editInventory(node){
  const p=node.properties,create=!node.id.startsWith('db:');
  const point=p.db==='point'||node.kind==='facility';
  if(create&&!p.inventory_key){checkpoint();p.inventory_key=crypto.randomUUID();changed();updateHistory();}
  if(create&&!point&&!operationOwners.length){try{const snapshot=await api('/operational-data');operationOwners=snapshot.owners;}catch(e){message(e.message,true);return;}}
  const staged=inventoryDrafts.get(node.id);
  const fallback=point
    ?{name:node.label,lat:node.lat,lng:node.lng,address:'',capacity:null,phone:'',operating_hours:''}
    :{name:node.label,quantity:node.quantity+'份',lat:node.lat,lng:node.lng,address:'',is_available:node.available};
  const values={...(p.base_values||fallback),...staged?.values};
  $('inventory-title').textContent=create?(point?'新增資源點':'登記物資'):(point?'更新資源點資料':'更新物資資料');
  $('inventory-fields').innerHTML=
    (create&&!point?`<label>提供者<select id="inventory-owner" required><option value="">選擇志工或管理員</option>${operationOwners.map(o=>`<option value="${escapeHtml(o.id)}" ${staged?.owner_id===o.id?'selected':''}>${escapeHtml(o.name)}</option>`).join('')}</select></label><label>品項<select id="inventory-type">${options({water:'飲用水',food:'食物',first_aid:'急救用品',shelter:'庇護所',vehicle:'交通工具',tool:'工具',other:'其他物資'},staged?.resource_type||'water')}</select></label>`:'')+
    (create&&point?`<label>資源點類型<select id="inventory-point-type">${options(POINT_TYPE_LABELS,staged?.point_type||'other')}</select></label>`:'')+
    `<label>名稱<input id="inventory-name" maxlength="200" required value="${escapeHtml(values.name)}"></label>`+
    (!point?`<label>數量與單位<input id="inventory-quantity" maxlength="80" required value="${escapeHtml(values.quantity)}"></label>`:'')+
    `<label>地址<input id="inventory-address" maxlength="500" value="${escapeHtml(values.address)}"></label>`+
    (!point?`<label class="checkbox-label"><input id="inventory-available" type="checkbox" ${values.is_available?'checked':''}>物資可用</label>`
      :`<div class="form-grid"><label>容量<input id="inventory-capacity" type="number" min="0" step="1" value="${values.capacity??''}"></label><label>電話<input id="inventory-phone" maxlength="40" value="${escapeHtml(values.phone||'')}"></label></div><label>開放時間<input id="inventory-hours" maxlength="100" value="${escapeHtml(values.operating_hours||'')}"></label>`);
  $('inventory-form').onsubmit=event=>{
    event.preventDefault();
    // 位置沿用這個節點現有的座標——表單不再要人手填小數點，改用拖曳地圖圖示。
    const next={name:$('inventory-name').value,lat:node.lat??null,lng:node.lng??null,
      address:$('inventory-address').value};
    if(point)Object.assign(next,{capacity:$('inventory-capacity').value===''?null:Number($('inventory-capacity').value),
      phone:$('inventory-phone').value,operating_hours:$('inventory-hours').value});
    else Object.assign(next,{quantity:$('inventory-quantity').value,is_available:$('inventory-available').checked});
    const changedValues=create?next:Object.fromEntries(Object.entries(next).filter(([k,v])=>p.base_values[k]!==v));
    if(Object.keys(changedValues).length)inventoryDrafts.set(node.id,{node_id:node.id,operation:create?'create':'update',
      expected_version:staged?.expected_version||p.version||null,values:changedValues,
      ...(create?(point?{creation_key:p.inventory_key,point_type:$('inventory-point-type').value}
                       :{creation_key:p.inventory_key,owner_id:$('inventory-owner').value,resource_type:$('inventory-type').value}):{})});
    else inventoryDrafts.delete(node.id);
    updateInventoryCount();$('inventory-dialog').close();message('已更新待寫回清單');
  };
  $('inventory-dialog').showModal();icons();
}
async function reviewInventory(){
  $('inventory-review-dialog').showModal();$('apply-inventory').disabled=true;inventoryReview=null;
  if(!inventoryDrafts.size){$('inventory-review-result').textContent='沒有待寫回的變更';return;}
  const command={changes:structuredClone([...inventoryDrafts.values()])};
  $('inventory-review-result').textContent='核對資料版本中';
  try{const result=await api('/database-diff',command);
    $('inventory-review-result').innerHTML=result.changes.map(c=>`<section class="inventory-change"><h3>${escapeHtml(c.label)}</h3>${c.operation==='already_applied'?'<p>已登記，將連回既有資料</p>':''}<table class="preview-table"><thead><tr><th>欄位</th><th>資料庫</th><th>變更後</th></tr></thead><tbody>${c.fields.map(f=>`<tr><td>${escapeHtml(INVENTORY_FIELDS[f.field]||f.field)}</td><td>${escapeHtml(f.before??'未填')}</td><td>${escapeHtml(f.after??'未填')}</td></tr>`).join('')}</tbody></table></section>`).join('')+result.conflicts.map(c=>`<p class="error">${escapeHtml(nodeById(c.node_id)?.label||c.node_id)}：${escapeHtml(c.reason)}</p>`).join('');
    inventoryReview=result.can_apply?command:null;$('apply-inventory').disabled=!result.can_apply;
  }catch(e){$('inventory-review-result').textContent=e.message;}
}
async function applyInventory(){
  if(!inventoryReview)return;const command=inventoryReview;inventoryReview=null;$('apply-inventory').disabled=true;
  try{const result=await api('/database-push',command);
    const aliases=new Map(result.applied.filter(r=>r.node_id!==r.database_id).map(r=>[r.node_id,r.database_id]));
    const existing=new Set(state.graph.nodes.filter(n=>!aliases.has(n.id)).map(n=>n.id));
    state.graph.nodes=state.graph.nodes.filter(n=>!aliases.has(n.id)||!existing.has(aliases.get(n.id)));
    state.graph.nodes.forEach(n=>{if(aliases.has(n.id)){n.id=aliases.get(n.id);n.logistics=[];}});
    state.graph.edges.forEach(e=>{e.source=aliases.get(e.source)||e.source;e.target=aliases.get(e.target)||e.target;});
    state.graph.edges=state.graph.edges.filter(e=>e.source!==e.target);
    if(state.selected&&aliases.has(state.selected.id))state.selected.id=aliases.get(state.selected.id);
    inventoryDrafts.clear();updateInventoryCount();$('inventory-review-dialog').close();
    changed();state.undo=[];state.redo=[];updateHistory();
    try{await refreshOperations();state.undo=[];state.redo=[];updateHistory();message('資料庫已更新，已留下異動紀錄');}catch(e){message('資料庫已寫入；現況讀取失敗，請按更新現況。'+e.message,true);}
  }catch(e){$('inventory-review-result').textContent=e.message;}
}
function initOperations(){
  $('add-object').onclick=event=>{
    event.stopPropagation();
    const menu=$('object-menu'),open=menu.hidden;
    menu.hidden=!open;$('add-object').setAttribute('aria-expanded',String(open));
    if(open)menu.querySelector('button').focus();
  };
  $('object-menu').querySelectorAll('[data-add-kind]').forEach(button=>button.onclick=event=>{
    event.stopPropagation();beginAddObject(button.dataset.addKind);
  });
  document.addEventListener('click',event=>{if(!event.target.closest('.object-add'))closeObjectMenu();});
  $('more-tools').onclick=()=>{
    const expanded=document.querySelector('.toolbar').classList.toggle('expanded');
    $('more-tools').setAttribute('aria-expanded',String(expanded));
    $('more-tools').setAttribute('aria-label',expanded?'收合進階工具':'開啟進階工具');
    $('more-tools').title=expanded?'收合進階工具':'開啟進階工具';
    map.invalidateSize();if(cy)cy.resize();
  };
  $('catalog-objects').onclick=()=>{setCatalog('objects');renderOperations();};$('catalog-tasks').onclick=()=>{setCatalog('tasks');renderOperations();};$('catalog-resources').onclick=()=>{setCatalog('resources');renderOperations();};
  run('review-inventory',reviewInventory);run('apply-inventory',applyInventory);
  $('close-inventory').onclick=()=>$('inventory-dialog').close();$('close-inventory-review').onclick=()=>$('inventory-review-dialog').close();
  $('discard-inventory').onclick=()=>{inventoryDrafts.clear();updateInventoryCount();$('inventory-review-dialog').close();};
  window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==parent)return;if(event.data?.workspaceVisible){map.invalidateSize();if(cy)cy.resize();if(state.live&&!state.dirty&&!inventoryDrafts.size)refreshOperations({silent:true}).catch(e=>message(e.message,true));}if(event.data?.operationStage in OPERATION_STAGES){operationStage=event.data.operationStage;setCatalog('tasks');renderOperations();}});
}
