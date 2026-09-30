'use strict';
const NEED_STATUS=window.NEED_STATUS_LABEL;   // 單一來源見 /static/labels.js
const OPERATION_STAGES={sos:'緊急求救',...NEED_STATUS};
const CHECKIN_STATUS={ok:'平安',safe:'平安',pending:'待回應',no_response:'未回應',help_needed:'需要協助',confirmed:'已確認'};
const ROLE_NAMES={elderly:'長者',volunteer:'志工',family:'家屬',admin:'管理員'};
const INVENTORY_FIELDS={name:'名稱',quantity:'數量／單位',lat:'位置',lng:'位置',address:'地址',is_available:'可用',
  capacity:'容量',phone:'電話',operating_hours:'開放時間'};
const POINT_TYPE_LABELS={shelter:'避難收容所',community:'里民活動中心',hospital:'醫療院所',fire_station:'消防分隊',
  police:'警察局/派出所',store:'物資分發點',warehouse:'物資倉庫',clinic:'衛生所',government:'公所／政府機關',other:'其他'};
const CARE_RELATIONS={family:'家屬',volunteer:'志工',neighbor:'鄰居',other:'聯絡人'};
// 求救通報後幾分鐘還沒處理要變色：5 分鐘黃、10 分鐘紅
const SOS_LATE_MIN=5,SOS_OVERDUE_MIN=10;
const isSos=n=>n.properties.need_type==='sos';
function minutesSince(iso){return iso?(Date.now()-new Date(iso))/60000:0;}
function sinceText(iso){return iso&&window.sosAlarm?sosAlarm.since(iso):'';}
// 24 小時制；不是今天就加上日期（昨晚 20:12 跟今天 20:12 不能看起來一樣）
function reportedText(iso){if(!iso)return'未知';const d=new Date(iso),pad=n=>String(n).padStart(2,'0');
  const day=d.toDateString()===new Date().toDateString()?'':`${d.getMonth()+1}/${d.getDate()} `;
  return`${day}${pad(d.getHours())}:${pad(d.getMinutes())}（${sinceText(iso)}）`;}
// 沒人受理：5 分鐘黃、10 分鐘紅；有人受理但 20 分鐘還沒到場：黃
const SOS_ARRIVAL_MIN=20;
function ageClass(n){const p=n.properties;if(!isSos(n)||p.status!=='open')return'';
  if(p.responder)return !p.on_scene_at&&minutesSince(p.acknowledged_at)>=SOS_ARRIVAL_MIN?'late':'';
  const m=minutesSince(p.reported_at);return m>=SOS_OVERDUE_MIN?'overdue':m>=SOS_LATE_MIN?'late':'';}
function openSos(){return operationalNodes().filter(n=>isSos(n)&&n.properties.status==='open');}
function showSos(id){operationStage='sos';setCatalog('tasks');renderOperations();focusOperational(id);}
const inventoryDrafts=new Map(), operationEvents=new Map();
let operationOwners=[], operationRequest=null, operationStage='open', catalogTab='objects', inventoryReview=null;

function workspaceUrl(id){const url=new URL(location.href);id?url.searchParams.set('id',id):url.searchParams.delete('id');history.replaceState(null,'',url.pathname+url.search+url.hash);}
// 求救要看得到在哪條街：拉近到街道層級；其他物件只平移，不打斷調度者目前的比例尺
function focusOperational(id){const node=nodeById(id);if(!node)return;$('mode').value='select';state.hidden.delete(node.kind);select('node',id);if(node.lat!==null){if(node.properties?.need_type==='sos')map.setView([node.lat,node.lng],Math.max(map.getZoom(),16));else map.panTo([node.lat,node.lng]);}if(cy)cy.center(cy.getElementById('node:'+id));}
function operationalNodes(){return state.graph.nodes.filter(n=>n.properties.db==='need');}
function operationalResources(){return state.graph.nodes.filter(n=>['resource','point'].includes(n.properties.db));}
function taskInStage(node,stage){
  if(stage==='sos')return node.properties.status==='open'&&node.properties.need_type==='sos';
  if(stage==='open')return node.properties.status==='open'&&node.properties.need_type!=='sos';
  return node.properties.status===stage;
}
function setCatalog(tab){
  catalogTab=tab;$('objects').hidden=tab!=='objects';$('operation-tasks').hidden=tab!=='tasks';$('operation-resources').hidden=tab!=='resources';$('operation-rollcall').hidden=tab!=='rollcall';
  for(const key of ['objects','tasks','resources','rollcall'])$('catalog-'+key).classList.toggle('active',tab===key);
  $('catalog-title').textContent={objects:'物件',tasks:'需求與任務',resources:'物資回報',rollcall:'災時點名'}[tab];renderObjects();
  if(tab==='rollcall')loadRollcall();
}
function renderOperations(){
  const tasks=operationalNodes();
  $('operation-stages').innerHTML=Object.entries(OPERATION_STAGES).map(([key,label])=>`<button data-stage="${key}" class="${key==='sos'?'sos-stage ':''}${operationStage===key&&catalogTab==='tasks'?'active':''}" aria-pressed="${operationStage===key&&catalogTab==='tasks'}">${label}<strong>${tasks.filter(n=>taskInStage(n,key)).length}</strong></button>`).join('');
  $('operation-stages').querySelectorAll('button').forEach(button=>button.onclick=()=>{operationStage=button.dataset.stage;setCatalog('tasks');renderOperations();});
  const stamp=state.graph.nodes.find(n=>n.properties.observed_at)?.properties.observed_at;
  $('observed-at').textContent=stamp?'現況快照 · '+new Date(stamp).toLocaleString('zh-TW',{hourCycle:'h23'}):'尚未讀取平台現況';
  renderOperationTasks();
}
function renderOperationTasks(){
  const query=$('search').value.toLowerCase();
  // 求救先處理等最久的；其他需求依優先級，同級再看誰先通報
  const byAge=(a,b)=>String(a.properties.reported_at||'').localeCompare(String(b.properties.reported_at||''));
  // 求救：沒人受理 → 已受理在路上 → 已到場，同一組裡等最久的在前
  const sosRank=n=>!n.properties.responder?0:n.properties.on_scene_at?2:1;
  const tasks=operationalNodes().filter(n=>taskInStage(n,operationStage)&&(n.label+' '+n.id+' '+n.properties.description).toLowerCase().includes(query)).sort((a,b)=>operationStage==='sos'?(sosRank(a)-sosRank(b))||byAge(a,b):(b.properties.urgency-a.properties.urgency)||byAge(a,b));
  $('operation-tasks').innerHTML=tasks.map(n=>{
    const sos=isSos(n),where=n.lat===null?'位置未知':(n.properties.address||n.properties.location_source),since=sinceText(n.properties.reported_at);
    const meta=sos?[n.properties.responder?(n.properties.on_scene_at?'已到場：':'處理中：')+n.properties.responder:'未受理',since&&'通報 '+since,where]:[n.properties.quantity_text||'數量未填',where,since];
    return `<button data-task="${escapeHtml(n.id)}" class="${state.selected?.id===n.id?'selected':''} ${ageClass(n)}"><span class="task-priority ${sos?'urgent':''}">${sos?'SOS':'P'+n.properties.urgency}</span><span class="task-text">${escapeHtml(n.label)}<small>${escapeHtml(meta.filter(Boolean).join(' · '))}</small></span></button>`;
  }).join('')||'<p class="muted">此狀態沒有需求</p>';
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
  for(const need of needs)edges.push(edge(nearest(need)[0],need,need.properties.need_type==='sos'?'緊急求救':'事件需求'));
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
    loadRollcall();
    // 單獨開啟工作區時自己響鈴；嵌在外框裡由外框響，避免同一筆響兩次
    if(!document.documentElement.classList.contains('embed')&&window.sosAlarm)
      sosAlarm.check(openSos().filter(n=>!n.properties.responder).map(n=>({id:n.id,name:n.label.split(' · ')[0],address:n.properties.address,reported_at:n.properties.reported_at})),item=>showSos(item.id));
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
  const rc=isNode?rollcallOf(item):null;
  if(rc)fields.push(['災時點名',rc.status_label+(rc.marked_by?'（'+rc.marked_by+'確認）':'')]);
  if(p.db==='user')fields.push(['角色',(p.roles||[]).map(r=>ROLE_NAMES[r]||r).join('、')],['最近打卡',p.checkin?(CHECKIN_STATUS[p.checkin.status]||p.checkin.status)+' · '+p.checkin.date:'尚無紀錄'],['打卡回覆',p.checkin?.note||'未提供'],['未解除警報',p.active_alerts],['脆弱度',p.vulnerability??'未評估']);
  if(p.db==='resource')fields.push(['擁有者',p.owner],['登記數量',p.quantity_text||'未知'],['可用狀態',item.available?'可用':'保留中或不可用']);
  if(p.db==='point')fields.push(['收容容量',p.capacity??'未知'],['目前人數',p.current_load??'未知'],['庫存','未提供'],
    ['電話',p.base_values?.phone||'未提供'],['開放時間',p.base_values?.operating_hours||'未提供']);
  // 求救不是物資需求：不顯示數量、優先級，狀態說「待處理」而不是「待媒合」
  if(p.db==='need'&&p.need_type==='sos')fields.push(['狀態',p.status==='open'?(p.responder?'處理中':'待受理'):(NEED_STATUS[p.status]||p.status)],
    ['處理人',p.responder?p.responder+'，'+reportedText(p.acknowledged_at)+'受理':'尚未有人受理'],
    ...(p.on_scene_at?[['到場',reportedText(p.on_scene_at)]]:p.responder?[['到場','還在路上']]:[]),
    ...(p.nearest_aed?[['AED',p.nearest_aed.replace(/^最近的 AED：/,'')]]:[]),['通報時間',reportedText(p.reported_at)],['狀況',p.description||'未說明'],['定位依據',p.location_source]);
  else if(p.db==='need')fields.push(['狀態',NEED_STATUS[p.status]||p.status],['通報時間',reportedText(p.reported_at)],['優先級',p.urgency],['登記數量',p.quantity_text||'未知'],['需求',p.description||'未填'],['定位依據',p.location_source]);
  // 原始經緯度是系統內部表示（而且會露出浮點誤差），摘要只講定位結果；
  // 要精確數值的人是在編輯，那邊本來就有緯度／經度欄位。
  if(isNode)fields.push(['地址',p.address||p.base_values?.address||'未提供'],['地圖定位',item.lat===null?'未定位':'已定位']);
  // 求助者的狀況放在同一個面板，處理時不用切頁找
  const who=p.db==='need'?nodeById(p.requester_id)?.properties:null;
  const profile=who?[['脆弱度',who.vulnerability??'未評估'],['最近打卡',who.checkin?(CHECKIN_STATUS[who.checkin.status]||who.checkin.status)+' · '+who.checkin.date:'尚無紀錄'],['未解除警報',who.active_alerts]]:[];
  const related=isNode?state.graph.edges.filter(e=>e.id.startsWith('db:edge:')&&(e.source===item.id||e.target===item.id)):[];
  el.innerHTML=`<div class="object-heading"><strong>${escapeHtml(item.label)}</strong><span class="source-label">${escapeHtml(isNode?item.source:item.provenance)}</span></div><dl class="object-facts">${fields.map(([k,v])=>`<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('')}</dl>${p.quantity_verified===false&&p.need_type!=='sos'?'<p class="error">數量或單位待確認，未納入分配試算。</p>':''}
    ${p.db==='need'?'<div id="need-contacts" class="contact-list"></div>':''}
    ${p.db==='need'&&p.need_type!=='sos'&&['open','suggested'].includes(p.status)?'<div id="need-candidate-summary" class="candidate-summary muted">候選載入中…</div>':''}
    <div id="operational-actions" class="actions"></div>${profile.length?'<h2>求助者狀況</h2><dl class="object-facts">'+profile.map(([k,v])=>`<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('')+'</dl>':''}${related.length?'<h2>關聯物件</h2><div class="related-objects">'+related.map(e=>{const other=e.source===item.id?e.target:e.source;return `<button data-related="${escapeHtml(other)}"><span>${escapeHtml(e.label)}</span>${escapeHtml(nodeById(other)?.label||other)}</button>`;}).join('')+'</div>':''}
    ${p.db==='need'?'<h2 class="event-heading">任務紀錄</h2><div id="operation-events" class="muted">讀取中</div>':''}<p class="muted">${p.observed_at?escapeHtml(new Date(p.observed_at).toLocaleString('zh-TW',{hourCycle:'h23'})):''}</p><details><summary>來源識別碼</summary><pre>${escapeHtml(item.id)}</pre></details>`;
  el.querySelectorAll('[data-related]').forEach(b=>b.onclick=()=>focusOperational(b.dataset.related));
  const actions=$('operational-actions');
  function button(label,icon,fn,primary=false){const b=document.createElement('button');b.type='button';b.className=primary?'primary':'';b.innerHTML=`<i data-lucide="${icon}"></i>${label}`;b.onclick=fn;actions.append(b);return b;}
  if(p.db==='resource'||p.db==='point')button('編輯正式資料','pencil',()=>editInventory(item));
  if(rc&&rc.status!=='ok'){if(rc.phone){const a=document.createElement('a');a.className='icon-button';a.href='tel:'+rc.phone.replace(/[^0-9+]/g,'');a.innerHTML='<i data-lucide="phone"></i>';a.append('撥打 '+rc.phone);actions.append(a);}
    button('標記平安','shield-check',e=>markRollcall(rc.id,e.currentTarget),true);
    if(rc.status!=='help')button('需要協助','siren',e=>markRollcall(rc.id,e.currentTarget,'help'));icons();}
  if(p.db==='need'){
    if(p.status==='suggested'){
      loadOperationCandidates(item);
      if(isAdmin()){button('核准並發 LINE 任務卡','send',()=>actOnNeed(item,'confirm_dispatch'),true);button('退回','undo-2',()=>actOnNeed(item,'decline_suggestion'));}
      else actions.insertAdjacentHTML('beforeend','<p class="muted">核准派遣僅限管理員，請聯絡管理員處理。</p>');
    }
    if(p.status==='open'&&p.need_type==='sos'){
      // 沒人受理：指派；有人受理但還沒到場：可以改派（被耽擱、聯絡不上時）
      if(isAdmin()){if(!p.responder)loadSosCandidates(item);else if(!p.on_scene_at)loadSosCandidates(item,true);
        // 需要送醫就撥 119，同時在紀錄上留下轉報的時間
        const call=document.createElement('a');call.className='icon-button call-119';call.href='tel:119';call.innerHTML='<i data-lucide="siren"></i>通報 119';
        call.onclick=()=>{fetch('/api/resources/needs/'+encodeURIComponent(item.id.slice(8))+'/reported_119',{method:'POST'}).then(()=>{operationEvents.clear();loadOperationEvents(item);}).catch(()=>{});};
        actions.append(call);
        button('確認已處理','check-check',()=>actOnNeed(item,'resolve_sos'),!!p.responder);}
      else actions.insertAdjacentHTML('beforeend','<p class="muted">受理與結案僅限管理員。</p>');
    }
    if(p.status==='open'&&p.need_type!=='sos'){
      let proposeButton=null;
      if(isAdmin()){proposeButton=button('正在搜尋候選','user-round-search',()=>{},true);proposeButton.disabled=true;}
      button('完整分配試算','package-check',()=>{try{openAllocation();}catch(e){message(e.message,true);}});
      loadOperationCandidates(item,proposeButton);
    }
    if(['suggested','matched'].includes(p.status)&&isAdmin())button('傳訊息給志工','message-square-text',()=>messageAssignee(item));
    if(p.need_type==='sos'&&p.status==='open'&&p.responder&&isAdmin())button('傳訊息給處理人','message-square-text',()=>messageAssignee(item));
    if(['open','suggested','matched'].includes(p.status)&&isAdmin())loadNeedContact(item);
    loadOperationEvents(item);
  }
}
// 電話只在管理員點開時讀取、只放在畫面上，不寫進工作區快照或匯出檔。
// 求助者本人在前，家屬照通知順序排；求救時第一件事就是打電話。
async function loadNeedContact(node){
  try{
    const c=await resourceRequest('/needs/'+encodeURIComponent(node.id.slice(8))+'/contact');
    const box=$('need-contacts');
    if(state.selected?.id!==node.id||!box)return;
    const people=[{name:c.name||'求助者',relation:'本人',phone:c.phone}].concat(c.contacts||[]);
    const links=people.map(x=>{const digits=(x.phone||'').replace(/[^0-9+]/g,'');if(digits.length<7)return'';
      return `<a class="icon-button call" href="tel:${escapeHtml(digits)}"><i data-lucide="phone"></i><span>${escapeHtml(x.name)}<small>${escapeHtml(CARE_RELATIONS[x.relation]||x.relation||'聯絡人')} · ${escapeHtml(x.phone)}</small></span></a>`;}).filter(Boolean);
    box.innerHTML=links.length?'<h2>聯絡</h2>'+links.join(''):(isSos(node)?'<p class="error">求助者與家屬都沒有登記電話，請派人到場確認。</p>':'');
    icons();
  }catch(e){/* 讀不到就不顯示 */}
}
// 紀錄裡的操作者是系統內部代號（manager、admin:王小明），畫面上說人話
function actorName(label){if(!label||/^system:/.test(label))return '系統';if(label==='manager'||/^workspace:/.test(label))return '後台';if(/^volunteer:/.test(label))return '志工';if(label==='rehearsal')return '演練';return label.replace(/^admin:/,'管理員：').replace(/^(志工|管理員|家屬):/,'$1：');}
// 求救還沒人受理：列出可以指派的人（志工由近到遠，再來是管理員），指派後對方會收到 LINE 任務卡
async function loadSosCandidates(node,replace=false){
  const actions=$('operational-actions');
  const box=document.createElement('div');box.className='assign-sos';box.innerHTML='<span class="muted">讀取可指派的人…</span>';
  actions.prepend(box);
  try{
    const data=await resourceRequest('/needs/'+encodeURIComponent(node.id.slice(8))+'/sos_candidates');
    if(state.selected?.id!==node.id||!box.isConnected)return;
    const people=(data.candidates||[]).filter(c=>!replace||c.name!==node.properties.responder);
    if(!people.length){box.innerHTML='<p class="error">沒有可指派的志工或管理員，請直接撥電話或通報 119。</p>';return;}
    box.innerHTML=`<label>${replace?'改派給（原處理人被耽擱時）':'指派處理人'}<select id="sos-assignee">${people.map(c=>`<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}・${escapeHtml(c.role)}${c.km!=null?'・'+c.km+' km':''}${c.line?'':'・未綁 LINE'}${c.paused?'・暫停支援中':''}</option>`).join('')}</select></label>`;
    const go=document.createElement('button');go.type='button';go.className=replace?'':'primary';go.innerHTML=replace?'<i data-lucide="repeat"></i>改派':'<i data-lucide="user-round-check"></i>指派';
    go.onclick=()=>assignSos(node,$('sos-assignee').value,$('sos-assignee').selectedOptions[0].textContent,replace);
    box.append(go);icons();
  }catch(e){if(box.isConnected)box.innerHTML=`<p class="error">${escapeHtml(e.message)}</p>`;}
}
async function assignSos(node,userId,label,replace=false){
  const title=replace?'改派處理人？':'指派處理人？';
  const body=replace?`改由 <strong>${escapeHtml(label)}</strong> 處理。原處理人 ${escapeHtml(node.properties.responder||'')} 會收到「不用再過去了」。`
    :`由 <strong>${escapeHtml(label)}</strong> 處理這筆求救。對方綁了 LINE 會收到任務卡（電話、導航、處理完成）。`;
  if(!await askConfirm(title,body,replace?'改派':'指派'))return;
  try{
    const r=await fetch('/api/resources/needs/'+encodeURIComponent(node.id.slice(8))+'/assign_sos?user_id='+encodeURIComponent(userId)+(replace?'&replace=true':''),{method:'POST'});
    const data=await r.json();if(!r.ok||data.error)throw Error(data.error||data.detail||'指派失敗');
    operationEvents.clear();await refreshOperations({silent:true});focusOperational(node.id);message(data.message);
  }catch(e){message(e.message,true);}
}
// ── 災時點名：緊急模式時誰平安、誰需要協助、誰還沒回；還沒回的依脆弱度排序，給志工照順序上門 ──
let rollcallData=null,rollcallById=new Map(),rollcallFilter='';
// 地圖與面板用：這位長者這一輪點名的狀態（沒有點名時是 null）
function rollcallOf(node){return rollcallData?.active&&node?.properties?.db==='user'?rollcallById.get(node.id.replace('db:person:',''))||null:null;}
const ROLLCALL_CLASS={help:'urgent',pending:'pending',unwell:'warn',helped:'ok',ok:'ok'};
async function loadRollcall(){
  const before=JSON.stringify([...rollcallById].map(([k,v])=>[k,v.status]));
  try{const r=await fetch('/api/rollcall');rollcallData=r.ok?await r.json():null;}catch(e){rollcallData=null;}
  rollcallById=new Map((rollcallData?.people||[]).map(p=>[p.id,p]));
  renderRollcall();
  // 點名狀態有變才重畫地圖，免得每 30 秒整張地圖閃一次
  if(before!==JSON.stringify([...rollcallById].map(([k,v])=>[k,v.status]))){renderCanvas();if(state.selected?.type==='node')renderSelection();}
}
function renderRollcall(){
  const box=$('operation-rollcall'),badge=$('rollcall-count'),d=rollcallData;
  const waiting=d?.active?d.counts.pending+d.counts.help:0;
  badge.hidden=!waiting;badge.textContent=waiting||'';
  if(catalogTab!=='rollcall')return;
  if(!d){box.innerHTML='<p class="muted">讀不到點名資料</p>';$('object-count').textContent='';return;}
  if(!d.active){box.innerHTML='<p class="muted">日常模式沒有點名。啟動緊急模式時，系統會自動用 LINE 問每位長者是否平安，回報結果會出現在這裡。</p>';$('object-count').textContent='';return;}
  $('object-count').textContent=d.total+' 位長者';
  const c=d.counts;
  // 人數格子兼篩選：點一下只看那一類，再點一次看全部；左上的搜尋也會過濾姓名與地址
  const chip=(key,cls,label)=>`<button type="button" class="rc ${cls}${rollcallFilter===key?' on':''}" data-rc-filter="${key}" aria-pressed="${rollcallFilter===key}">${label} <b>${c[key]}</b></button>`;
  const query=$('search').value.trim().toLowerCase();
  const shown=d.people.filter(p=>(!rollcallFilter||p.status===rollcallFilter)&&(!query||(p.name+' '+p.address).toLowerCase().includes(query)));
  box.innerHTML=`<p class="muted rollcall-since">點名開始於 ${escapeHtml(reportedText(d.started_at))}</p><div class="rollcall-summary">${chip('ok','ok','平安')}${chip('unwell','warn','不舒服')}${chip('help','urgent','需要協助')}${chip('pending','pending','還沒回')}${c.helped?chip('helped','ok wide','已協助（求救已結案）'):''}</div>
    <div class="rollcall-actions"><button id="rollcall-remind" type="button"${c.pending?'':' disabled'}><i data-lucide="bell-ring"></i>再問一次還沒回的人</button><a class="icon-button" href="/api/rollcall/export.csv" download title="匯出點名名單（Excel 可開）"><i data-lucide="download"></i>匯出 CSV</a></div>
    <div class="rollcall-list">${shown.length?'':'<p class="muted">這個條件下沒有人</p>'}${shown.map(p=>{
      const meta=[p.status==='pending'?'脆弱度 '+p.vulnerability:(p.marked_by?p.marked_by+'確認':'本人回報')+(p.responded_at?' · '+sinceText(p.responded_at):''),p.address||'地址未填',p.line?'':'未綁 LINE'].filter(Boolean).join(' · ');
      const call=p.phone?`<a class="icon-button" href="tel:${escapeHtml(p.phone.replace(/[^0-9+]/g,''))}" title="撥打 ${escapeHtml(p.phone)}" aria-label="撥打 ${escapeHtml(p.name)}"><i data-lucide="phone"></i></a>`:'';
      const mark=p.status==='ok'?'':`<button type="button" data-rc-ok="${escapeHtml(p.id)}" title="電話或上門確認後標記平安">標記平安</button>`;
      return `<div class="rollcall-row"><button type="button" class="rc-person" data-rc-focus="${escapeHtml(p.id)}"><span class="rc-chip ${ROLLCALL_CLASS[p.status]}">${escapeHtml(p.status_label)}</span><span class="task-text">${escapeHtml(p.name)}<small>${escapeHtml(meta)}</small></span></button>${call}${mark}</div>`;}).join('')}</div>`;
  box.querySelectorAll('[data-rc-filter]').forEach(b=>b.onclick=()=>{rollcallFilter=rollcallFilter===b.dataset.rcFilter?'':b.dataset.rcFilter;renderRollcall();});
  box.querySelectorAll('[data-rc-focus]').forEach(b=>b.onclick=()=>focusOperational('db:person:'+b.dataset.rcFocus));
  box.querySelectorAll('[data-rc-ok]').forEach(b=>b.onclick=()=>markRollcall(b.dataset.rcOk,b));
  const remind=$('rollcall-remind');if(remind)remind.onclick=remindRollcall;
  icons();
}
async function markRollcall(id,button,status='ok'){
  const person=rollcallData?.people.find(p=>p.id===id),name=escapeHtml(person?.name||'');
  const ok=status==='ok'?await askConfirm('標記平安？',`已經電話或上門確認 <strong>${name}</strong> 平安了嗎？`,'標記平安')
    :await askConfirm('需要協助？',`<strong>${name}</strong> 需要協助：會開一張求救單，通知附近志工，後台也會響鈴。`,'開求救單');
  if(!ok)return;
  button.disabled=true;
  try{const r=await fetch('/api/rollcall/'+encodeURIComponent(id)+'?status='+status,{method:'POST'});const data=await r.json();if(!r.ok)throw Error(data.detail||'標記失敗');message(data.message);await loadRollcall();if(status==='help')await refreshOperations({silent:true});}
  catch(e){message(e.message,true);button.disabled=false;}
}
async function remindRollcall(){
  if(!await askConfirm('再問一次？','用 LINE 再問一次還沒回報的長者是否平安。','傳送'))return;
  try{const r=await fetch('/api/rollcall/remind',{method:'POST'});const data=await r.json();if(!r.ok)throw Error(data.detail||'傳送失敗');message(data.message);}
  catch(e){message(e.message,true);}
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
    const names={propose_dispatch:'建立建議',confirm_dispatch:'核准派遣',decline_suggestion:'退回建議',task_report:'現場回報',accept_task:'志工接單',mark_delivered:'配送完成',resolve_sos:'求助已處理',sos_resolved:'求救結案',sos_acknowledged:'受理求救',sos_escalated:'逾時未受理，已再通知管理員',sos_reported_119:'已轉報 119',sos_on_scene:'處理人到場',sos_cancelled:'當事人取消求救',sos_reassigned:'改派處理人',sos_arrival_overdue:'受理後逾時未到場，已提醒管理員',welfare_check_requested:'家屬請人探視',cancel_need:'取消需求',admin_message:'管理員指示'};
    // 求救事件不走物資狀態（受理後仍是 open，顯示「待媒合」會誤導）；時間轉成台灣時間
    const when=t=>{const d=new Date(String(t||'').replace(' ','T')+(/[zZ]|[+-]\d\d:?\d\d$/.test(t||'')?'':'Z'));return isNaN(d)?(t||''):d.toLocaleString('zh-TW',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});};
    $('operation-events').innerHTML=events.length?events.map(e=>`<div class="event-row"><strong>${escapeHtml(names[e.action]||e.action)}</strong><span>${e.action.startsWith('sos_')?'':escapeHtml(NEED_STATUS[e.new_status]||e.outcome)}</span>${e.details.note||e.details.text?`<p>${escapeHtml(e.details.note||e.details.text)}</p>`:''}<small>${escapeHtml(actorName(e.actor_label))} · ${escapeHtml(when(e.created_at))}</small></div>`).join(''):'尚無處理紀錄';
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
  $('catalog-objects').onclick=()=>{setCatalog('objects');renderOperations();};$('catalog-tasks').onclick=()=>{setCatalog('tasks');renderOperations();};$('catalog-resources').onclick=()=>{setCatalog('resources');renderOperations();};$('catalog-rollcall').onclick=()=>{setCatalog('rollcall');renderOperations();};
  run('review-inventory',reviewInventory);run('apply-inventory',applyInventory);
  $('close-inventory').onclick=()=>$('inventory-dialog').close();$('close-inventory-review').onclick=()=>$('inventory-review-dialog').close();
  $('discard-inventory').onclick=()=>{inventoryDrafts.clear();updateInventoryCount();$('inventory-review-dialog').close();};
  window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==parent)return;if(event.data?.workspaceVisible){map.invalidateSize();if(cy)cy.resize();if(state.live&&!state.dirty&&!inventoryDrafts.size)refreshOperations({silent:true}).catch(e=>message(e.message,true));}if(event.data?.operationStage in OPERATION_STAGES){operationStage=event.data.operationStage;setCatalog('tasks');renderOperations();}
    // 外框的求救橫幅按「查看」：先更新現況（新求救可能還不在畫面上），再跳到那一筆
    if(event.data?.focusNeed){const id=event.data.focusNeed;(state.live&&!state.dirty&&!inventoryDrafts.size?refreshOperations({silent:true}):Promise.resolve()).catch(()=>{}).then(()=>{if(nodeById(id))showSos(id);});}});
  // 「幾分鐘前」與逾時顏色每 30 秒重畫一次，資料沒變也要走
  setInterval(()=>{if(catalogTab==='tasks')renderOperationTasks();},30000);
}
