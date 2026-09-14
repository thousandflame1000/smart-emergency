'use strict';
let comparisonRequest=0;
const COMPARISON_FIELDS={label:'名稱',kind:'類型',lat:'緯度',lng:'經度',quantity:'數量',available:'納入分析',source:'來源／起點',target:'終點',status:'道路狀態',directed:'單行方向',speed_kph:'路速',multiplier:'緩行倍率',provenance:'資料來源',properties:'原始屬性',logistics:'品項庫存與需求'};
function currentBaseline(){return {name:$('workspace-name').value.trim()||'未命名工作區',graph:structuredClone(state.graph),captured_at:new Date().toISOString(),workspace_id:state.id||null,revision:state.revision||0,unsaved:state.dirty};}
function comparisonMessage(text,error=false){$('comparison-status').textContent=text;$('comparison-status').classList.toggle('error',error);}
function invalidateComparison(){comparisonRequest++;state.comparison=null;state.comparisonInputs=null;$('export-comparison').disabled=true;$('run-comparison').disabled=false;$('comparison-result').replaceChildren();comparisonMessage('目前變更尚未比較');}
function renderBaseline(){
  const b=state.baseline;$('baseline-name').textContent=b?.name||'尚未設定';
  $('baseline-time').textContent=b?`${new Date(b.captured_at).toLocaleString('zh-TW')} · 來源版本 ${b.revision}${b.unsaved?' · 含未儲存變更':''}${b.workspace_id?'':' · 未儲存來源'}`:'';
}
function comparisonOptions(value){
  const nodes=new Map((state.baseline?.graph.nodes||[]).map(n=>[n.id,n]));state.graph.nodes.forEach(n=>nodes.set(n.id,n));
  return '<option value="">不比較路徑</option>'+[...nodes.values()].map(n=>`<option value="${escapeHtml(n.id)}" ${value===n.id?'selected':''}>${escapeHtml(n.label)} (${TYPES[n.kind]})</option>`).join('');
}
async function openComparison(){
  if(!state.graph.nodes.length&&!state.baseline)throw Error('請先載入資料');
  if(!state.baseline){state.baseline=currentBaseline();changed();}
  renderBaseline();
  for(const suffix of ['start','end']){$('compare-'+suffix).innerHTML=comparisonOptions($('route-'+suffix).value);}
  $('comparison-dialog').showModal();await runComparison();
}
async function runComparison(){
  if(!state.baseline)return;
  const token=++comparisonRequest,version=state.editVersion;
  const inputs={baseline:structuredClone(state.baseline),scenario:{name:$('workspace-name').value,workspace_id:state.id,revision:state.revision,unsaved:state.dirty,graph:structuredClone(state.graph)},start:$('compare-start').value||null,end:$('compare-end').value||null};
  state.comparison=null;state.comparisonInputs=null;$('export-comparison').disabled=true;$('comparison-result').replaceChildren();$('run-comparison').disabled=true;comparisonMessage('正在比較兩份拓樸快照…');
  try{
    const result=await api('/compare',{baseline:inputs.baseline.graph,graph:inputs.scenario.graph,start:inputs.start,end:inputs.end});
    if(token!==comparisonRequest||version!==state.editVersion)return;
    state.comparison=result;state.comparisonInputs=inputs;renderComparison(result);$('export-comparison').disabled=false;comparisonMessage('比較完成 · 僅供情境試算，未執行派遣');
  }catch(e){if(token===comparisonRequest)comparisonMessage(e.message,true);}finally{if(token===comparisonRequest)$('run-comparison').disabled=false;}
}
function deltaText(value){return value>0?'+'+value:String(value);}
function comparisonFocus(type,id){
  const item=type==='node'?nodeById(id):state.graph.edges.find(e=>e.id===id);if(!item)return;
  $('comparison-dialog').close();$('mode').value='select';state.connect=null;
  if(type==='node')state.hidden.delete(item.kind);else {state.hidden.delete(nodeById(item.source).kind);state.hidden.delete(nodeById(item.target).kind);}
  state.selected={type,id};render();
  const node=type==='node'?item:nodeById(item.source);if(state.view==='map'&&node.lat!==null)map.panTo([node.lat,node.lng]);
  if(state.view==='graph'&&cy)cy.center(cy.getElementById(type+':'+id));
}
function renderComparison(result){
  const metricNames={nodes:'物件',edges:'連線',people:'納入分析的人員紀錄',road_components:'路網分區',critical_roads:'橋接瓶頸',unreachable_people:'物資不可達人員紀錄'};
  const table=(head,rows)=>`<div class="comparison-table-wrap"><table class="comparison-table"><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
  let html=table(['指標','基準','目前','差異'],Object.entries(metricNames).map(([key,label])=>`<tr><th>${label}</th><td>${result.before.metrics[key]}</td><td>${result.after.metrics[key]}</td><td>${deltaText(result.metric_deltas[key])}</td></tr>`).join(''));
  if(result.route_delta){
    const labels={lost:'路徑中斷',restored:'恢復可達',unavailable:'兩份情境皆無可達路徑',unchanged:'路徑未變',changed:'路徑或時間已變更'};
    const routeText=r=>r.reachable?`${r.km} 公里 / 約 ${r.minutes} 分鐘`:r.reason==='endpoint_missing'?'端點已移除或尚未加入':'無可達路徑';
    html+=`<section class="comparison-section"><h3>路徑：${labels[result.route_delta.status]}</h3>${table(['基準','目前'],`<tr><td>${routeText(result.before.route)}</td><td>${routeText(result.after.route)}</td></tr>`)}${result.route_delta.minutes!==null?`<p>時間差 ${deltaText(result.route_delta.minutes)} 分鐘 · 距離差 ${deltaText(result.route_delta.km)} 公里</p>`:''}</section>`;
  }
  html+='<section class="comparison-section"><h3>人員可達性</h3>';
  const peopleLabels={newly_unreachable_people:'新增不可達',restored_people:'恢復可達',entered_people:'新增納入分析',exited_people:'退出分析'};
  const beforeNodes=new Map(state.baseline.graph.nodes.map(n=>[n.id,n]));
  for(const [key,label] of Object.entries(peopleLabels)){
    html+=`<details ${result[key].length?'open':''}><summary>${label} ${result[key].length} 筆</summary><div class="comparison-people">${result[key].slice(0,50).map(id=>{const n=nodeById(id)||beforeNodes.get(id);return nodeById(id)?`<button data-compare-type="node" data-compare-id="${escapeHtml(id)}">${escapeHtml(n?.label||id)}</button>`:`<span>${escapeHtml(n?.label||id)}</span>`;}).join('')}${result[key].length>50?'<span>畫面顯示前 50 筆；完整清單包含於匯出報告。</span>':''}</div></details>`;
  }
  html+='<p class="muted">新增不可達與恢復可達只比較兩份快照皆納入分析的同一批人員；刪除、停用或改類型不算救援成功。</p></section>';
  for(const [collection,label] of [['nodes','物件'],['edges','連線']]){
    const changes=result.changes[collection];let rows='';const type=collection==='nodes'?'node':'edge';
    const entries=Object.entries(changes).flatMap(([action,items])=>items.map(item=>({action,item})));
    for(const {action,item} of entries.slice(0,100)){
      const name=action==='removed'?escapeHtml(item.label):`<button data-compare-type="${type}" data-compare-id="${escapeHtml(item.id)}">${escapeHtml(item.label)}</button>`;
      rows+=`<tr><td>${{added:'新增',removed:'移除',updated:'修改'}[action]}</td><td>${name}</td><td>${(item.fields||[]).map(f=>COMPARISON_FIELDS[f]||escapeHtml(f)).join('、')||'全部'}</td></tr>`;
    }
    html+=`<section class="comparison-section"><h3>${label}變更 · 新增 ${changes.added.length} / 移除 ${changes.removed.length} / 修改 ${changes.updated.length}</h3>${rows?table(['變更','名稱','欄位'],rows):'<p class="muted">沒有變更</p>'}${entries.length>100?'<p class="muted">畫面顯示前 100 筆；完整清單包含於匯出報告。</p>':''}</section>`;
  }
  html+=`<details><summary>資料限制與計算依據</summary><p>未納入路徑的缺座標連線：基準 ${result.before.ignored_edges.length} / 目前 ${result.after.ignored_edges.length}</p><ul>${result.after.assumptions.map(a=>`<li>${escapeHtml(a)}</li>`).join('')}</ul><p>模型：${escapeHtml(result.model)}；${escapeHtml(result.generated_at)}</p><p>基準 SHA-256：${escapeHtml(result.baseline_fingerprint)}<br>情境 SHA-256：${escapeHtml(result.scenario_fingerprint)}</p></details>`;
  $('comparison-result').innerHTML=html;
  $('comparison-result').querySelectorAll('[data-compare-id]').forEach(b=>b.onclick=()=>comparisonFocus(b.dataset.compareType,b.dataset.compareId));
}
function exportComparison(){
  if(!state.comparison||!state.comparisonInputs)return;
  const data={format:'smart-emergency-comparison-v1',...state.comparisonInputs,report:state.comparison};
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='情境比較報告.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function initComparison(){
  run('compare',openComparison);run('export-comparison',exportComparison);
  $('run-comparison').onclick=runComparison;
  $('close-comparison').onclick=()=>{$('comparison-dialog').close();comparisonRequest++;$('run-comparison').disabled=false;};
  $('comparison-dialog').addEventListener('cancel',()=>{comparisonRequest++;$('run-comparison').disabled=false;});
  for(const id of ['compare-start','compare-end'])$(id).onchange=()=>{invalidateComparison();$('run-comparison').disabled=false;};
  run('capture-baseline',()=>{if(!confirm('將目前拓樸固定為新基準？原比較基準將被取代，工作區內容不變。'))return;checkpoint();state.baseline=currentBaseline();changed();renderBaseline();});
  run('copy-scenario',async()=>{await save(true);comparisonMessage('已另存情境，固定比較基準一併保留');});
}
