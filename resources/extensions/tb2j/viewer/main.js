import * as echarts from 'echarts/core';
import {ScatterChart,HeatmapChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,TitleComponent,VisualMapComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {bonds,selectBonds,selectJ,orderRows,validateMapping,mappingMap,orbitalRows,sumOrbitals,csv,shells,validateFilters} from './model.js';
echarts.use([ScatterChart,HeatmapChart,GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,TitleComponent,VisualMapComponent,CanvasRenderer]);
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
let meta,all=[],mapping=[],overrides=[],map=new Map(),tableRows=[],plotRows=[],page=0,sort='r',direction='asc',selected=null,epoch=0,layout='facets',selectedShell=null,activeMatrix=[],heat;
const f={pairs:[],mode:'total',atomI:'',atomJ:'',rMin:'',rMax:'9',jMin:'',jMax:'',Rx:'',Ry:'',Rz:'',oi:[],oj:[],gi:'',gj:''};
const cache=new Map(),colors=['#147c96','#ce6744','#7456a6','#368354','#ba841c','#4678bb'];
const pairCaption=name=>name.replace('–',' ↔ '),chart=echarts.init($('chart'));
new ResizeObserver(()=>{chart.resize();heat?.resize();}).observe(document.body);
new ResizeObserver(()=>{if(window.parent!==window)window.parent.postMessage({protocol,kind:'resize',height:document.documentElement.scrollHeight},'*');}).observe(document.body);
function message(text){$('message').textContent=text;}
async function part(path){if(!cache.has(path))cache.set(path,bridge('read-file',{path}));return cache.get(path);}
function field(label,key,options){
 const l=element('label',label),input=options?element('select'):element('input');
 if(options)for(const [value,title] of options){const opt=element('option',title);opt.value=value;input.append(opt);}
 else{input.type='number';input.step='any';input.placeholder='不限';}
 input.value=f[key];input.dataset.filter=key;input.setAttribute('aria-label',label);
 const change=()=>{f[key]=input.value;selectedShell=null;page=0;if(['mode','atomI','atomJ'].includes(key))orbitalControls();void update().catch(error);};
 input.addEventListener('change',change);if(!options)input.addEventListener('input',change);
 l.append(input);$('filters').append(l);
}
function controls(){
 $('pairs').replaceChildren();$('filters').replaceChildren();
 for(const [name,count] of Object.entries(meta.element_pairs)){
  const label=element('label'),check=element('input');label.dataset.pair=name;check.type='checkbox';check.checked=f.pairs.includes(name);check.setAttribute('aria-label',pairCaption(name));
  check.addEventListener('change',()=>{f.pairs=check.checked?[...f.pairs,name]:f.pairs.filter(p=>p!==name);selectedShell=null;page=0;void update().catch(error);});
  label.append(check,element('span',pairCaption(name)+' · '+count+' 条原始项'));$('pairs').append(label);
 }
 field('显示物理量','mode',[['total','总 Jᵢⱼ'],...(meta.orbital_count?[['orbital','所选轨道贡献和']]:[])]);
 const atoms=[['','全部原子'],...meta.atoms.map(a=>[String(a.index),atomLabel(a.index)])];
 field('原子 i（起点）','atomI',atoms);field('原子 j（终点）','atomJ',atoms);
 field('距离下界 d ≥ / Å','rMin');field('最大距离 d < / Å','rMax');field('J 下界 ≥ / meV','jMin');field('J 上界 ≤ / meV','jMax');
 for(const [index,key] of ['Rx','Ry','Rz'].entries())field(key+'（晶格整数）',key,[['','不限'],...[...new Set(all.map(r=>r.R[index]))].sort((a,b)=>a-b).map(v=>[String(v),String(v)])]);
 orbitalControls();
}
function orbitalName(atom,oi){const row=map.get(atom+':'+oi);return row?row.label+' · orb_'+oi+(row.source?'（投影参考）':'（已确认）'):'orb_'+oi+'（名称未确定）';}
function orbitalControls(){
 const host=$('orbital-controls');host.replaceChildren();
 if(!meta.orbital_count){host.append(element('p','本次没有轨道分解产物，可继续查看总 J。'));return;}
 host.append(element('p','初始默认选中全部轨道；切换“所选轨道贡献和”后，图中才使用这些矩阵元素的和。总 J 仍单独保留，不能默认两者相等。'));
 const grid=element('div');grid.className='orbital-fields';
 for(const end of ['i','j']){
  const fs=element('fieldset');fs.append(element('legend','原子 '+end+' 的轨道'));
  const active=meta.atoms.filter(a=>!f[end==='i'?'atomI':'atomJ']||a.index===Number(f[end==='i'?'atomI':'atomJ'])),maximum=Math.max(0,...active.map(a=>a.orbitals));
  const allButton=element('button','全选'),noneButton=element('button','清空');
  allButton.onclick=()=>{f['o'+end]=Array.from({length:maximum},(_,i)=>i+1);orbitalControls();void update().catch(error);};noneButton.onclick=()=>{f['o'+end]=[];orbitalControls();void update().catch(error);};fs.append(allButton,noneButton);
  for(let oi=1;oi<=maximum;oi++){
   const names=[...new Set(active.filter(a=>a.orbitals>=oi).map(a=>map.get(a.index+':'+oi)?.label||'未确定'))];
   const label=element('label'),check=element('input');check.type='checkbox';check.checked=f['o'+end].includes(oi);check.setAttribute('aria-label','轨道 '+end+' '+oi);
   check.addEventListener('change',()=>{f['o'+end]=check.checked?[...f['o'+end],oi]:f['o'+end].filter(v=>v!==oi);selectedShell=null;page=0;void update().catch(error);if(selected!==null)void showBond(selected).catch(error);});
   label.append(check,element('span',names.join(' / ')+' · orb_'+oi));fs.append(label);
  }
  const groups=[...new Set(mapping.map(r=>r.group).filter(Boolean))],label=element('label','手动分组'),select=element('select');select.setAttribute('aria-label','轨道分组 '+end);
  for(const group of ['',...groups]){const opt=element('option',group||'不限制分组');opt.value=group;select.append(opt);}select.value=f['g'+end];select.onchange=()=>{f['g'+end]=select.value;selectedShell=null;void update().catch(error);};label.append(select);fs.append(label);grid.append(fs);
 }
 host.append(grid);
}
async function orbitalData(pair){return (await Promise.all(meta.orbital_parts.filter(p=>p.pair===pair).map(p=>part(p.path)))).flat();}
async function update(){
 validateFilters(f);$('error').hidden=true;const ticket=++epoch;let base=selectBonds(all,f),raw=base,plot=base;
 if(f.mode==='orbital'){
  message('正在载入所选元素对的完整轨道分片…');
  const chunks=await Promise.all(meta.orbital_parts.filter(p=>f.pairs.includes(p.pair)).map(p=>part(p.path)));if(ticket!==epoch)return;
  raw=orbitalRows(chunks.flat(),base,f.oi,f.oj,map,f.gi,f.gj);plot=selectJ(sumOrbitals(raw),f);const ids=new Set(plot.map(r=>r.id));raw=raw.filter(r=>ids.has(r.id));
 }else{raw=selectJ(base,f);plot=raw;}
 const groups=shells(plot,Number($('shell-tolerance').value));renderShells(groups);
 if(selectedShell){const inShell=r=>r.pair===selectedShell.pair&&r.r>=selectedShell.minR&&r.r<=selectedShell.maxR;raw=raw.filter(inShell);plot=plot.filter(inShell);}
 tableRows=orderRows(raw,sort,direction);plotRows=plot;for(const label of $('pairs').querySelectorAll('label'))label.querySelector('span').textContent=pairCaption(label.dataset.pair)+' · '+plot.filter(r=>r.pair===label.dataset.pair).length+' / '+meta.element_pairs[label.dataset.pair]+' 条';draw();table();
 $('empty').hidden=plot.length>0;$('current').textContent=`当前 ${f.rMin===''?'0':f.rMin} ≤ d < ${f.rMax===''?'不限':f.rMax} Å · ${plot.length} / ${all.length} 条原始交换项 · ${f.pairs.map(pairCaption).join('、')||'未选元素对'} · R=[${[f.Rx,f.Ry,f.Rz].map(v=>v===''?'不限':v).join(', ')}]${selectedShell?' · 已选近邻分组':''}`;
 message('完整筛选数据共 '+raw.length+' 行；排序后再分页，原始记录和科学参数保持。');
 if(selected!==null&&!plot.some(r=>r.id===selected)){$('detail').hidden=true;selected=null;}
}
function draw(){
 const names=f.pairs,facets=layout==='facets',columns=facets?Math.max(1,Math.min(3,Math.floor(($('chart').clientWidth||800)/360))):1,rows=Math.max(1,Math.ceil(names.length/columns)),height=facets?rows*310:430;
 $('chart').style.height=height+'px';chart.resize();
 const xs=plotRows.map(r=>r.r),ys=plotRows.map(r=>r.J),yMin=Math.min(0,...ys),yMax=Math.max(0,...ys),pad=Math.max((yMax-yMin)*.12,.001),xMax=f.rMax!==''?Number(f.rMax):Math.max(1,...xs)*1.03;
 const tooltip={trigger:'item',formatter:params=>{const r=plotRows.find(r=>r.id===params.data[2]),box=element('div');box.style.whiteSpace='pre-line';box.textContent=r?`${pairCaption(r.pair)}\n${atomLabel(r.i)} → ${atomLabel(r.j)}\nR=[${r.R.join(', ')}] · d=${r.r} Å\n${f.mode==='orbital'?'所选轨道和':'J'}=${r.J} meV${f.mode==='orbital'?'\n总 J='+r.totalJ+' meV':''}\n点击查看轨道热图`:'';return box;}};
 const rough=Math.max((yMax-yMin)/5,1e-12),power=10**Math.floor(Math.log10(rough)),step=[1,2,5,10].map(v=>v*power).find(v=>v>=rough)||power*10,lower=Math.floor((yMin-pad*.1)/step)*step,upper=Math.ceil((yMax+pad*.1)/step)*step;
 const grid=[],xAxis=[],yAxis=[],title=[];
 for(let index=0;index<(facets?Math.max(1,names.length):1);index++){
  const col=index%columns,row=Math.floor(index/columns),width=100/columns;
  grid.push({left:(col*width+width*.17)+'%',width:(width*.77)+'%',top:row*310+48,height:facets?200:height-140});
  xAxis.push({type:'value',gridIndex:index,min:0,max:xMax,name:'距离 d / Å',nameLocation:'middle',nameGap:28});
  yAxis.push({type:'value',gridIndex:index,min:lower,max:upper,name:'J / meV',axisLabel:{formatter:v=>num(v)}});
  title.push({text:facets?pairCaption(names[index]||'未选择元素对')+' · '+plotRows.filter(r=>r.pair===names[index]).length+' 条':'元素对交换作用叠加',left:facets?(col*width+width*.17)+'%':'center',top:row*310+8,textStyle:{fontSize:15,color:'#203740'}});
 }
 chart.setOption({animation:false,title,grid,tooltip,xAxis,yAxis,legend:facets?undefined:{bottom:0},dataZoom:facets?[]:[{type:'inside'},{type:'slider',bottom:30,height:18}],series:names.map((name,index)=>({id:name,name:pairCaption(name),type:'scatter',xAxisIndex:facets?index:0,yAxisIndex:facets?index:0,symbolSize:7,itemStyle:{color:colors[Object.keys(meta.element_pairs).indexOf(name)%colors.length],opacity:.8},data:plotRows.filter(r=>r.pair===name).map(r=>[r.r,r.J,r.id])}))},{notMerge:true});
 $('chart-note').textContent='分面使用相同坐标范围，保留相同距离和反向记录；不平滑、不倍增。'+(f.mode==='orbital'?'当前为所选轨道贡献和，不能直接替代总 J。':'J 的正负沿用 TB2J 原始约定。');
}
function renderShells(groups){
 const t=element('table'),head=element('thead'),tr=element('tr');for(const title of ['元素对','近邻组','距离范围 / Å','J 最小 / meV','J 最大 / meV','J 中位数 / meV','原始项数','查看'])tr.append(element('th',title));head.append(tr);t.append(head);const body=element('tbody'),counts={};
 for(const group of groups){counts[group.pair]=(counts[group.pair]||0)+1;const row=element('tr');if(selectedShell?.pair===group.pair&&selectedShell.minR===group.minR)row.className='selected';for(const value of [pairCaption(group.pair),counts[group.pair],num(group.minR)+'–'+num(group.maxR),num(group.minJ),num(group.maxJ),num(group.medianJ),group.count])row.append(element('td',value));const td=element('td'),button=element('button','查看本组');button.setAttribute('aria-label',pairCaption(group.pair)+' 第 '+counts[group.pair]+' 近邻组');button.onclick=()=>{selectedShell=group;page=0;void update().catch(error);};td.append(button);row.append(td);body.append(row);}
 t.append(body);$('shells').replaceChildren(t);
}
function table(){
 const orb=f.mode==='orbital',columns=[['i','原子 i'],['j','原子 j'],['R','晶格平移 R'],['r','距离 d / Å'],...(orb?[['oi','轨道 i'],['oj','轨道 j']]:[]),['J',orb?'轨道 J / meV':'Jᵢⱼ / meV'],['absJ','|J| / meV']];
 const t=element('table'),head=element('thead'),tr=element('tr');
 for(const [key,title] of columns){const th=element('th'),button=element('button',title+(sort===key?(direction==='asc'?' ↑':' ↓'):''));button.setAttribute('aria-label','排序 '+title);button.onclick=()=>{direction=sort===key?(direction==='asc'?'desc':'asc'):['J','absJ'].includes(key)?'desc':'asc';sort=key;tableRows=orderRows(tableRows,sort,direction);page=0;syncSort();table();};th.append(button);tr.append(th);}head.append(tr);t.append(head);const body=element('tbody'),pages=Math.max(1,Math.ceil(tableRows.length/100));page=Math.min(page,pages-1);
 for(const r of tableRows.slice(page*100,(page+1)*100)){
  const row=element('tr');row.dataset.bond=r.id;row.tabIndex=0;if(r.id===selected)row.className='selected';row.onclick=()=>void showBond(r.id).catch(error);row.onkeydown=e=>{if(e.key==='Enter')void showBond(r.id).catch(error);};
  for(const [key] of columns){let value=r[key];if(key==='i'||key==='j')value=atomLabel(value);else if(key==='R')value='['+r.R.join(', ')+']';else if(key==='oi'||key==='oj')value=orbitalName(r[key==='oi'?'i':'j'],value);else if(key==='absJ')value=num(Math.abs(r.J));else if(typeof value==='number')value=num(value);const td=element('td',value??'—');if(typeof r[key]==='number')td.title=String(r[key]);row.append(td);}body.append(row);
 }
 t.append(body);$('table').replaceChildren(t);$('rows-count').textContent=tableRows.length+' 行（完整排序）';$('page-label').textContent=(page+1)+' / '+pages;$('prev').disabled=page===0;$('next').disabled=page>=pages-1;
}
function syncSort(){$('sort').value=sort+':'+direction;}
async function showBond(id){
 selected=id;table();const bond=all.find(r=>r.id===id);if(!bond)return;
 $('detail').hidden=false;$('detail-title').textContent=atomLabel(bond.i)+' → '+atomLabel(bond.j)+' · d='+num(bond.r)+' Å · J='+num(bond.J)+' meV';$('detail-note').textContent='原始 R=['+bond.R.join(', ')+']。行对应原子 i，列对应原子 j；自动名称是初始投影参考。';
 if(!meta.orbital_count){$('heatmap').hidden=true;$('matrix-note').textContent='本次未输出轨道分解。';return;}
 const values=(await orbitalData(bond.pair)).filter(v=>v[0]===id);if(selected!==id)return;
 activeMatrix=orbitalRows(values,[bond],f.oi,f.oj,map,f.gi,f.gj);const is=[...new Set(activeMatrix.map(r=>r.oi))].sort((a,b)=>a-b),js=[...new Set(activeMatrix.map(r=>r.oj))].sort((a,b)=>a-b),range=Math.max(.000001,...activeMatrix.map(r=>Math.abs(r.J))),sum=activeMatrix.reduce((s,r)=>s+r.J,0);
 $('heatmap').hidden=false;if(!heat)heat=echarts.init($('heatmap'));heat.resize();
 heat.setOption({animation:false,tooltip:{formatter:params=>{const r=activeMatrix[params.data[3]],box=element('div');box.style.whiteSpace='pre-line';box.textContent=r?orbitalName(r.i,r.oi)+' → '+orbitalName(r.j,r.oj)+'\n'+r.J+' meV':'';return box;}},grid:{left:160,right:50,top:30,bottom:110},xAxis:{type:'category',data:js.map(oi=>orbitalName(bond.j,oi)),axisLabel:{rotate:25,fontSize:11},name:'轨道 j'},yAxis:{type:'category',data:is.map(oi=>orbitalName(bond.i,oi)),axisLabel:{fontSize:11},name:'轨道 i'},visualMap:{dimension:2,min:-range,max:range,orient:'horizontal',left:'center',bottom:0,calculable:true,inRange:{color:['#3267ad','#f8fafc','#bf493f']}},series:[{type:'heatmap',data:activeMatrix.map((r,index)=>[js.indexOf(r.oj),is.indexOf(r.oi),r.J,index]),label:{show:true,formatter:p=>num(p.data[2]),fontSize:11}}]},{notMerge:true});
 $('matrix-note').textContent='所选矩阵元素和='+num(sum)+' meV；原始总 J='+num(bond.J)+' meV；差值='+num(sum-bond.J)+' meV。轨道和可能不等于总 J，保留原始值。';
 const t=element('table'),h=element('tr');for(const title of ['轨道 i','轨道 j','贡献 / meV','|贡献| / meV'])h.append(element('th',title));const head=element('thead');head.append(h);t.append(head);const body=element('tbody');for(const r of orderRows(activeMatrix,'absJ','desc')){const row=element('tr');for(const v of [orbitalName(r.i,r.oi),orbitalName(r.j,r.oj),num(r.J),num(Math.abs(r.J))])row.append(element('td',v));body.append(row);}t.append(body);$('contributions').replaceChildren(t);
}
chart.on('click',params=>{if(Array.isArray(params.data))void showBond(params.data[2]).catch(error);});
$('prev').onclick=()=>{page--;table();};$('next').onclick=()=>{page++;table();};
$('sort').onchange=()=>{[sort,direction]=$('sort').value.split(':');tableRows=orderRows(tableRows,sort,direction);page=0;table();};
$('shell-tolerance').onchange=()=>{const value=Number($('shell-tolerance').value);if(!Number.isFinite(value)||value<0){error(Error('距离容差须为非负有限数'));return;}selectedShell=null;void update().catch(error);};
$('clear-shell').onclick=()=>{selectedShell=null;void update().catch(error);};
for(const name of ['facets','overlay'])$(name).onclick=()=>{layout=name;$('facets').setAttribute('aria-pressed',String(name==='facets'));$('overlay').setAttribute('aria-pressed',String(name==='overlay'));draw();};
$('same-cell').onclick=()=>{f.Rx='0';f.Ry='0';f.Rz='0';selectedShell=null;page=0;controls();void update().catch(error);};
$('all-cells').onclick=()=>{f.Rx='';f.Ry='';f.Rz='';selectedShell=null;page=0;controls();void update().catch(error);};
$('reset').onclick=()=>{Object.assign(f,{pairs:Object.keys(meta.element_pairs),mode:'total',atomI:'',atomJ:'',rMin:'',rMax:String(meta.default_max_distance_A||9),jMin:'',jMax:'',Rx:'',Ry:'',Rz:'',gi:'',gj:''});sort='r';direction='asc';selectedShell=null;selected=null;$('detail').hidden=true;page=0;syncSort();controls();void update().catch(error);};
$('png').onclick=()=>download('TB2J-Jij-vs-distance.png',chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#fff'}).split(',')[1],'image/png','base64');
$('heat-png').onclick=()=>{if(heat)download('TB2J-orbital-matrix.png',heat.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#fff'}).split(',')[1],'image/png','base64');};
$('csv').onclick=()=>download('TB2J-filtered-'+(f.mode==='orbital'?'orbitals':'exchange')+'.csv',csv(tableRows,f.mode==='orbital'),'text/csv;charset=utf-8');
$('matrix-csv').onclick=()=>download('TB2J-selected-orbital-matrix.csv',csv(activeMatrix,true),'text/csv;charset=utf-8');
$('mapping-download').onclick=()=>download('TB2J-orbital-labels.json',JSON.stringify({schema:'fpilot.tb2j-labels.v1',mapping,original_index_base:1},null,2),'application/json');
function mergeMapping(){const merged=mappingMap(meta.automatic_mapping||[]);for(const row of overrides)merged.set(row.atom_index+':'+row.orbital_index,row);mapping=[...merged.values()];map=mappingMap(mapping);}
function mappingEditor(rows){
 const values=mappingMap(rows),t=element('table'),h=element('tr');for(const title of ['原子','矩阵序号','Wannier 序号','手动覆盖标签','可选分组'])h.append(element('th',title));const head=element('thead');head.append(h);t.append(head);const body=element('tbody');
 for(const atom of meta.atoms)for(let oi=1;oi<=atom.orbitals;oi++){const row=element('tr'),existing=values.get(atom.index+':'+oi),global=meta.basis_rows.find(r=>r.atom_index===atom.index&&r.orbital_index===oi)?.wannier_index;row.append(element('td',atomLabel(atom.index)),element('td','orb_'+oi),element('td',global??'未记录'));for(const key of ['label','group']){const td=element('td'),input=element('input');input.type='text';input.maxLength=80;input.value=existing?.[key]||'';input.placeholder=map.get(atom.index+':'+oi)?.label||'可留空';input.dataset.atom=atom.index;input.dataset.orbital=oi;input.dataset.key=key;input.setAttribute('aria-label',atomLabel(atom.index)+' orb_'+oi+' '+(key==='label'?'标签':'分组'));input.oninput=()=>{$('confirmed').checked=false;};td.append(input);row.append(td);}body.append(row);}t.append(body);$('mapping').replaceChildren(t);$('confirmed').checked=false;
}
$('candidate').onclick=()=>{mappingEditor(meta.automatic_mapping||meta.candidate_mapping);$('mapping-status').textContent='已载入初始投影参考；自动显示本身无需手工填写或保存。';};
$('restore').onclick=()=>mappingEditor(meta.frozen_mapping);
$('save-mapping').onclick=async()=>{try{if(!$('confirmed').checked)throw Error('保存手动覆盖前，请确认基底与坐标轴');const entries=new Map();for(const input of $('mapping').querySelectorAll('input')){const key=input.dataset.atom+':'+input.dataset.orbital;if(!entries.has(key))entries.set(key,{atom_index:Number(input.dataset.atom),orbital_index:Number(input.dataset.orbital),label:'',group:''});entries.get(key)[input.dataset.key]=input.value;}const rows=[...entries.values()];if(rows.some(r=>r.group.trim()&&!r.label.trim()))throw Error('填写分组的项需要标签');const validated=validateMapping(rows.filter(r=>r.label.trim()),meta.atoms);$('save-mapping').disabled=true;const state=await bridge('save-state',{value:{schema:'fpilot.tb2j-labels.v1',mapping:validated,confirmed:true}});overrides=validated;mergeMapping();$('mapping-status').textContent='手动覆盖已保存 · 版本 '+state.revision+'；原计算参数和产物保持。';orbitalControls();await update();if(selected!==null)await showBond(selected);}catch(e){error(e);}finally{$('save-mapping').disabled=false;}};
async function main(){
 meta=await part('index.json');if(meta.schema!=='fpilot.tb2j-view.v1')throw Error('无法识别绘图数据版本');
 f.pairs=Object.keys(meta.element_pairs);f.rMax=String(meta.default_max_distance_A||9);const [chunks,state]=await Promise.all([Promise.all(meta.exchange_parts.map(p=>part(p.path))),bridge('read-state')]);all=bonds(chunks.flat(),meta.atoms);if(all.length!==meta.exchange_count)throw Error('完整数据数量不一致，停止显示截断结果');
 overrides=validateMapping(state.value?.confirmed?state.value.mapping:meta.frozen_mapping,meta.atoms);mergeMapping();mappingEditor(overrides);const maximum=Math.max(0,...meta.atoms.map(a=>a.orbitals));f.oi=Array.from({length:maximum},(_,i)=>i+1);f.oj=[...f.oi];controls();
 $('summary').textContent=meta.exchange_count+' 条完整交换项 · '+meta.orbital_count+' 个轨道矩阵元素 · '+(meta.mode==='spinor'?'SOC':'共线')+' · meV / Å';$('calculation').textContent=(meta.calculation_max_distance_A?'本次计算最大距离：d < '+meta.calculation_max_distance_A+' Å。':'原运行未设置计算截断距离。')+' 当前是已有结果的显示筛选，修改不会重算。';
 $('basis').textContent=meta.basis_text||'未保存基底对应文件';$('projections').textContent=meta.projections||'未找到明确投影定义';$('mapping-status').textContent='自动投影参考 '+(meta.automatic_mapping?.length||0)+' 项；手动覆盖 '+overrides.length+' 项。';$('candidate').disabled=!meta.candidate_mapping.length;
 for(const note of meta.label_notes||[]){const p=element('p',note);p.className='help';$('label-notes').append(p);}for(const warning of meta.warnings){const p=element('p',warning);p.className='warning';$('validation').append(p);}$('validation').append(element('p','程序完成；数值收敛：'+(meta.validation.numerical_convergence||'NOT_EVALUATED')+'；物理验证：'+(meta.validation.physical||'NOT_PERFORMED')+'；方向验证：'+(meta.validation.direction_validation||'NOT_EVALUATED')));await update();
}
if(window.parent!==window)window.parent.postMessage({protocol,kind:'ready'},'*');main().catch(e=>{error(e);if(window.parent!==window)window.parent.postMessage({protocol,kind:'failed',error:e.message},'*');});
