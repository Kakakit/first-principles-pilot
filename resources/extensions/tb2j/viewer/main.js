import * as echarts from 'echarts/core';
import {ScatterChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,TitleComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {bonds,selectBonds,selectJ,orderRows,validateMapping,mappingMap,orbitalRows,sumOrbitals,csv} from './model.js';
echarts.use([ScatterChart,GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,TitleComponent,CanvasRenderer]);
const $=id=>document.getElementById(id),protocol='fpilot.extension-view.v1';
const waiting=new Map();let sequence=0;
window.addEventListener('message',event=>{if(event.source!==window.parent||event.data?.protocol!==protocol||event.data.kind!=='response')return;const pending=waiting.get(event.data.id);if(!pending)return;waiting.delete(event.data.id);event.data.error?pending.reject(Error(event.data.error)):pending.resolve(event.data.value);});
async function bridge(kind,payload={}){
 if(window.parent===window){if(kind==='read-file')return fetch(payload.path).then(r=>{if(!r.ok)throw Error('数据文件读取失败');return r.json();});if(kind==='read-state')return {revision:0,value:{}};if(kind==='save-state')throw Error('独立展示请下载映射 JSON；持久保存由扩展宿主提供');}
 const id=String(++sequence);return new Promise((resolve,reject)=>{waiting.set(id,{resolve,reject});window.parent.postMessage({protocol,kind,id,...payload},'*');});
}
const element=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
const num=value=>Number.isFinite(value)?Number(value.toPrecision(7)).toString():'—';
const download=(name,content,type,encoding='text')=>{if(window.parent!==window){void bridge('download',{name,content,media_type:type,encoding}).then(()=>message('已发起下载：'+name)).catch(error);return;}const blob=encoding==='base64'?Uint8Array.from(atob(content),c=>c.charCodeAt(0)):content;const url=URL.createObjectURL(new Blob([blob],{type}));const a=element('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);};
const error=e=>{$('error').hidden=false;$('error').textContent=e.message||String(e);};
const atomLabel=(index)=>{const a=meta.atoms.find(a=>a.index===index);return (a?.symbol||'?')+' · 原子 '+index;};
let meta,all=[],mapping=[],map=new Map(),tableRows=[],plotRows=[],page=0,sort='r',direction='asc',selected=null,epoch=0;
const f={pairs:[],mode:'total',atomI:'',atomJ:'',rMin:'',rMax:'',jMin:'',jMax:'',oi:[1],oj:[1],gi:'',gj:''};
const cache=new Map(),colors=['#147c96','#ce6744','#7456a6','#368354','#ba841c','#4678bb'];
const chart=echarts.init($('chart'));
new ResizeObserver(()=>chart.resize()).observe($('chart'));
new ResizeObserver(()=>{if(window.parent!==window)window.parent.postMessage({protocol,kind:'resize',height:document.documentElement.scrollHeight},'*');}).observe(document.body);
function message(text){$('message').textContent=text;}
async function part(path){if(!cache.has(path))cache.set(path,bridge('read-file',{path}));return cache.get(path);}
function field(label,key,options){
 const l=element('label',label),input=options?element('select'):element('input');
 if(options)for(const [value,title] of options){const opt=element('option',title);opt.value=value;input.append(opt);}
 else{input.type='number';input.step='any';}
 input.value=f[key];input.setAttribute('aria-label',label);
 input.addEventListener('change',()=>{f[key]=input.value;page=0;if(['mode','atomI','atomJ'].includes(key))orbitalControls();void update().catch(error);});
 if(!options)input.addEventListener('input',()=>{f[key]=input.value;page=0;void update().catch(error);});
 l.append(input);$('filters').append(l);
}
function controls(){
 for(const [name,count] of Object.entries(meta.element_pairs)){
  const label=element('label'),check=element('input');check.type='checkbox';check.checked=true;check.setAttribute('aria-label',name);check.addEventListener('change',()=>{f.pairs=check.checked?[...f.pairs,name]:f.pairs.filter(p=>p!==name);page=0;void update().catch(error);});
  label.append(check,element('span',name+' ('+count+')'));$('pairs').append(label);
 }
 field('显示物理量','mode',[['total','总 Jᵢⱼ'],...(meta.orbital_count?[['orbital','所选轨道贡献']]:[])]);
 const atoms=[['','全部原子'],...meta.atoms.map(a=>[String(a.index),atomLabel(a.index)])];
 field('原子 i','atomI',atoms);field('原子 j','atomJ',atoms);
 field('距离下界 r / Å','rMin');field('距离上界 r / Å','rMax');field('J 下界 / meV','jMin');field('J 上界 / meV','jMax');
 orbitalControls();
}
function orbitalControls(){
 const host=$('orbital-controls');host.replaceChildren();host.hidden=f.mode!=='orbital';
 if(host.hidden)return;
 host.append(element('p','图中显示每条原子对所选矩阵元素的和；下表保留各矩阵元素。J 区间按图中的贡献和筛选，不将其改称总 J。'));
 const grid=element('div');grid.className='orbital-fields';
 for(const end of ['i','j']){
  const fs=element('fieldset');fs.append(element('legend','原子 '+end+' 的轨道'));
  const active=meta.atoms.filter(a=>!f[end==='i'?'atomI':'atomJ']||a.index===Number(f[end==='i'?'atomI':'atomJ']));
  const maximum=Math.max(0,...active.map(a=>a.orbitals));
  for(let oi=1;oi<=maximum;oi++){
   const applicable=active.filter(a=>a.orbitals>=oi),labels=applicable.map(a=>map.get(a.index+':'+oi)?.label).filter(Boolean),names=[...new Set(labels)];
   const label=element('label'),check=element('input');check.type='checkbox';check.checked=f['o'+end].includes(oi);check.setAttribute('aria-label','轨道 '+end+' '+oi);
   check.addEventListener('change',()=>{f['o'+end]=check.checked?[...f['o'+end],oi]:f['o'+end].filter(v=>v!==oi);page=0;void update().catch(error);});
   label.append(check,element('span','orb_'+oi+(names.length===1?' · '+(labels.length===applicable.length?'':'部分确认：')+names[0]:names.length>1?' · 多种确认标签':'')));fs.append(label);
  }
  const groups=[...new Set(mapping.map(r=>r.group).filter(Boolean))],label=element('label','确认分组'),select=element('select');
  for(const group of ['',...groups]){const opt=element('option',group||'不限制分组');opt.value=group;select.append(opt);}select.value=f['g'+end];select.addEventListener('change',()=>{f['g'+end]=select.value;page=0;void update().catch(error);});label.append(select);fs.append(label);grid.append(fs);
 }
 host.append(grid);
}
async function update(){
 const ticket=++epoch;let base=selectBonds(all,f),raw=base,plot=base;
 if(f.mode==='orbital'){
  message('载入所选元素对轨道数据；已载入的分片在本机复用…');
  const chunks=await Promise.all(meta.orbital_parts.filter(p=>f.pairs.includes(p.pair)).map(p=>part(p.path)));
  if(ticket!==epoch)return;
  raw=orbitalRows(chunks.flat(),base,f.oi,f.oj,map,f.gi,f.gj);plot=selectJ(sumOrbitals(raw),f);
  const ids=new Set(plot.map(r=>r.id));raw=raw.filter(r=>ids.has(r.id));
 }else{raw=selectJ(base,f);plot=raw;}
 tableRows=orderRows(raw,sort,direction);plotRows=plot;draw();table();
 message('完整数据筛选：'+plot.length+' 条原子对，'+raw.length+' 行表格；点选、排序均在本机处理。');
}
function draw(){
 const names=Object.keys(meta.element_pairs),orb=f.mode==='orbital';
 chart.setOption({title:{text:orb?'所选轨道交换贡献随距离':'元素对 Jᵢⱼ 随距离',textStyle:{fontSize:16}},grid:{left:72,right:24,top:54,bottom:100},legend:{bottom:0},tooltip:{trigger:'item',formatter:params=>{const id=params.data[2],r=plotRows.find(r=>r.id===id);const box=element('div');box.style.whiteSpace='pre-line';box.textContent=r?`${r.pair}\n${atomLabel(r.i)} → ${atomLabel(r.j)}\nR = [${r.R.join(', ')}]\n距离 r = ${r.r} Å\n${orb?'所选轨道和':'Jᵢⱼ'} = ${r.J} meV${orb?'\n总 J = '+r.totalJ+' meV\n所选和−总 J = '+(r.J-r.totalJ)+' meV':''}`:'';return box;}},xAxis:{type:'value',name:'距离 r / Å',nameLocation:'middle',nameGap:30},yAxis:{type:'value',name:orb?'轨道贡献 / meV':'Jᵢⱼ / meV',scale:true},dataZoom:[{type:'inside'},{type:'slider',bottom:30,height:18}],series:names.map((name,index)=>({id:name,name,type:'scatter',symbolSize:6,itemStyle:{color:colors[index%colors.length]},data:plotRows.filter(r=>r.pair===name).map(r=>[r.r,r.J,r.id])}))},{notMerge:true});
 $('chart-note').textContent='r 是原子间距离，R 是原生晶格平移。保留所有原始原子对，包括相同距离和反向记录；未平均、倍增或平滑。'+(orb?'只汇总已选矩阵元素，所选和不保证等于总 J。':'');
}
function table(){
 const orb=f.mode==='orbital',columns=[['i','原子 i'],['j','原子 j'],['R','晶格平移 R'],['r','距离 r / Å'],...(orb?[['oi','轨道 i'],['oj','轨道 j']]:[]),['J',orb?'轨道 J / meV':'Jᵢⱼ / meV']];
 if(orb)columns.push(['gi','分组 i'],['gj','分组 j']);
 const t=element('table'),head=element('thead'),tr=element('tr');
 for(const [key,title] of columns){const th=element('th'),button=element('button',title+(sort===key?(direction==='asc'?' ↑':' ↓'):''));button.setAttribute('aria-label','排序 '+title);button.addEventListener('click',()=>{direction=sort===key?(direction==='asc'?'desc':'asc'):key==='J'?'desc':'asc';sort=key;tableRows=orderRows(tableRows,sort,direction);page=0;table();});th.append(button);tr.append(th);}
 head.append(tr);t.append(head);const body=element('tbody');
 const pages=Math.max(1,Math.ceil(tableRows.length/100));page=Math.min(page,pages-1);
 for(const r of tableRows.slice(page*100,(page+1)*100)){
  const row=element('tr');if(r.id===selected)row.className='selected';
  for(const [key] of columns){let value=r[key];if(key==='i'||key==='j')value=atomLabel(value);else if(key==='R')value='['+r.R.join(', ')+']';else if(key==='oi'||key==='oj'){const name=r[key==='oi'?'li':'lj'];value='orb_'+value+(name?' · '+name:'');}else if(typeof value==='number')value=num(value);const td=element('td',value??'—');if(typeof r[key]==='number')td.title=String(r[key]);row.append(td);}body.append(row);
 }
 t.append(body);$('table').replaceChildren(t);$('rows-count').textContent=tableRows.length+' 行 · 排序覆盖全部筛选结果';$('page-label').textContent=(page+1)+' / '+pages;$('prev').disabled=page===0;$('next').disabled=page>=pages-1;
}
chart.on('click',params=>{selected=params.data[2];const index=tableRows.findIndex(r=>r.id===selected);if(index>=0)page=Math.floor(index/100);table();$('table').scrollIntoView({behavior:'smooth',block:'nearest'});});
$('prev').onclick=()=>{page--;table();};$('next').onclick=()=>{page++;table();};
$('png').onclick=()=>download('TB2J-Jij-vs-distance.png',chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#fff'}).split(',')[1],'image/png','base64');
$('csv').onclick=()=>download('TB2J-filtered-'+(f.mode==='orbital'?'orbitals':'exchange')+'.csv',csv(tableRows,f.mode==='orbital'),'text/csv;charset=utf-8');
$('mapping-download').onclick=()=>download('TB2J-orbital-labels.json',JSON.stringify({schema:'fpilot.tb2j-labels.v1',mapping,original_index_base:1},null,2),'application/json');
function mappingEditor(rows){
 const values=mappingMap(rows),t=element('table'),head=element('tr');
 for(const title of ['原子','矩阵序号','Wannier 序号','确认标签','可选分组'])head.append(element('th',title));const thead=element('thead');thead.append(head);t.append(thead);const body=element('tbody');
 for(const atom of meta.atoms)for(let oi=1;oi<=atom.orbitals;oi++){
  const row=element('tr'),existing=values.get(atom.index+':'+oi),global=meta.basis_rows.find(r=>r.atom_index===atom.index&&r.orbital_index===oi)?.wannier_index;
  row.append(element('td',atomLabel(atom.index)),element('td','orb_'+oi),element('td',global??'未记录'));
  for(const key of ['label','group']){const td=element('td'),input=element('input');input.type='text';input.maxLength=80;input.value=existing?.[key]||'';input.dataset.atom=atom.index;input.dataset.orbital=oi;input.dataset.key=key;input.setAttribute('aria-label',atomLabel(atom.index)+' orb_'+oi+' '+(key==='label'?'标签':'分组'));input.addEventListener('input',()=>{$('confirmed').checked=false;});td.append(input);row.append(td);}body.append(row);
 }
 t.append(body);$('mapping').replaceChildren(t);$('confirmed').checked=false;
}
$('candidate').onclick=()=>{mappingEditor(meta.candidate_mapping);$('mapping-status').textContent='已载入未确认候选；核对基底证据后勾选确认并保存。';};
$('restore').onclick=()=>{mappingEditor(meta.frozen_mapping);$('mapping-status').textContent='编辑区已恢复运行时标签；保存后生效。';};
$('save-mapping').onclick=async()=>{
 try{
  if(!$('confirmed').checked)throw Error('请先核对并确认实际基底与轨道顺序');
  const entries=new Map();for(const input of $('mapping').querySelectorAll('input')){const key=input.dataset.atom+':'+input.dataset.orbital;if(!entries.has(key))entries.set(key,{atom_index:Number(input.dataset.atom),orbital_index:Number(input.dataset.orbital),label:'',group:''});entries.get(key)[input.dataset.key]=input.value;}
  const rows=[...entries.values()];if(rows.some(r=>r.group.trim()&&!r.label.trim()))throw Error('填写分组的轨道必须填写确认标签');
  const validated=validateMapping(rows.filter(r=>r.label.trim()),meta.atoms);$('save-mapping').disabled=true;
  const state=await bridge('save-state',{value:{schema:'fpilot.tb2j-labels.v1',mapping:validated,confirmed:true}});
  mapping=validated;map=mappingMap(mapping);$('mapping-status').textContent='展示标签已保存 · 版本 '+state.revision+'；原计算参数和产物保持原样。';$('error').hidden=true;orbitalControls();await update();
 }catch(e){error(e);}finally{$('save-mapping').disabled=false;}
};
async function main(){
 meta=await part('index.json');if(meta.schema!=='fpilot.tb2j-view.v1')throw Error('无法识别绘图数据版本');
 f.pairs=Object.keys(meta.element_pairs);const [chunks,state]=await Promise.all([Promise.all(meta.exchange_parts.map(p=>part(p.path))),bridge('read-state')]);
 all=bonds(chunks.flat(),meta.atoms);if(all.length!==meta.exchange_count)throw Error('完整原子对数据数量不一致，停止显示截断结果');
 mapping=validateMapping(state.value?.confirmed?state.value.mapping:meta.frozen_mapping,meta.atoms);map=mappingMap(mapping);mappingEditor(mapping);controls();
 $('summary').textContent=meta.exchange_count+' 个原子对 · '+meta.orbital_count+' 个轨道矩阵元素 · '+(meta.mode==='spinor'?'SOC':'共线')+' · meV / Å';
 $('basis').textContent=meta.basis_text||'本次未记录 assigned_basis.txt';$('projections').textContent=meta.projections||'本次缺少冻结投影定义，需手工核对映射';
 $('mapping-status').textContent=state.revision?'使用已保存展示映射 · 版本 '+state.revision:'使用原运行冻结映射；未映射项保留 orb 序号。';
 $('candidate').disabled=!meta.candidate_mapping.length;
 for(const warning of meta.warnings){const p=element('p',warning);p.className='warning';$('validation').append(p);}
 $('validation').append(element('p','程序完成；数值收敛：'+(meta.validation.numerical_convergence||'NOT_EVALUATED')+'；物理验证：'+(meta.validation.physical||'NOT_PERFORMED')+'；方向验证：'+(meta.validation.direction_validation||'NOT_EVALUATED')));
 await update();
}
if(window.parent!==window)window.parent.postMessage({protocol,kind:'ready'},'*');
main().catch(e=>{error(e);if(window.parent!==window)window.parent.postMessage({protocol,kind:'failed',error:e.message},'*');});
