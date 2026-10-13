import * as echarts from 'echarts/core';
import {LineChart,ScatterChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,TitleComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {PRESETS,defaults,validate,value,unit,group,normalizedFit,series,fitSeries,fitMatches,csvReport,yOptions,batchGroup} from './model.js';
echarts.use([LineChart,ScatterChart,GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,TitleComponent,CanvasRenderer]);
const protocol='fpilot.extension-view.v1',pending=new Map();let serial=0,data,state,chart,residual;const selectedRows=new Set();
const $=id=>document.getElementById(id),esc=v=>String(v??'未提供').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>v===null||v===undefined?'未提供':Number.isFinite(v)?Number(v.toPrecision(9)).toString():String(v);
function status(message){$('status').textContent=message;}
function rpc(kind,args={}){return new Promise((resolve,reject)=>{const id=String(++serial),timer=setTimeout(()=>{pending.delete(id);reject(Error('展示通信超时'));},20000);pending.set(id,{resolve,reject,timer});parent.postMessage({protocol,kind,id,...args},'*');});}
addEventListener('message',e=>{if(e.source!==parent||e.data?.protocol!==protocol||e.data.kind!=='response')return;const p=pending.get(e.data.id);if(p){clearTimeout(p.timer);pending.delete(e.data.id);e.data.error?p.reject(Error(e.data.error)):p.resolve(e.data.value);}});
const opts=(items,selected)=>items.map(([k,v])=>`<option value="${esc(k)}" ${k===selected?'selected':''}>${esc(v)}</option>`).join('');
function controls(){
 $('controls').innerHTML=`<label>常用预设<select id="preset">${opts([...Object.keys(PRESETS).map(k=>[k,k]),['custom','自定义']],state.preset)}</select></label><label>横轴 X<select id="x">${opts(Object.entries(data.fields).map(([k,v])=>[k,v.label]),state.x)}</select></label><label>归一化<select id="normalization">${opts([['cell','每晶胞'],['atom','每原子'],['formula','每化学式单元']],state.normalization)}</select></label><label>压力单位<select id="pressureUnit">${opts([['GPa','GPa'],['kbar','kbar']],state.pressureUnit)}</select></label><label>显示<select id="mode">${opts([['scatter','散点'],['line','散点与连线']],state.mode)}</select></label>`+['xmin','xmax','ymin','ymax'].map((k,i)=>`<label>${['X 下限','X 上限','Y 下限','Y 上限'][i]}<input id="${k}" class="range" placeholder="自动" value="${esc(state[k])}"></label>`).join('');
 renderYMenu();
 $('energy').value=state.energy;$('model').value=state.model;
 $('preset').onchange=()=>{if(PRESETS[$('preset').value]){Object.assign(state,PRESETS[$('preset').value],{preset:$('preset').value});if(state.preset==='E–V')state.ys=[state.energy];state.ys=[...state.ys];controls();redraw();}};
 for(const k of ['x','normalization','pressureUnit','mode','xmin','xmax','ymin','ymax'])$(k).onchange=()=>{const old=structuredClone(state);state[k]=$(k).value;if(k==='x')state.preset='custom';try{validate(state,data);if(k==='pressureUnit')controls();redraw();}catch(e){state=old;controls();status(e.message);}};

}
function renderYMenu(){
 let draft=[...state.ys];
 $('ys').innerHTML=`<span>纵轴 Y（多选）</span><button id="y-toggle" aria-expanded="false" aria-controls="y-panel">${esc(state.ys.map(k=>data.fields[k].label).join('、'))} ▾</button><div id="y-panel" hidden><p id="y-hint"></p><div id="y-options"></div><p id="y-error" class="warning" role="alert"></p><div class="controls"><button id="y-clear">清空选择</button><button id="y-apply" class="primary">应用 Y 轴选择</button><button id="y-cancel">取消</button></div></div>`;
 const close=()=>{$('y-panel').hidden=true;$('y-toggle').setAttribute('aria-expanded','false');};
 const draw=()=>{
  $('y-hint').textContent=draft.length?'当前单位：'+unit(draft[0],state,data.fields)+'。不同单位已置灰；切换类别请先清空选择。':'请选择物理量；第一项决定同一 Y 轴的单位。';
  $('y-options').innerHTML=yOptions(data,state,draft).map(o=>`<button type="button" class="y-option" data-y="${esc(o.key)}" role="checkbox" aria-checked="${o.selected}" ${o.reason?'disabled':''}><span class="tick">${o.selected?'✓':''}</span><span>${esc(o.label)}<small>${esc(o.unit)}${o.reason?' · '+esc(o.reason):''}</small></span></button>`).join('');
  document.querySelectorAll('[data-y]').forEach(el=>el.onclick=()=>{draft=draft.includes(el.dataset.y)?draft.filter(k=>k!==el.dataset.y):[...draft,el.dataset.y];$('y-error').textContent='';draw();});
 };
 $('y-toggle').onclick=()=>{const open=$('y-panel').hidden;$('y-panel').hidden=!open;$('y-toggle').setAttribute('aria-expanded',String(open));if(open){draft=[...state.ys];$('y-error').textContent='';draw();}};
 $('y-clear').onclick=()=>{draft=[];$('y-error').textContent='';draw();};
 $('y-cancel').onclick=close;
 $('y-apply').onclick=()=>{try{const next={...state,ys:[...draft],preset:'custom'};validate(next,data);state=next;renderYMenu();redraw();$('y-toggle').focus();}catch(e){$('y-error').textContent=e.message;}};
 $('ys').onkeydown=e=>{if(e.key==='Escape'){close();$('y-toggle').focus();}};
}
function chartLayout(id,count,base=420){
 const narrow=$(id).clientWidth<650, legendHeight=Math.min(Math.max(count,1),8)*25+30;
 $(id).style.height=(narrow?base+legendHeight:base)+'px';
 return {legend:{orient:'vertical',type:count>8?'scroll':'plain',...(narrow?{left:52,bottom:0,height:legendHeight-15}:{right:0,top:45,bottom:35,width:210}),textStyle:{width:190,overflow:'truncate'},tooltip:{show:true},itemGap:10},grid:{left:narrow?52:85,right:narrow?18:230,top:45,bottom:narrow?legendHeight+65:65}};
}
function table(){
 const columns=['volume_a3','energy_sigma0_ev','free_energy_ev','external_pressure_gpa','a_ang','b_ang','c_ang','force_max_ev_ang'];
 $('table').innerHTML='<table><thead><tr><th>批量选择</th><th>参与拟合</th><th>来源 / job ID</th><th>组名</th>'+columns.map(k=>`<th>${esc(data.fields[k].label)}<br><small>${esc(unit(k,state,data.fields))}</small></th>`).join('')+'<th>证据与排除原因</th></tr></thead><tbody>'+data.points.map(p=>`<tr><td><input type="checkbox" data-row="${esc(p.id)}" ${selectedRows.has(p.id)?'checked':''} aria-label="${esc(p.label)} 批量选择"></td><td><input type="checkbox" data-include="${esc(p.id)}" ${!state.excluded.includes(p.id)?'checked':''} ${p.fit_blockers.length?'disabled':''} aria-label="${esc(p.label)} 参与拟合"></td><td>${esc(p.label)}<br><small>${esc(p.job_id)} · 步 ${p.step}</small></td><td><input type="text" data-group="${esc(p.id)}" maxlength="80" value="${esc(group(p,state))}" aria-label="${esc(p.label)} 分组"></td>${columns.map(k=>'<td>'+esc(num(value(p,k,state,data.fields)))+'</td>').join('')}<td>${esc(p.fit_blockers.join('；')||'符合当前拟合门禁')}<br><small>结束 ${p.validation.program_finished?'是':'未确认'} / 电子 ${p.validation.electronic_ediff?'达成':'未确认'} / 离子 ${p.validation.stage==='scf'?'静态计算':p.validation.ionic_accuracy?'达成':'未确认'}</small></td></tr>`).join('')+'</tbody></table>';
 $('batch-count').textContent=`已选择 ${selectedRows.size} 个数据点`;
 document.querySelectorAll('[data-row]').forEach(el=>el.onchange=()=>{el.checked?selectedRows.add(el.dataset.row):selectedRows.delete(el.dataset.row);$('batch-count').textContent=`已选择 ${selectedRows.size} 个数据点`;});
 document.querySelectorAll('[data-include]').forEach(el=>el.onchange=()=>{state.excluded=state.excluded.filter(id=>id!==el.dataset.include);if(!el.checked)state.excluded.push(el.dataset.include);redraw();});
 document.querySelectorAll('[data-group]').forEach(el=>el.onchange=()=>{state.groups[el.dataset.group]=el.value.trim()||data.points.find(p=>p.id===el.dataset.group).group;redraw();});
}
function redraw(){
 validate(state,data);table();const matching=fitMatches(state,data);const plotted=series(data,state),fitted=fitSeries(data,state);
 const layout=chartLayout('chart',plotted.length+fitted.length);chart.resize();
 chart.setOption({...layout,animation:false,tooltip:{trigger:'item',formatter:p=>p.data?.label?esc(p.data.label)+'<br>'+esc(p.seriesName)+'<br>'+p.value.map(num).join(', '):esc(p.seriesName)+'<br>'+p.value.map(num).join(', ')},dataZoom:[{type:'inside'}],xAxis:{type:'value',scale:true,name:data.fields[state.x].label+' / '+unit(state.x,state,data.fields),nameLocation:'middle',nameGap:35,min:state.xmin===''?null:Number(state.xmin),max:state.xmax===''?null:Number(state.xmax)},yAxis:{type:'value',scale:true,name:state.ys.map(k=>data.fields[k].label).join(' / ')+' ['+unit(state.ys[0],state,data.fields)+']',min:state.ymin===''?null:Number(state.ymin),max:state.ymax===''?null:Number(state.ymax)},series:[...plotted.map(s=>({name:s.name,type:state.mode==='line'?'line':'scatter',symbolSize:8,data:s.points.map(p=>({...p,itemStyle:p.excluded?{color:'#9aabbc'}:undefined}))})),...fitted.map(s=>({name:s.name,type:'line',showSymbol:false,data:s.points,lineStyle:{width:2,type:'dashed'}}))]},true);
 $('fits').innerHTML=(!matching?'<p class="warning">拟合选择已改变；旧拟合暂不展示。点击“应用分组并重新拟合”。</p>':'')+(matching?data.fits.map(f=>{
 if(f.status!=='FITTED')return `<div class="fit"><b>${esc(f.group)}</b> · 暂不能拟合<p>${esc(f.reason)}</p><small>${f.excluded.map(p=>esc(p.label+': '+p.reasons.join('；'))).join('<br>')}</small></div>`;
 const ref=data.points.find(p=>f.point_ids.includes(p.id)),p=normalizedFit(f,ref,state);
 return `<div class="fit"><b>${esc(f.group)}</b> · ${esc(f.model)} · ${f.point_ids.length} 点<p>E₀ = ${num(p.e0)} ${unit(f.energy,state,data.fields)} · V₀ = ${num(p.v0)} ${unit('volume_a3',state,data.fields)}<br>B₀ = ${num(p.b0)} GPa · B₀′ = ${num(p.b1)}<br>残差 RMSE = ${num(p.rmse)} ${unit(f.energy,state,data.fields)}</p><small>${esc(f.warnings.join('；'))}</small><details><summary>使用点、残差与排除点</summary><pre>${esc(JSON.stringify({used:f.point_ids,residuals:f.residuals,excluded:f.excluded},null,2))}</pre></details></div>`;
 }).join(''):'');
 const residualLayout=chartLayout('residual',matching?data.fits.filter(f=>f.status==='FITTED').length:0,280);residual.resize();
 residual.setOption({...residualLayout,animation:false,tooltip:{trigger:'item'},xAxis:{type:'value',scale:true,name:'V / Å³/atom'},yAxis:{type:'value',name:'能量残差 / eV/atom'},series:matching?data.fits.filter(f=>f.status==='FITTED').map(f=>({name:f.group,type:'scatter',data:f.residuals.map(r=>[r.volume_a3_atom,r.residual_ev_atom])})):[]},true);
 status(matching?'数据与当前拟合一致；展示设置可保存。':'选择已修改，重新拟合后才能导出一致的完整结果。');
}
async function save(){validate(state,data);await rpc('save-state',{value:state});status('展示设置已保存；原分析数据保持。');}
async function boot(){
 data=await rpc('read-file',{path:'data.json'});state=defaults(data);const saved=await rpc('read-state');if(saved.value?.identity===data.identity){try{state=validate({...state,...saved.value},data);}catch{state=defaults(data);}}
 $('sources').innerHTML=`<p>${data.points.length} 个冻结数据点 · 每晶胞 / 每原子 / 每化学式单元可切换</p>`+data.points.map(p=>`<details><summary>${esc(p.label)} · ${esc(p.formula)} · ${p.n_atoms} 原子 · job ${esc(p.job_id)}</summary><pre>${esc(JSON.stringify({source_id:p.id,source:p.source,files:p.files},null,2))}</pre></details>`).join('');
 $('evidence').innerHTML=data.points.map(p=>`<details><summary>${esc(p.label)} · OUTCAR 第 ${p.evidence.lattice.line} 行晶格 · 离子步 ${p.step}</summary><p>${esc(p.warnings.join('；'))}</p><pre>${esc(JSON.stringify({lattice_angstrom:p.lattice_angstrom,settings:p.settings,validation:p.validation,fields:p.evidence},null,2))}</pre></details>`).join('');
 chart=echarts.init($('chart'));residual=echarts.init($('residual'));controls();redraw();
 for(const k of ['energy','model'])$(k).onchange=()=>{const old=state.energy;state[k]=$(k).value;if(k==='energy'&&state.ys.includes(old)){state.ys=state.ys.map(y=>y===old?state.energy:y);controls();}redraw();};
 $('batch-all').onclick=()=>{data.points.forEach(p=>selectedRows.add(p.id));table();};
 $('batch-none').onclick=()=>{selectedRows.clear();table();};
 $('batch-apply').onclick=()=>{try{batchGroup(state,data,[...selectedRows],$('batch-name').value);redraw();}catch(e){status(e.message);}};
 $('save').onclick=()=>save().catch(e=>status(e.message));
 $('refit').onclick=async()=>{try{$('refit').disabled=true;await save();status('正在使用冻结数据重新拟合…');await rpc('refresh-view');}catch(e){status('重新拟合未完成：'+e.message);$('refit').disabled=false;}};
 $('png').onclick=()=>rpc('download',{name:'pressure-eos.png',media_type:'image/png',encoding:'base64',content:chart.getDataURL({pixelRatio:3,backgroundColor:'#fff'}).split(',')[1]}).catch(e=>status(e.message));
 $('csv').onclick=()=>{if(!fitMatches(state,data)){status('请先重新拟合，使导出数据与选择一致。');return;}rpc('download',{name:'pressure-eos-selection.csv',media_type:'text/csv;charset=utf-8',encoding:'text',content:csvReport(data,state)}).catch(e=>status(e.message));};
 $('json').onclick=()=>{if(!fitMatches(state,data)){status('请先重新拟合，使导出结果与选择一致。');return;}rpc('download',{name:'pressure-eos-selection.json',media_type:'application/json',encoding:'text',content:JSON.stringify({data,view:state,displayed_points:data.points.map(p=>({id:p.id,values:Object.fromEntries(Object.keys(data.fields).map(k=>[k,{value:value(p,k,state,data.fields),unit:unit(k,state,data.fields)}]))})),physical_validation:'NOT_PERFORMED'},null,2)}).catch(e=>status(e.message));};
 let lastWidth=$('chart').clientWidth;const resize=new ResizeObserver(()=>{if(lastWidth!==$('chart').clientWidth){lastWidth=$('chart').clientWidth;redraw();}chart.resize();residual.resize();parent.postMessage({protocol,kind:'resize',height:document.body.scrollHeight},'*');});resize.observe(document.body);
}
boot().catch(e=>{status('展示失败：'+e.message);parent.postMessage({protocol,kind:'failed',error:e.message},'*');});
