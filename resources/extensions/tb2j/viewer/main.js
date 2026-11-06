import {percent,automaticGroups,groupBonds,reciprocalMeans,distanceMeans,groupNames,groupCsv,compactIdentity,CHANNELS} from './groups.js';
import {identityMapping} from './identity.js';
import * as echarts from 'echarts/core';
import {ScatterChart,HeatmapChart,LineChart,CustomChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,TitleComponent,VisualMapComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {bonds,selectBonds,selectJ,orderRows,validateMapping,mappingMap,orbitalRows,sumOrbitals,csv,shells,validateFilters} from './model.js';
echarts.use([ScatterChart,HeatmapChart,LineChart,CustomChart,GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,TitleComponent,VisualMapComponent,CanvasRenderer]);
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
let savedBase={},identityAttachment=null;
let meta,all=[],mapping=[],overrides=[],map=new Map(),tableRows=[],plotRows=[],page=0,sort='absJ',direction='desc',selected=null,epoch=0,layout='single',selectedShell=null,activeMatrix=[],heat;
const f={pairs:[],mode:'groups',atomI:'',atomJ:'',rMin:'',rMax:'9',jMin:'',jMax:'',Rx:'',Ry:'',Rz:'',oi:[],oj:[],gi:'',gj:''};
let automatic={map:new Map(),diagnostics:[]},chartGroups=[],comparison=false,graphNames={},legendVisible={},siteContext=[],displayedKeys=[];
const seriesColors={total:'hsl(215, 30%, 20%)',tt:'hsl(205, 75%, 42%)',te:'hsl(30, 85%, 45%)',et:'hsl(150, 50%, 35%)',ee:'hsl(275, 50%, 48%)',unknown:'hsl(215, 12%, 55%)'};
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
 l.append(input);$('filters').append(l);return l;
}
function controls(){
 $('pairs').replaceChildren();$('filters').replaceChildren();$('quantity-controls').replaceChildren();
 for(const [name,count] of Object.entries(meta.element_pairs).sort(([a],[b])=>a.localeCompare(b))){
  const button=element('button',pairCaption(name)+' · '+count+' 条');button.dataset.pair=name;button.setAttribute('aria-pressed',String(f.pairs.includes(name)));
  button.onclick=()=>{f.pairs=comparison?(f.pairs.includes(name)?f.pairs.filter(p=>p!==name):[...f.pairs,name]):[name];selectedShell=null;selected=null;page=0;$('detail').hidden=true;controls();void update().catch(error);};$('pairs').append(button);
 }
 $('quantity-controls').append(field('显示内容','mode',[['groups','总 J ＋ 四个轨道组通道'],['total','仅总 Jᵢⱼ'],...(meta.orbital_count?[['orbital','高级：所选单轨道贡献']]:[])]),field('最大距离 d < / Å','rMax'));
 const atoms=[['','全部原子'],...meta.atoms.map(a=>[String(a.index),atomLabel(a.index)])];
 field('原子 i（原始方向）','atomI',atoms);field('原子 j（原始方向）','atomJ',atoms);
 field('距离下界 d ≥ / Å','rMin');field('总 J 下界 ≥ / meV','jMin');field('总 J 上界 ≤ / meV','jMax');
 for(const [index,key] of ['Rx','Ry','Rz'].entries())field(key+'（原始晶格整数）',key,[['','不限'],...[...new Set(all.map(r=>r.R[index]))].sort((a,b)=>a-b).map(v=>[String(v),String(v)])]);
 orbitalControls();
}
function orbitalName(atom,oi){const row=map.get(atom+':'+oi),g=automatic.map.get(atom+':'+oi)?.group,cn=siteContext.find(s=>s.atom===atom)?.coordination;const name=g==='t'?(cn===6?'t₂g':cn===4?'t₂':'三重组'):g==='e'?(cn===6?'e_g':cn===4?'e':'二重组'):'未确定';return row?name+' · WF'+(row.wannier_index??'?')+' (orb_'+oi+')':'orb_'+oi+'（名称未确定）';}
function orbitalControls(){
 const host=$('orbital-controls');host.replaceChildren();
 if(!meta.orbital_count){host.append(element('p','本次没有轨道分解产物，可继续查看总 J。'));return;}
 host.append(element('p','以下选择只影响“高级：所选单轨道贡献”模式；总 J 与自动轨道组图使用完整矩阵。'));
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
 validateFilters(f);$('error').hidden=true;const ticket=++epoch;let base=selectJ(selectBonds(all,f),f),raw=base;
 let entries=[];if(f.mode!=='total'&&meta.orbital_count){
  message('正在整理所选元素对的完整轨道数据…');entries=(await Promise.all(meta.orbital_parts.filter(p=>f.pairs.includes(p.pair)).map(p=>part(p.path)))).flat();if(ticket!==epoch)return;
 }
 let grouped;
 if(f.mode==='orbital'){
  raw=orbitalRows(entries,base,f.oi,f.oj,map,f.gi,f.gj);const sums=new Map(sumOrbitals(raw).map(b=>[b.id,b]));grouped=base.filter(b=>sums.has(b.id)).map(b=>({...b,J:sums.get(b.id).J,values:{},matrixSum:null}));
 }else grouped=groupBonds(base,entries,automatic.map,meta.atoms);
 const independent=reciprocalMeans(grouped);chartGroups=distanceMeans(independent,Number($('shell-tolerance').value));plotRows=base;
 if(selectedShell){const group=chartGroups.find(g=>g.pair===selectedShell.pair&&g.minR===selectedShell.minR&&g.maxR===selectedShell.maxR);if(group){const ids=new Set(group.memberIds);raw=raw.filter(r=>ids.has(r.id));}else selectedShell=null;}
 tableRows=orderRows(raw,sort,direction);renderShells(chartGroups);draw();table();
 $('empty').hidden=independent.length>0;$('current').textContent=f.pairs.map(pairCaption).join('、')+' · '+(f.rMin||'0')+' ≤ d < '+(f.rMax||'不限')+' Å'+(selectedShell?' · 明细已定位到所选距离组':'');
 const ready=automatic.diagnostics.filter(d=>d.ready).length,delta=chartGroups.map(g=>g.delta?Math.max(Math.abs(g.delta.min),Math.abs(g.delta.max)):null).filter(v=>v!==null),strong=grouped.length?grouped.reduce((a,b)=>Math.abs(a.J)>Math.abs(b.J)?a:b):null;
 $('stats').replaceChildren(...[['原始记录 / 独立连接',base.length+' / '+independent.length],['距离组',chartGroups.length],['最强 |J|',strong?num(Math.abs(strong.J))+' meV · '+num(strong.r)+' Å':'—'],['四通道和 − 总 J',delta.length?(Math.max(...delta)<1e-10?'≈ 0 meV':num(Math.max(...delta))+' meV'):'未分组']].map(([title,value])=>{const card=element('div');card.className='stat';card.append(element('span',title),element('strong',value));return card;}));
 $('group-note').textContent=meta.orbital_count?'自动分组 '+ready+' / '+meta.atoms.length+' 个原子；按原 WF 子集汇总，未确定项单列。组间交换通道与 WF 的组间混合是不同概念。':'本次没有轨道分解，仅展示总 J。';
 message('先在当前筛选内平均互反项，再按距离组取平均；原始记录按 |J| 排序后分页。'+(f.mode==='orbital'?'高级单轨道模式使用所选矩阵元素和。':''));
 if(selected!==null&&!base.some(r=>r.id===selected)){$('detail').hidden=true;selected=null;}
}
function draw(){
 const names=f.pairs,facets=comparison,columns=facets?Math.max(1,Math.min(3,Math.floor(($('chart').clientWidth||800)/384))):1,rows=Math.max(1,Math.ceil(names.length/columns)),height=facets?rows*384:512;
 $('chart').style.height=height+'px';chart.resize();
 const seriesKeys=f.mode==='groups'&&meta.orbital_count?['total',...CHANNELS]:['total'];if(f.mode==='groups'&&(plotRows.some(b=>[b.i,b.j].some(atom=>!automatic.diagnostics.find(d=>d.atom===atom)?.ready))||chartGroups.some(g=>g.series.unknown&&Math.max(Math.abs(g.series.unknown.min),Math.abs(g.series.unknown.max))>1e-12)))seriesKeys.push('unknown');
 displayedKeys=seriesKeys;graphNames=names.length===1?groupNames(names[0],meta.atoms,siteContext):{total:'总 Jᵢⱼ',tt:'三重组 → 三重组',te:'三重组 → 二重组',et:'二重组 → 三重组',ee:'二重组 → 二重组',unknown:'未确定贡献'};
 if(f.mode==='orbital')graphNames.total='所选单轨道贡献';
 const ranges=chartGroups.flatMap(g=>seriesKeys.flatMap(k=>g.series[k]?[g.series[k].min,g.series[k].max]:[])),yMin=Math.min(0,...ranges),yMax=Math.max(0,...ranges),pad=Math.max((yMax-yMin)*.08,.001),xMax=f.rMax!==''?Number(f.rMax):Math.max(1,...chartGroups.map(g=>g.maxR))*1.03;
 const grid=[],xAxis=[],yAxis=[],title=[],series=[];
 for(let index=0;index<Math.max(1,names.length);index++){
  const col=index%columns,row=Math.floor(index/columns),width=100/columns,pair=names[index],data=chartGroups.map((g,gi)=>({...g,index:gi})).filter(g=>g.pair===pair);
  grid.push({left:(col*width+width*(facets ? .2 : .13))+'%',width:(width*(facets ? .72 : .81))+'%',top:row*(facets?384:512)+48,height:facets?256:352});
  xAxis.push({type:'value',gridIndex:index,min:0,max:xMax,name:'原子间距离 d / Å',nameLocation:'middle',nameGap:32});
  yAxis.push({type:'value',gridIndex:index,min:yMin-pad,max:yMax+pad,name:'J / meV',nameLocation:'middle',nameGap:facets?48:64,axisLabel:{formatter:v=>num(v)}});
  title.push({text:pairCaption(pair||'未选择元素对')+' · 距离组平均',left:(col*width+width*(facets ? .2 : .13))+'%',top:row*(facets?384:512)+8,textStyle:{fontSize:16,color:'hsl(215, 30%, 20%)'}});
  for(const key of seriesKeys){const color=seriesColors[key],name=graphNames[key];
   series.push({id:pair+':'+key,name,type:'line',smooth:false,connectNulls:false,xAxisIndex:index,yAxisIndex:index,symbol:'circle',symbolSize:8,lineStyle:{width:key==='total'?3:2,color},itemStyle:{color},data:data.map(g=>[g.r,g.series[key]?.mean??null,g.index])});
   series.push({id:pair+':'+key+':range',name,type:'custom',silent:true,xAxisIndex:index,yAxisIndex:index,tooltip:{show:false},renderItem:(_p,api)=>{const a=api.coord([api.value(0),api.value(1)]),b=api.coord([api.value(0),api.value(2)]);return {type:'group',children:[{type:'line',shape:{x1:a[0],y1:a[1],x2:b[0],y2:b[1]},style:{stroke:color,lineWidth:1,opacity:.5}},...[-1,1].map((_,j)=>({type:'line',shape:{x1:a[0]-4,y1:j?a[1]:b[1],x2:a[0]+4,y2:j?a[1]:b[1]},style:{stroke:color,lineWidth:1,opacity:.5}}))]};},encode:{x:0,y:[1,2]},data:data.filter(g=>g.series[key]).map(g=>[g.r,g.series[key].min,g.series[key].max,g.index])});
  }
 }
 chart.setOption({animation:false,title,grid,xAxis,yAxis,legend:{type:'scroll',bottom:0,data:seriesKeys.map(k=>graphNames[k]),selected:Object.fromEntries(seriesKeys.map(k=>[graphNames[k],legendVisible[k]!==false]))},tooltip:{trigger:'item',formatter:p=>{const g=chartGroups[p.data[2]],box=element('div');box.style.whiteSpace='pre-line';if(!g)return '';box.textContent=pairCaption(g.pair)+'\nd='+num(g.r)+' Å · '+g.connectionCount+' 个独立连接 / '+g.sourceCount+' 条原始项\n'+seriesKeys.filter(k=>g.series[k]).map(k=>graphNames[k]+': '+num(g.series[k].mean)+' meV ['+num(g.series[k].min)+', '+num(g.series[k].max)+']').join('\n')+'\n误差棒：同距离连接最小—最大范围；点击查看原始项';return box;}},dataZoom:facets?[]:[{type:'inside',filterMode:'none'},{type:'slider',bottom:48,height:16,filterMode:'none'}],series},{notMerge:true});
 $('chart-note').textContent='点为互反平均后的距离组算术平均，误差棒为同距离连接范围；连线仅引导阅读，不代表连续函数或数值精度。'+(f.mode==='groups'?'四个通道为原 WF 子空间贡献；总 J 单独保留。':'');
}
function selectDistanceGroup(group){$('raw-records').open=true;selectedShell=group;page=0;const ticket=epoch+1;void update().then(()=>{if(epoch!==ticket||selectedShell!==group)return;const members=all.filter(b=>group.memberIds.includes(b.id));if(members.length)void showBond(members.reduce((a,b)=>Math.abs(a.J)>Math.abs(b.J)?a:b).id).catch(error);}).catch(error);}
function renderShells(groups){
 const t=element('table'),head=element('thead'),tr=element('tr');for(const title of ['元素对','距离范围 / Å','平均 J / meV','最小—最大 / meV','独立 / 原始项','四通道和−总 J / meV','明细'])tr.append(element('th',title));head.append(tr);t.append(head);const body=element('tbody');
 for(const group of groups){const row=element('tr');if(selectedShell?.pair===group.pair&&selectedShell.minR===group.minR)row.className='selected';for(const value of [pairCaption(group.pair),num(group.minR)+'–'+num(group.maxR),num(group.series.total.mean),num(group.series.total.min)+'–'+num(group.series.total.max),group.connectionCount+' / '+group.sourceCount,num(group.delta?.mean)])row.append(element('td',value));const td=element('td'),button=element('button','查看连接');button.onclick=()=>selectDistanceGroup(group);td.append(button);row.append(td);body.append(row);}
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
chart.on('click',params=>{if(Array.isArray(params.data)&&chartGroups[params.data[2]])selectDistanceGroup(chartGroups[params.data[2]]);});
chart.on('legendselectchanged',event=>{for(const [key,name] of Object.entries(graphNames))legendVisible[key]=event.selected[name]!==false;});
$('prev').onclick=()=>{page--;table();};$('next').onclick=()=>{page++;table();};
$('sort').onchange=()=>{[sort,direction]=$('sort').value.split(':');tableRows=orderRows(tableRows,sort,direction);page=0;table();};
$('shell-tolerance').onchange=()=>{const value=Number($('shell-tolerance').value);if(!Number.isFinite(value)||value<0){error(Error('距离容差须为非负有限数'));return;}selectedShell=null;void update().catch(error);};
$('clear-shell').onclick=()=>{selectedShell=null;void update().catch(error);};
for(const name of ['facets','overlay'])$(name).onclick=()=>{comparison=name==='facets';if(!comparison)f.pairs=f.pairs.slice(0,1);$('facets').setAttribute('aria-pressed',String(comparison));$('overlay').setAttribute('aria-pressed',String(!comparison));selectedShell=null;controls();void update().catch(error);};
$('same-cell').onclick=()=>{f.Rx='0';f.Ry='0';f.Rz='0';selectedShell=null;page=0;controls();void update().catch(error);};
$('all-cells').onclick=()=>{f.Rx='';f.Ry='';f.Rz='';selectedShell=null;page=0;controls();void update().catch(error);};
$('reset').onclick=()=>{Object.assign(f,{pairs:Object.keys(meta.element_pairs).sort().slice(0,1),mode:'groups',atomI:'',atomJ:'',rMin:'',rMax:String(meta.default_max_distance_A||9),jMin:'',jMax:'',Rx:'',Ry:'',Rz:'',gi:'',gj:''});sort='absJ';direction='desc';selectedShell=null;selected=null;$('detail').hidden=true;page=0;syncSort();controls();void update().catch(error);};
$('png').onclick=()=>download('TB2J-Jij-vs-distance.png',chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#fff'}).split(',')[1],'image/png','base64');
$('heat-png').onclick=()=>{if(heat)download('TB2J-orbital-matrix.png',heat.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#fff'}).split(',')[1],'image/png','base64');};
$('csv').onclick=()=>download('TB2J-filtered-'+(f.mode==='orbital'?'orbitals':'exchange')+'.csv',csv(tableRows,f.mode==='orbital'),'text/csv;charset=utf-8');
$('matrix-csv').onclick=()=>download('TB2J-selected-orbital-matrix.csv',csv(activeMatrix,true),'text/csv;charset=utf-8');
$('groups-csv').onclick=()=>download('TB2J-distance-group-means.csv',groupCsv(chartGroups,graphNames,displayedKeys.filter(k=>legendVisible[k]!==false)),'text/csv;charset=utf-8');
$('mapping-download').onclick=()=>download('TB2J-orbital-labels.json',JSON.stringify({schema:'fpilot.tb2j-labels.v1',mapping,wf_identity_rows:meta.wf_identity_rows||[],spin_alignment:meta.wf_spin_alignment,original_index_base:1},null,2),'application/json');
function mergeMapping(){const merged=mappingMap(meta.automatic_mapping||[]);for(const row of overrides){const key=row.atom_index+':'+row.orbital_index;merged.set(key,{...merged.get(key),...row,source:'用户声明覆盖',status:'USER_DECLARED'});};mapping=[...merged.values()];map=mappingMap(mapping);automatic=automaticGroups(meta.wf_identity_rows||[],meta.atoms);}
function mappingEditor(rows){
 const values=mappingMap(rows),t=element('table'),h=element('tr');for(const title of ['原子','矩阵序号','Wannier 序号','手动覆盖标签','可选分组'])h.append(element('th',title));const head=element('thead');head.append(h);t.append(head);const body=element('tbody');
 for(const atom of meta.atoms)for(let oi=1;oi<=atom.orbitals;oi++){const row=element('tr'),existing=values.get(atom.index+':'+oi),global=meta.basis_rows.find(r=>r.atom_index===atom.index&&r.orbital_index===oi)?.wannier_index;row.append(element('td',atomLabel(atom.index)),element('td','orb_'+oi),element('td',global??'未记录'));for(const key of ['label','group']){const td=element('td'),input=element('input');input.type='text';input.maxLength=80;input.value=existing?.[key]||'';input.placeholder=map.get(atom.index+':'+oi)?.label||'可留空';input.dataset.atom=atom.index;input.dataset.orbital=oi;input.dataset.key=key;input.setAttribute('aria-label',atomLabel(atom.index)+' orb_'+oi+' '+(key==='label'?'标签':'分组'));input.oninput=()=>{$('confirmed').checked=false;};td.append(input);row.append(td);}body.append(row);}t.append(body);$('mapping').replaceChildren(t);$('confirmed').checked=false;
}
$('candidate').onclick=()=>{mappingEditor(meta.automatic_mapping||meta.candidate_mapping);$('mapping-status').textContent='已载入初始投影参考；自动显示本身无需手工填写或保存。';};
$('restore').onclick=()=>mappingEditor(meta.frozen_mapping);
$('save-mapping').onclick=async()=>{try{if(!$('confirmed').checked)throw Error('保存手动覆盖前，请确认基底与坐标轴');const entries=new Map();for(const input of $('mapping').querySelectorAll('input')){const key=input.dataset.atom+':'+input.dataset.orbital;if(!entries.has(key))entries.set(key,{atom_index:Number(input.dataset.atom),orbital_index:Number(input.dataset.orbital),label:'',group:''});entries.get(key)[input.dataset.key]=input.value;}const rows=[...entries.values()];if(rows.some(r=>r.group.trim()&&!r.label.trim()))throw Error('填写分组的项需要标签');const validated=validateMapping(rows.filter(r=>r.label.trim()),meta.atoms);$('save-mapping').disabled=true;const state=await bridge('save-state',{value:{...savedBase,schema:'fpilot.tb2j-labels.v1',mapping:validated,confirmed:true,...(identityAttachment?{wf_identity_report:compactIdentity(identityAttachment,meta.basis_rows)}:{})}});savedBase={...savedBase,mapping:validated,confirmed:true};overrides=validated;mergeMapping();$('mapping-status').textContent='手动覆盖已保存 · 版本 '+state.revision+'；原计算参数和产物保持。';orbitalControls();await update();if(selected!==null)await showBond(selected);}catch(e){error(e);}finally{$('save-mapping').disabled=false;}};

function applyIdentity(report){const identified=identityMapping(report,meta.model_bindings,meta.basis_rows);identityAttachment=report;siteContext=report.models[0]?.local_axes?.filter(s=>[4,6].includes(s.coordination))||siteContext;meta.wf_identity_rows=identified.rows;meta.automatic_mapping=identified.automatic;meta.candidate_mapping=identified.automatic;meta.wf_spin_alignment=identified.spin_alignment;}
function showIdentity(){
 const rows=meta.wf_identity_rows||[];$('wf-report-status').textContent=rows.length?'已关联最终WF参考重叠 · '+rows.length+' 个通道轨道；交换仍属于原WF基底。':'尚无最终WF报告；可导入匹配报告更新展示，初始投影名称仍为参考。';
 const body=element('table'),head=element('tr');for(const name of ['原子','orb','WF','通道','主要特征','自动轨道组','参考覆盖（非d纯度）','胞内占比','主要分量占比','d参考成分（原子参考内相对权重）'])head.append(element('th',name));body.append(head);
 for(const r of rows){const tr=element('tr'),g=automatic.map.get(r.atom_index+':'+r.orbital_index)?.group,cn=siteContext.find(s=>s.atom===r.atom_index)?.coordination,label=g==='t'?(cn===6?'t₂g':cn===4?'t₂':'三重组'):g==='e'?(cn===6?'e_g':cn===4?'e':'二重组'):'未确定';for(const v of [atomLabel(r.atom_index),'orb_'+r.orbital_index,'WF'+r.wannier_index,r.channel,r.label,label,percent(r.coverage),percent(r.home_fraction),percent(r.dominant_fraction),(r.components||[]).filter(c=>c.l===2).map(c=>c.label+' '+percent(c.relative_weight)).join('；')])tr.append(element('td',v));body.append(tr);} $('wf-report-table').replaceChildren(body);
 const alignment=meta.wf_spin_alignment;$('wf-spin-status').textContent=alignment?.status==='REFERENCE_OVERLAPS_AGREE'?'上下自旋参考复重叠一致；这仍不等于物理验证。':rows.length?'上下自旋共同基底未建立或未配对；不能仅凭相同轨道名称确认J的基底一致性。':'';
}
$('wf-report-file').onchange=async event=>{try{const file=event.target.files[0];if(!file)return;if(file.size>8*1024**2)throw Error('身份报告超过8 MiB');let report;try{report=JSON.parse(await file.text());}catch{throw Error('无法读取这份 JSON。请重新下载 Wannier 能级结果页的 wf-identity.json。');}applyIdentity(report);await bridge('save-state',{value:{...savedBase,wf_identity_report:compactIdentity(report,meta.basis_rows),mapping:overrides,confirmed:!!savedBase.confirmed}});mergeMapping();mappingEditor(overrides);showIdentity();orbitalControls();await update();if(selected!==null)await showBond(selected);}catch(e){$('wf-report-status').textContent=e.message;error(e);}finally{event.target.value='';}};

async function main(){
 meta=await part('index.json');if(meta.schema!=='fpilot.tb2j-view.v1')throw Error('无法识别绘图数据版本');
 f.pairs=Object.keys(meta.element_pairs).sort().slice(0,1);f.rMax=String(meta.default_max_distance_A||9);const [chunks,state]=await Promise.all([Promise.all(meta.exchange_parts.map(p=>part(p.path))),bridge('read-state')]);all=bonds(chunks.flat(),meta.atoms);if(all.length!==meta.exchange_count)throw Error('完整数据数量不一致，停止显示截断结果');
 savedBase=state.value||{};siteContext=meta.site_context||[];if(savedBase.wf_site_context&&JSON.stringify(savedBase.wf_site_context.bindings)===JSON.stringify(meta.model_bindings))siteContext=savedBase.wf_site_context.sites;syncSort();if(savedBase.wf_identity_report){try{applyIdentity(savedBase.wf_identity_report);}catch(e){error(Error('保存的身份报告未套用：'+e.message));}}
 overrides=validateMapping(state.value?.confirmed?state.value.mapping:meta.frozen_mapping,meta.atoms);mergeMapping();mappingEditor(overrides);const maximum=Math.max(0,...meta.atoms.map(a=>a.orbitals));f.oi=Array.from({length:maximum},(_,i)=>i+1);f.oj=[...f.oi];controls();
 $('summary').textContent=meta.exchange_count+' 条完整交换项 · '+meta.orbital_count+' 个轨道矩阵元素 · '+(meta.mode==='spinor'?'SOC':'共线')+' · meV / Å';$('calculation').textContent=(meta.calculation_max_distance_A?'本次计算最大距离：d < '+meta.calculation_max_distance_A+' Å。':'原运行未设置计算截断距离。')+' 当前是已有结果的显示筛选，修改不会重算。';
 $('basis').textContent=meta.basis_text||'未保存基底对应文件';$('projections').textContent=meta.projections||'未找到明确投影定义';$('mapping-status').textContent='自动投影参考 '+(meta.automatic_mapping?.length||0)+' 项；手动覆盖 '+overrides.length+' 项。';$('candidate').disabled=!meta.candidate_mapping.length;showIdentity();
 for(const note of meta.label_notes||[]){const p=element('p',note);p.className='help';$('label-notes').append(p);}for(const warning of meta.warnings){const p=element('p',warning);p.className='warning';$('validation').append(p);}$('validation').append(element('p','程序完成；数值收敛：'+(meta.validation.numerical_convergence||'NOT_EVALUATED')+'；物理验证：'+(meta.validation.physical||'NOT_PERFORMED')+'；方向验证：'+(meta.validation.direction_validation||'NOT_EVALUATED')));await update();
}
if(window.parent!==window)window.parent.postMessage({protocol,kind:'ready'},'*');main().catch(e=>{error(e);if(window.parent!==window)window.parent.postMessage({protocol,kind:'failed',error:e.message},'*');});
