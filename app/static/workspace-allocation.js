'use strict';
let allocationRequest=0, allocationPicker=null;
const ALLOCATION_REASONS={destination_unavailable:'需求物件未納入分析',missing_coordinates:'需求缺少座標',no_stock:'同品項與單位沒有庫存',no_dispatch_capacity:'供應停用、未定位或出貨上限為零',unreachable:'道路無法到達',travel_limit:'超過單程時間上限',capacity_or_priority:'受庫存、出貨上限或優先級限制'};
function logisticsRows(role){return state.graph.nodes.flatMap(node=>(node.logistics||[]).filter(line=>!role||line.role===role).map(line=>({node,line})));}
function nodeHasSupply(n){return n.available&&((n.logistics||[]).length?n.logistics.some(l=>l.role==='supply'&&l.quantity>0&&l.dispatch_limit!==0):['supply','facility'].includes(n.kind)&&n.quantity>0);}
function allocationMessage(text,error=false){$('allocation-status').textContent=text;$('allocation-status').classList.toggle('error',error);}
function invalidateAllocation(){allocationRequest++;state.allocation=null;state.allocationInputs=null;$('allocation-export').disabled=true;$('run-allocation').disabled=false;$('allocation-result').replaceChildren();allocationMessage('目前資料尚未試算');if(state.report?.allocation){state.report=null;renderCanvas();$('route-result').textContent='';}}
function renderMaterials(){
  const previous=$('allocation-material').value,pairs=new Map();
  for(const {line} of logisticsRows())if(line.item.trim()&&line.unit.trim())pairs.set(JSON.stringify([line.item.trim(),line.unit.trim()]),line.item.trim()+' / '+line.unit.trim());
  $('allocation-material').innerHTML=pairs.size?[...pairs].map(([key,label])=>`<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`).join(''):'<option value="">尚無品項與單位</option>';
  if(pairs.has(previous))$('allocation-material').value=previous;
  $('allocation-baseline-name').textContent=state.baseline?'比較基準：'+state.baseline.name:'尚無比較基準';
}
function renderAllocationRows(){
  for(const role of ['supply','demand']){
    const rows=logisticsRows(role),container=$(role==='supply'?'allocation-stock':'allocation-demand');
    container.innerHTML=rows.length?rows.map(({node,line})=>`<div class="allocation-row" data-logistics="${escapeHtml(line.id)}"><label class="allocation-location">物件位置<button data-pick="${escapeHtml(line.id)}" title="${escapeHtml(node.id)}"><i data-lucide="map-pin"></i><span>${escapeHtml(node.label)}</span></button></label><label>品項<input data-log-field="item" maxlength="80" value="${escapeHtml(line.item)}" required></label><label>單位<input data-log-field="unit" maxlength="40" value="${escapeHtml(line.unit)}" required></label><label>${role==='supply'?'庫存':'需求量'}<input data-log-field="quantity" type="number" min="0" max="1000000" step="1" value="${line.quantity}" required></label>${role==='supply'?`<label>出貨上限<input data-log-field="dispatch_limit" type="number" min="0" max="1000000" step="1" value="${line.dispatch_limit??''}" placeholder="同庫存"></label>`:`<label>優先級<select data-log-field="priority">${[5,4,3,2,1].map(p=>`<option value="${p}" ${line.priority===p?'selected':''}>${p}${p===5?' 最高':p===1?' 最低':''}</option>`).join('')}</select></label>`}<button class="allocation-remove" data-remove="${escapeHtml(line.id)}" title="刪除這筆物資紀錄" aria-label="刪除這筆物資紀錄"><i data-lucide="trash-2"></i></button></div>`).join(''):'<p class="muted">尚無明確的'+(role==='supply'?'品項庫存':'品項需求')+'紀錄</p>';
    container.querySelectorAll('[data-logistics]').forEach(row=>{const source=document.createElement('small');source.className='muted';source.textContent=rows.find(r=>r.line.id===row.dataset.logistics).line.source||'手動填報（未核實）';row.querySelector('.allocation-location').append(source);});
    container.querySelectorAll('[data-log-field]').forEach(input=>{
      input.oninput=invalidateAllocation;
      input.onchange=()=>{if(!input.reportValidity())return;const id=input.closest('[data-logistics]').dataset.logistics,entry=logisticsRows().find(r=>r.line.id===id),field=input.dataset.logField;if(!entry)return;
        const value=['item','unit'].includes(field)?input.value.trim():field==='dispatch_limit'&&input.value===''?null:Number(input.value);
        if(typeof value==='string'&&!value){input.setCustomValidity('請填寫品項與單位');input.reportValidity();input.setCustomValidity('');return;}
        mutate(()=>{entry.line[field]=value;});renderMaterials();};
    });
    container.querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>{const entry=logisticsRows().find(r=>r.line.id===button.dataset.remove);mutate(()=>{entry.node.logistics=entry.node.logistics.filter(l=>l.id!==entry.line.id);});renderAllocationRows();});
    container.querySelectorAll('[data-pick]').forEach(button=>button.onclick=()=>openAllocationPicker({id:button.dataset.pick}));
  }
  renderMaterials();icons();
}
function openAllocationPicker(context){allocationPicker=context;$('allocation-node-query').value='';renderAllocationPicker();$('allocation-node-dialog').showModal();}
function renderAllocationPicker(){
  const query=$('allocation-node-query').value.toLowerCase();
  const nodes=state.graph.nodes.filter(n=>(n.label+' '+n.id).toLowerCase().includes(query)).sort((a,b)=>(a.kind==='road_node')-(b.kind==='road_node')).slice(0,50);
  $('allocation-node-results').innerHTML=nodes.map(n=>`<button data-allocation-node="${escapeHtml(n.id)}"><span class="swatch" style="background:${COLORS[n.kind]}"></span><span>${escapeHtml(n.label)}<small class="muted"> ${TYPES[n.kind]} · ${escapeHtml(n.id)}</small></span></button>`).join('')||'<p class="muted">沒有符合的物件</p>';
  $('allocation-node-results').querySelectorAll('button').forEach(button=>button.onclick=()=>{
    const node=nodeById(button.dataset.allocationNode);if((node.logistics||[]).length>=30||(!allocationPicker.id&&logisticsRows().length>=2000)){$('allocation-node-results').insertAdjacentHTML('afterbegin','<p role="alert">物資紀錄上限：每個物件 30 筆、工作區共 2,000 筆。</p>');return;}
    const context=allocationPicker;
    mutate(()=>{node.logistics??=[];if(context.id){const old=logisticsRows().find(r=>r.line.id===context.id);if(old.node.id!==node.id){old.node.logistics=old.node.logistics.filter(l=>l.id!==context.id);node.logistics.push(old.line);}}else{const pair=$('allocation-material').value?JSON.parse($('allocation-material').value):['',''];node.logistics.push({id:crypto.randomUUID(),role:context.role,item:pair[0],unit:pair[1],quantity:0,priority:3,dispatch_limit:null});}});
    $('allocation-node-dialog').close();renderAllocationRows();
  });
}
function openAllocation(){if(!state.graph.nodes.length)throw Error('請先載入道路與物件位置');renderAllocationRows();$('allocation-dialog').showModal();}
async function runAllocation(){
  for(const input of $('allocation-dialog').querySelectorAll('input,select'))if(!input.reportValidity())return;
  const pair=JSON.parse($('allocation-material').value),token=++allocationRequest,version=state.editVersion;
  const inputs={graph:structuredClone(state.graph),baseline:$('allocation-compare').checked?structuredClone(state.baseline?.graph||null):null,item:pair[0],unit:pair[1],max_minutes:Number($('allocation-minutes').value)};
  state.allocation=null;state.allocationInputs=null;$('allocation-result').replaceChildren();$('allocation-export').disabled=true;$('run-allocation').disabled=true;allocationMessage('正在計算道路與容量限制下的分配…');
  try{const result=await api('/allocate',inputs);if(token!==allocationRequest||version!==state.editVersion)return;state.allocation=result;state.allocationInputs=inputs;renderAllocationResult();$('allocation-export').disabled=false;allocationMessage('試算完成 · 未扣庫存、未建立派遣');}
  catch(e){if(token===allocationRequest)allocationMessage(e.message,true);}finally{if(token===allocationRequest)$('run-allocation').disabled=false;}
}
function renderAllocationResult(){
  const r=state.allocation,after=r.after,unit=escapeHtml(r.unit),table=(head,rows)=>`<div class="comparison-table-wrap"><table class="comparison-table"><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
  let html=`<h3>${escapeHtml(r.item)} / ${unit}</h3>`;
  const labels={stock:'填報庫存',dispatch_capacity:'可出貨上限',requested:'需求量',allocated:'分配量',unmet:'未滿足量'};
  html+=table(r.before?['指標','基準','目前','差異']:['指標','目前'],Object.entries(labels).map(([key,label])=>`<tr><th>${label}（${unit}）</th>${r.before?`<td>${r.before.summary[key]}</td>`:''}<td>${after.summary[key]}</td>${r.before?`<td>${deltaText(r.comparison.deltas[key])}</td>`:''}</tr>`).join(''));
  if(r.comparison)html+=`<p class="muted">需求紀錄：新增 ${r.comparison.entered_demands.length}、移除 ${r.comparison.exited_demands.length}、需求量或優先級變更 ${r.comparison.changed_demands.length}；需求減少不代表完成配送。</p>`;
  html+='<section class="comparison-section"><h3>分配建議</h3>'+(after.assignments.length?table(['供應 → 需求','數量','單程估計','路線'],after.assignments.map((a,i)=>`<tr><td>${escapeHtml(a.source_label)} → ${escapeHtml(a.target_label)}</td><td>${a.quantity} ${unit}</td><td>${a.route.minutes} 分鐘<br>${a.route.km} 公里</td><td><button data-allocation-route="${i}" title="顯示試算配送路線" aria-label="顯示試算配送路線"><i data-lucide="route"></i></button></td></tr>`).join('')):'<p class="muted">沒有可分配的配送組合</p>')+'</section>';
  html+='<section class="comparison-section"><h3>需求與缺口</h3>'+table(['需求位置','優先級','分配／需求','缺口原因'],after.demands.map(d=>`<tr><td>${escapeHtml(d.label)}</td><td>${d.priority}</td><td>${d.allocated} / ${d.requested} ${unit}</td><td>${d.reason?ALLOCATION_REASONS[d.reason]:'已滿足試算需求'}</td></tr>`).join(''))+'</section>';
  html+='<section class="comparison-section"><h3>供應餘額</h3>'+table(['供應位置','庫存／出貨上限','本次分配','預估餘額'],after.inventory.map(s=>`<tr><td>${escapeHtml(s.label)}</td><td>${s.stock} / ${s.dispatch_capacity}</td><td>${s.allocated}</td><td>${s.remaining}</td></tr>`).join(''))+'</section>';
  html+=`<details><summary>計算依據與限制</summary><ul>${r.assumptions.map(a=>`<li>${escapeHtml(a)}</li>`).join('')}</ul><p>缺座標而未納入路徑的連線：${after.ignored_edges.length}</p><p>${escapeHtml(r.solver)} · ${escapeHtml(r.model)}<br>${escapeHtml(r.generated_at)}</p><p>資料 SHA-256：${escapeHtml(after.fingerprint)}</p></details>`;
  $('allocation-result').innerHTML=html;$('allocation-result').querySelectorAll('[data-allocation-route]').forEach(b=>b.onclick=()=>showAllocationRoute(+b.dataset.allocationRoute));icons();
}
function showAllocationRoute(index){
  const a=state.allocation?.after.assignments[index];if(!a)return;$('allocation-dialog').close();$('mode').value='select';
  for(const id of a.route.nodes)state.hidden.delete(nodeById(id).kind);
  state.report={allocation:true,route:{reachable:true,...a.route},metrics:{},critical_edges:[]};state.selected=null;render();
  $('route-start').value=a.source;$('route-end').value=a.target;$('route-result').textContent=`${a.quantity} ${state.allocation.unit} · 約 ${a.route.minutes} 分鐘`;
  if(state.view==='map')map.fitBounds(a.route.nodes.map(id=>{const n=nodeById(id);return [n.lat,n.lng];}),{maxZoom:17,padding:[35,35]});else if(cy)cy.fit(cy.nodes().filter(n=>a.route.nodes.includes(n.data('nodeId'))),50);
  message('目前顯示試算配送路線，尚未派遣');
}
function exportAllocation(){if(!state.allocation)return;const data={format:'smart-emergency-allocation-v1',workspace:{id:state.id,name:$('workspace-name').value,revision:state.revision,unsaved:state.dirty},inputs:state.allocationInputs,report:state.allocation};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='物資分配試算.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function saveAllocation(copy){for(const input of $('allocation-dialog').querySelectorAll('input,select'))if(!input.reportValidity())return;try{await save(copy);allocationMessage(copy?'已另存物資情境，未建立派遣':'工作區與物資紀錄已儲存，未扣庫存');}catch(e){allocationMessage(e.message,true);}}
function initAllocation(){
  run('allocation',openAllocation);$('run-allocation').onclick=runAllocation;run('allocation-export',exportAllocation);
  $('close-allocation').onclick=()=>{$('allocation-dialog').close();allocationRequest++;$('run-allocation').disabled=false;};$('allocation-dialog').addEventListener('cancel',()=>{allocationRequest++;$('run-allocation').disabled=false;});
  $('close-allocation-node').onclick=()=>$('allocation-node-dialog').close();$('allocation-node-query').oninput=renderAllocationPicker;
  $('add-stock').onclick=()=>openAllocationPicker({role:'supply'});$('add-demand').onclick=()=>openAllocationPicker({role:'demand'});
  for(const id of ['allocation-material','allocation-minutes','allocation-compare'])$(id).oninput=invalidateAllocation;
  run('allocation-copy',()=>saveAllocation(true));run('allocation-save',()=>saveAllocation(false));
  run('allocation-baseline',()=>{for(const input of $('allocation-dialog').querySelectorAll('input,select'))if(!input.reportValidity())return;if(!confirm('將目前路網、品項庫存與需求固定為比較基準？'))return;checkpoint();state.baseline=currentBaseline();changed();renderMaterials();});
}
