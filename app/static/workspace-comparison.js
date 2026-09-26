'use strict';
let comparisonRequest=0;
const COMPARISON_FIELDS={label:'名稱',kind:'類型',lat:'位置',lng:'位置',quantity:'數量',available:'納入分析',source:'來源／起點',target:'終點',status:'關係狀態',directed:'方向性',provenance:'資料來源',properties:'原始屬性',logistics:'品項庫存與需求'};
function currentBaseline(){return {name:$('workspace-name').value.trim()||'未命名工作區',graph:structuredClone(state.graph),captured_at:new Date().toISOString(),workspace_id:state.id||null,revision:state.revision||0,unsaved:state.dirty};}
function comparisonMessage(text,error=false){$('comparison-status').textContent=text;$('comparison-status').classList.toggle('error',error);}
function invalidateComparison(){comparisonRequest++;state.comparison=null;state.comparisonInputs=null;$('export-comparison').disabled=true;$('run-comparison').disabled=false;$('comparison-result').replaceChildren();comparisonMessage('目前變更尚未比較');}
function renderBaseline(){
  const b=state.baseline;$('baseline-name').textContent=b?.name||'尚未設定';
  $('baseline-time').textContent=b?`${new Date(b.captured_at).toLocaleString('zh-TW')} · 來源版本 ${b.revision}${b.unsaved?' · 含未儲存變更':''}${b.workspace_id?'':' · 未儲存來源'}`:'';
}
async function openComparison(){
  if(!state.graph.nodes.length&&!state.baseline)throw Error('請先載入資料');
  if(!state.baseline){state.baseline=currentBaseline();changed();}
  renderBaseline();$('comparison-dialog').showModal();await runComparison();
}
async function runComparison(){
  if(!state.baseline)return;
  const token=++comparisonRequest,version=state.editVersion;
  const inputs={baseline:structuredClone(state.baseline),scenario:{name:$('workspace-name').value,workspace_id:state.id,revision:state.revision,unsaved:state.dirty,graph:structuredClone(state.graph)}};
  state.comparison=null;state.comparisonInputs=null;$('export-comparison').disabled=true;$('comparison-result').replaceChildren();$('run-comparison').disabled=true;comparisonMessage('正在比較兩份事件資料快照…');
  try{
    const result=await api('/compare',{baseline:inputs.baseline.graph,graph:inputs.scenario.graph});
    if(token!==comparisonRequest||version!==state.editVersion)return;
    state.comparison=result;state.comparisonInputs=inputs;renderComparison(result);$('export-comparison').disabled=false;comparisonMessage('比較完成 · 僅供審查，未執行派遣');
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
  const metricNames={nodes:'物件',edges:'關係',people:'人員',available_supplies:'可用物資',open_demands:'待處理需求',components:'資料群組',unlinked_objects:'未連結物件',inactive_relations:'停用關係'};
  const table=(head,rows)=>`<div class="comparison-table-wrap"><table class="comparison-table"><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
  let html=table(['指標','基準','目前','差異'],Object.entries(metricNames).map(([key,label])=>`<tr><th>${label}</th><td>${result.before.metrics[key]}</td><td>${result.after.metrics[key]}</td><td>${deltaText(result.metric_deltas[key])}</td></tr>`).join(''));
  html+='<section class="comparison-section"><h3>資料關聯變化</h3>';
  const groupLabels={newly_unlinked_objects:'新增未連結',newly_linked_objects:'新增連結',entered_objects:'新增物件',exited_objects:'退出物件'};
  const beforeNodes=new Map(state.baseline.graph.nodes.map(n=>[n.id,n]));
  for(const [key,label] of Object.entries(groupLabels)){
    html+=`<details ${result[key].length?'open':''}><summary>${label} ${result[key].length} 筆</summary><div class="comparison-people">${result[key].slice(0,50).map(id=>{const n=nodeById(id)||beforeNodes.get(id);return nodeById(id)?`<button data-compare-type="node" data-compare-id="${escapeHtml(id)}">${escapeHtml(n?.label||id)}</button>`:`<span>${escapeHtml(n?.label||id)}</span>`;}).join('')}${result[key].length>50?'<span>畫面顯示前 50 筆；完整清單包含於匯出報告。</span>':''}</div></details>`;
  }
  html+='<p class="muted">連結變化只比較兩份快照中仍啟用的同一物件；刪除或停用不會被誤算為已完成處置。</p></section>';
  for(const [collection,label] of [['nodes','物件'],['edges','關係']]){
    const changes=result.changes[collection];let rows='';const type=collection==='nodes'?'node':'edge';
    const entries=Object.entries(changes).flatMap(([action,items])=>items.map(item=>({action,item})));
    for(const {action,item} of entries.slice(0,100)){
      const name=action==='removed'?escapeHtml(item.label):`<button data-compare-type="${type}" data-compare-id="${escapeHtml(item.id)}">${escapeHtml(item.label)}</button>`;
      rows+=`<tr><td>${{added:'新增',removed:'移除',updated:'修改'}[action]}</td><td>${name}</td><td>${(item.fields||[]).map(f=>COMPARISON_FIELDS[f]||escapeHtml(f)).join('、')||'全部'}</td></tr>`;
    }
    html+=`<section class="comparison-section"><h3>${label}變更 · 新增 ${changes.added.length} / 移除 ${changes.removed.length} / 修改 ${changes.updated.length}</h3>${rows?table(['變更','名稱','欄位'],rows):'<p class="muted">沒有變更</p>'}${entries.length>100?'<p class="muted">畫面顯示前 100 筆；完整清單包含於匯出報告。</p>':''}</section>`;
  }
  html+=`<details><summary>資料限制與計算依據</summary><ul>${result.after.assumptions.map(a=>`<li>${escapeHtml(a)}</li>`).join('')}</ul><p>模型：${escapeHtml(result.model)}；${escapeHtml(result.generated_at)}</p><p>基準 SHA-256：${escapeHtml(result.baseline_fingerprint)}<br>目前 SHA-256：${escapeHtml(result.scenario_fingerprint)}</p></details>`;
  $('comparison-result').innerHTML=html;
  $('comparison-result').querySelectorAll('[data-compare-id]').forEach(b=>b.onclick=()=>comparisonFocus(b.dataset.compareType,b.dataset.compareId));
}
function exportComparison(){
  if(!state.comparison||!state.comparisonInputs)return;
  const data={format:'smart-emergency-comparison-v1',...state.comparisonInputs,report:state.comparison};
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='事件資料比較報告.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function initComparison(){
  run('compare',openComparison);run('export-comparison',exportComparison);$('run-comparison').onclick=runComparison;
  $('close-comparison').onclick=()=>{$('comparison-dialog').close();comparisonRequest++;$('run-comparison').disabled=false;};
  $('comparison-dialog').addEventListener('cancel',()=>{comparisonRequest++;$('run-comparison').disabled=false;});
  run('capture-baseline',()=>{if(!confirm('將目前事件資料固定為新基準？原比較基準將被取代，工作區內容不變。'))return;checkpoint();state.baseline=currentBaseline();changed();renderBaseline();});
  run('copy-scenario',async()=>{await save(true);comparisonMessage('已另存資料快照，固定比較基準一併保留');});
}
