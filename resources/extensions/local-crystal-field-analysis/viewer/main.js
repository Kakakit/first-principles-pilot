const protocol='fpilot.extension-view.v1',pending=new Map(),cache=new Map();let serial=0,data,state,model,selection,series,current,epoch=0;
const $=id=>document.getElementById(id),esc=v=>String(v??'未提供').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),num=v=>Number.isFinite(v)?Number(v.toPrecision(8)).toString():'未提供',colors=['#2469aa','#c15a23','#23856d','#8c54ae','#bc4161','#776620'];
function rpc(kind,args={}){return new Promise((resolve,reject)=>{const id=String(++serial),timer=setTimeout(()=>{pending.delete(id);reject(Error('展示通信超时'));},20000);pending.set(id,{resolve,reject,timer});parent.postMessage({protocol,kind,id,...args},'*');});}
addEventListener('message',e=>{if(e.source!==parent||e.data?.protocol!==protocol||e.data.kind!=='response')return;const p=pending.get(e.data.id);if(p){clearTimeout(p.timer);pending.delete(e.data.id);e.data.error?p.reject(Error(e.data.error)):p.resolve(e.data.value);}});
async function file(path){if(!cache.has(path)){cache.set(path,rpc('read-file',{path}));if(cache.size>24)cache.delete(cache.keys().next().value);}try{return await cache.get(path);}catch(e){cache.delete(path);throw e;}}
const choices=(id,items,value)=>{$(id).innerHTML=items.map(([k,t])=>`<option value="${esc(k)}">${esc(t)}</option>`).join('');$(id).value=String(items.some(([k])=>String(k)===String(value))?value:items[0]?.[0]??'');return $(id).value;};
function table(head,rows){return '<table><thead><tr>'+head.map(v=>'<th>'+esc(v)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';}
function hAtoms(m){const ids=[...new Set(m.reports.map(r=>r.atom))];return ids.map(atom=>({atom,element:m.reports.find(r=>r.atom===atom).element,shells:m.reports.map((r,i)=>({...r,id:String(i)})).filter(r=>r.atom===atom)}));}
function groupKey(){return [data.mode,state.model,state.atom,state.shell,state.spin,state.kind].join(':');}
const fail=e=>{$('error').textContent=e.message||String(e);};
async function load(){
 const ticket=++epoch;$('error').textContent='';$('status').textContent='正在加载所选冻结数据…';
 const loaded=data.mode==='pdos'?data.models[state.model]:await file(data.models[state.model].file);if(ticket!==epoch)return;model=loaded;
 const atoms=data.mode==='pdos'?model.atoms:hAtoms(model);
 state.atom=Number(choices('atom',atoms.map(a=>[String(a.atom),a.element+' 第 '+a.atom+' 号'+(a.shells.find(s=>s.polyhedron)?.polyhedron?' · '+a.shells.find(s=>s.polyhedron).polyhedron:'')]),state.atom));const atom=atoms.find(a=>a.atom===state.atom);
 state.shell=choices('shell',atom.shells.map(s=>[data.mode==='pdos'?String(s.l):s.id,'spd'[s.l]+(data.mode==='hamiltonian'?' · 径向 '+s.radial:'')+' ('+(data.mode==='pdos'?s.labels.length:s.orbital_labels.length)+' 轨道)']),state.shell);
 selection=atom.shells.find(s=>(data.mode==='pdos'?String(s.l):s.id)===state.shell);
 if(!selection)throw Error('来源没有可分辨的 s/p/d 壳层');
 const spins=data.mode==='pdos'?selection.spins.map((s,i)=>[String(i),s.label]):[['0',model.spinors?'SOC / spinor':model.seed?.endsWith('.2')?'↓（独立模型）':model.seed?.endsWith('.1')?'↑（独立模型）':'当前模型']];
 state.spin=Number(choices('spin',spins,state.spin));
 $('kind-label').hidden=data.mode==='pdos';$('kind').value=state.kind;$('pdos-panel').hidden=data.mode!=='pdos';$('h-advanced').hidden=data.mode==='pdos';
 if(data.mode==='pdos'){
  const key=state.model+':'+state.atom+':'+state.shell;const parts=await Promise.all(selection.chunks.map(async c=>(await file(c.path)).parts[c.key]));if(ticket!==epoch)return;
  series={energy:parts.flatMap(p=>p.energy_ev),density:selection.spins.map((_,si)=>selection.labels.map((_,oi)=>parts.flatMap(p=>p.density[si][oi])))};
  if(state.windowKey!==key){state.windowKey=key;state.lower=model.range_ev[0];state.upper=model.range_ev[1];}
  for(const [id,value] of [['lower',state.lower],['upper',state.upper],['lower-slider',state.lower],['upper-slider',state.upper]]){$(id).min=model.range_ev[0];$(id).max=model.range_ev[1];$(id).value=value;}
 }else{
  const frames=[['raw','原始 Wannier 基底']];if(selection.global_axes_candidate)frames.push(['global','全局轴候选']);if(selection.coordination_candidate)frames.push(['coordination','配位局域轴候选']);state.frame=choices('frame',frames,state.frame);
 }
 $('identity').textContent=(model.source.name||model.seed||'计算来源')+' · '+atom.element+atom.atom+' · '+(data.mode==='pdos'?'VASP 全局轨道坐标':'Wannier 子壳局域分析')+(data.mode==='hamiltonian'&&!selection.complete_shell?' · 当前模型仅含部分壳层，不补造缺失轨道':'');
 $('warnings').innerHTML=(model.warnings||[]).map(w=>'<p>'+esc(w)+'</p>').join('');
 $('evidence').textContent=JSON.stringify(data.mode==='pdos'?{source:model.source,settings:model.settings,evidence:model.evidence,atom_lines:atom.evidence_lines}:{source:model.source,seed:model.seed,mapping_evidence:model.mapping_evidence,mapping:model.mapping,centres:model.centres,localization:model.localization,whole_atom_block:model.site_blocks?.find(a=>a.atom===state.atom),hr:model.hr_evidence},(key,value)=>/hash|sha256|digest/i.test(key)?undefined:value,2);
 if(ticket===epoch){draw();$('status').textContent='冻结数据已加载；更改区间、单位和分组在页面内计算。';}
}
function metrics(){return state.frame==='coordination'&&selection.coordination_candidate?selection.coordination_candidate.metrics:state.frame==='global'&&selection.global_axes_candidate?selection.global_axes_candidate:selection.raw;}
function draw(){
 $('error').textContent='';$('coordination-summary').textContent='';$('unit').value=state.unit;$('reference').value=state.reference;
 let labels,values,moments=null,h=null;
 try{
  if(data.mode==='pdos'){
   moments=series.density[state.spin].map(y=>moment(series.energy,y,state.lower,state.upper));values=moments.map(m=>m.centroid_ev);labels=selection.labels;
   $('window-note').textContent=`积分范围 ${num(state.lower)} 至 ${num(state.upper)} eV（相对 E_F）。`+(moments.some(m=>m.negative_values)?' 含负 DOS，按原值积分。':'')+(moments.some(m=>m.centroid_ev==null)?' 有轨道积分权重非正，重心未提供。':'')+(values.some(v=>Number.isFinite(v)&&(v<state.lower||v>state.upper))?' 重心在区间外，检查负谱权重。':'');
   drawDos();$('level-title').textContent='PDOS 轨道重心参考';$('weights').textContent='PDOS 是谱权重分布，不提供局域本征矢组成。';
  }else{
   h=metrics();values=hLevels(h,state.kind);labels=state.kind==='diagonal'?selection.orbital_labels:values.map((_,i)=>'能级 '+(i+1));
   $('level-title').textContent=state.kind==='diagonal'?'轨道平均能量 Hₘₘ':'局域本征能级';
   $('weights').innerHTML=table(['初始轨道基底',...h.eigenvalues_ev.map((_,i)=>'能级 '+(i+1))],h.weights.map((row,i)=>[selection.orbital_labels[i],...row.map(v=>num(v*100)+'%')]));
   $('matrix').innerHTML=table(['H / '+state.unit,...selection.orbital_labels],h.matrix.real.map((row,i)=>[selection.orbital_labels[i],...row.map((v,j)=>num(v*(state.unit==='meV'?1000:1))+(h.matrix.imag[i][j]?' + ('+num(h.matrix.imag[i][j]*(state.unit==='meV'?1000:1))+')i':''))]));
   $('diagnostic').textContent=selection.coordination_candidate?'当前多面体 '+(selection.polyhedron||'已指定')+'；局域轴由真实配体方向拟合。能级解释仍以最终 Wannier 轨道身份成立为前提。':'未设置配位架；一般壳层能级不要求填写配体或配位数。';
   const cp=selection.coordination_candidate?.metrics.cubic_partition;
   if(cp){const cn=selection.coordination_candidate.coordination,f=state.unit==='meV'?1000:1;const delta=cp.eg_minus_t2g_ev*(cn===4?-1:1);$('coordination-summary').textContent=(selection.polyhedron||'已指定多面体')+' · 局域配位架近似分组：'+(cn===4?'Δtet = ⟨t₂⟩ − ⟨e⟩':'Δoct = ⟨e_g⟩ − ⟨t₂g⟩')+' = '+num(delta*f)+' '+state.unit+'。基于最终 WF 身份与方向假设；不是纯静电晶场。';}

  }
 }catch(e){current=null;$('levels').innerHTML='';$('spectrum').textContent='当前区间无效，未生成能级。';$('groups').textContent='';$('pair-result').textContent='';fail(e);return;}
 const relative=relativeLevels(values,state.reference),factor=state.unit==='meV'?1000:1;
 current={mode:data.mode,source:model.source,atom:state.atom,shell:selection.l,labels,values_ev:values,display_ev:relative,moments,unit:state.unit,reference:state.reference,frame:data.mode==='pdos'?'VASP global':state.frame,kind:data.mode==='pdos'?'spectral_centroid':state.kind,range_ev:data.mode==='pdos'?[state.lower,state.upper]:null};
 const order=values.map((_,i)=>i).filter(i=>Number.isFinite(values[i])).sort((a,b)=>values[a]-values[b]);
 $('spectrum').innerHTML=table(['条目','原始能量 / '+state.unit,'图中能量 / '+state.unit,'至下一条 / '+state.unit,...(moments?['积分权重 / states']:['主要轨道组成'])],values.map((v,i)=>{
  const rank=order.indexOf(i),next=rank>=0?order[rank+1]:undefined;
  const mix=h&&state.kind==='eigen'?h.weights.map((row,j)=>({label:selection.orbital_labels[j],w:row[i]})).sort((a,b)=>b.w-a.w).slice(0,3).map(v=>v.label+' '+num(v.w*100)+'%').join('；'):h?'指定基底轨道':'',extra=moments?num(moments[i].weight):mix;
  return [labels[i],num(v==null?null:v*factor),num(relative[i]==null?null:relative[i]*factor),next===undefined?'—':num((values[next]-v)*factor),extra];
 }));
 const valid=relative.filter(Number.isFinite),lo=valid.length?Math.min(...valid):0,hi=valid.length?Math.max(...valid):1,span=Math.max(hi-lo,.01),yy=v=>265-(v-lo)/span*200;
 const n=values.length,w=740/Math.max(n,1);
 $('levels').innerHTML=`<rect width="900" height="350" fill="white"/><text x="24" y="25" font-size="17" fill="#243b53">${esc($('level-title').textContent)} · ${esc(model.source.name||model.seed||'来源')} · 原子 ${state.atom} · ${state.unit}</text><text x="24" y="48" font-size="12">${esc(state.reference==='centroid'?'零点：当前有效条目平均能量':'原始参考：'+(data.mode==='pdos'?'E_F':'Wannier Hamiltonian'))}</text>`+relative.map((v,i)=>{const x=80+i*w;return Number.isFinite(v)?`<line x1="${x}" x2="${x+w*.65}" y1="${yy(v)}" y2="${yy(v)}" stroke="${colors[i%colors.length]}" stroke-width="4"/><text x="${x}" y="${yy(v)-10}" font-size="13">${num(v*factor)}</text><text x="${x}" y="305" font-size="12">${esc(labels[i])}</text>`:`<text x="${x}" y="305" font-size="12">${esc(labels[i])}：未提供</text>`;}).join('');
 choices('pair-a',labels.map((label,i)=>[String(i),label]),state.pairA??0);choices('pair-b',labels.map((label,i)=>[String(i),label]),state.pairB??Math.min(1,labels.length-1));pair();
 $('group-options').innerHTML=labels.map((label,i)=>`<label><input type="checkbox" value="${i}" ${values[i]==null?'disabled':''}>${esc(label)}</label>`).join('');drawGroups();
}
function pair(){if(!current)return;state.pairA=Number($('pair-a').value);state.pairB=Number($('pair-b').value);const a=current.values_ev[state.pairA],b=current.values_ev[state.pairB];$('pair-result').textContent='B − A = '+num(Number.isFinite(a)&&Number.isFinite(b)?(b-a)*(state.unit==='meV'?1000:1):null)+' '+state.unit;}
function drawDos(){
 const x=series.energy,ys=series.density[state.spin],bounds=ys.reduce((b,y)=>y.reduce((b,v)=>[Math.min(b[0],v),Math.max(b[1],v)],b),[0,1e-12]),[ymin,ymax]=bounds,xr=x.at(-1)-x[0],yr=ymax-ymin||1,px=v=>65+(v-x[0])/xr*790,py=v=>245-(v-ymin)/yr*210;
 const paths=ys.map((y,j)=>`<path d="${y.map((v,i)=>(i?'L':'M')+num(px(x[i]))+','+num(py(v))).join(' ')}" fill="none" stroke="${colors[j%colors.length]}" stroke-width="1.5"/>`).join('');
 $('dos').innerHTML=`<rect width="900" height="300" fill="white"/><rect x="${px(state.lower)}" y="30" width="${px(state.upper)-px(state.lower)}" height="215" fill="#dceafa"/><line x1="65" x2="855" y1="245" y2="245" stroke="#567"/>${paths}<text x="65" y="275">${num(x[0])}</text><text x="790" y="275">${num(x.at(-1))} eV</text><text x="65" y="20">PDOS / states·eV⁻¹ · 能量 E − E_F</text><text x="5" y="45">${num(ymax)}</text><text x="5" y="245">${num(ymin)}</text>`;
 $('legend').innerHTML=selection.labels.map((label,i)=>`<span style="color:${colors[i%colors.length]}">━ ${esc(label)}</span>`).join('');
}
function drawGroups(){if(!current)return;const gs=state.groups[groupKey()]||[],f=state.unit==='meV'?1000:1,means=gs.map(g=>groupMean(current.values_ev,g.indices));$('groups').innerHTML=gs.map((g,i)=>`<p>${esc(g.name)}：${g.indices.map(j=>esc(current.labels[j])).join('、')}；重心 ${num(means[i]==null?null:means[i]*f)} ${state.unit}（原始参考） <button data-remove="${i}">移除</button></p>`).join('')+(gs.length>=2?'<p>'+gs.slice(1).map((g,i)=>esc(g.name)+' − '+esc(gs[0].name)+' = '+num(means[i+1]!=null&&means[0]!=null?(means[i+1]-means[0])*f:null)+' '+state.unit).join('；')+'</p>':'');$('groups').querySelectorAll('button').forEach(b=>b.onclick=()=>{gs.splice(Number(b.dataset.remove),1);drawGroups();});}
function exportData(){if(!current)throw Error('当前区间无效，无法导出');return {schema:'fpilot.orbital-level-selection.v1',...current,view:state,groups:(state.groups[groupKey()]||[]).map(g=>({...g,centroid_ev:groupMean(current.values_ev,g.indices)})),evidence:data.mode==='pdos'?model.evidence:model.hr_evidence,...(data.mode==='pdos'?{energy_ev:series.energy,density:series.density[state.spin],settings:model.settings}:{metrics:metrics(),mapping:model.mapping,options:model.options,transforms:selection.transforms}),warnings:model.warnings,identity:data.identity};}
function download(name,media,content,encoding='text'){return rpc('download',{name,media_type:media,encoding,content});}
async function boot(){
 data=await file('data.json');state={identity:data.identity,model:0,atom:null,shell:'2',spin:0,kind:'eigen',frame:'coordination',unit:'eV',reference:'centroid',groups:{}};
 const saved=(await rpc('read-state')).value;if(saved?.identity===data.identity&&Number.isInteger(saved.model)&&saved.model>=0&&saved.model<data.models.length){state={...state,...saved};if(!['eigen','diagonal'].includes(state.kind))state.kind='eigen';if(!['raw','centroid'].includes(state.reference))state.reference='centroid';if(!['eV','meV'].includes(state.unit))state.unit='eV';if(!state.groups||Array.isArray(state.groups)||typeof state.groups!=='object')state.groups={};}
 for(const mode of ['pdos','hamiltonian']){$(mode+'-tab').disabled=mode!==data.mode;$(mode+'-tab').setAttribute('aria-selected',String(mode===data.mode));$(mode+'-tab').title=mode===data.mode?'当前分析模式':'请在对应 DOS/SCF 或 Wannier90 结果任务中创建此模式的分析';}
 $('mode-note').textContent=data.mode==='pdos'?'DOS 完成即可查看，不需要 Wannier90 或 hr.dat。图中是所选区间的轨道谱重心，不是局域本征能级。':'从已有 hr.dat 提取局域壳层矩阵。默认展示本征能级和混合组成，不强制 t₂g/e_g 分组。';
 choices('model',data.models.map((m,i)=>[String(i),m.source.name||m.seed||'来源 '+(i+1)]),state.model);await load();
 for(const id of ['model','atom','shell','spin'])$(id).onchange=()=>{state[id]=['model','atom','spin'].includes(id)?Number($(id).value):$(id).value;if(id==='model'){state.atom=null;state.shell='2';}if(id==='atom')state.shell='2';load().catch(fail);};
 for(const id of ['frame','kind','unit','reference'])$(id).onchange=()=>{state[id]=$(id).value;draw();};
 for(const id of ['lower','upper','lower-slider','upper-slider'])$(id).oninput=()=>{const key=id.startsWith('lower')?'lower':'upper';state[key]=$(id).value.trim()===''?NaN:Number($(id).value);$(key).value=Number.isFinite(state[key])?state[key]:'';$(key+'-slider').value=state[key];draw();};
 $('full-range').onclick=()=>{state.lower=model.range_ev[0];state.upper=model.range_ev[1];for(const key of ['lower','upper']){$(key).value=state[key];$(key+'-slider').value=state[key];}draw();};
 for(const id of ['pair-a','pair-b'])$(id).onchange=pair;
 $('add-group').onclick=()=>{try{if(!current)throw Error('先设置有效积分区间');const indices=[...$('group-options').querySelectorAll('input:checked')].map(i=>Number(i.value)),name=$('group-name').value.trim();if(!name||!indices.length)throw Error('请命名并至少勾选一条能级');const gs=state.groups[groupKey()]||[];if(gs.some(g=>g.name===name))throw Error('当前组名已存在');gs.push({name,indices});state.groups[groupKey()]=gs;$('error').textContent='';drawGroups();}catch(e){fail(e);}};
 $('save').onclick=()=>{if(!current)return fail(Error('当前区间无效，无法保存'));rpc('save-state',{value:state}).then(()=>$('status').textContent='展示选择已保存。').catch(fail);};
 $('json').onclick=()=>{try{download('orbital-levels.json','application/json',JSON.stringify(exportData(),null,2)).catch(fail);}catch(e){fail(e);}};
 $('csv').onclick=()=>{try{const d=exportData(),f=state.unit==='meV'?1000:1,q=v=>'"'+String(v??'').replaceAll('"','""')+'"',rows=[['source','mode','atom','l','spin_channel','kind','frame','label','raw_energy','display_energy','unit','weight_states','lower_eV_EF','upper_eV_EF','evidence_sha256']];d.labels.forEach((label,i)=>rows.push([d.source.name,d.mode,d.atom,d.shell,$('spin').selectedOptions[0].textContent,d.kind,d.frame,label,d.values_ev[i]==null?'':d.values_ev[i]*f,d.display_ev[i]==null?'':d.display_ev[i]*f,state.unit,d.moments?.[i].weight,d.range_ev?.[0],d.range_ev?.[1],data.mode==='pdos'?model.evidence.doscar_sha256:model.hr_evidence.sha256]));d.groups.forEach(g=>rows.push([d.source.name,d.mode,d.atom,d.shell,'','group_mean',d.frame,g.name,g.centroid_ev==null?'':g.centroid_ev*f,'',state.unit,'',d.range_ev?.[0],d.range_ev?.[1],'']));download('orbital-levels.csv','text/csv;charset=utf-8','\uFEFF'+rows.map(row=>row.map(q).join(',')).join('\r\n')).catch(fail);}catch(e){fail(e);}};
 $('png').onclick=async()=>{try{exportData();const svg=new XMLSerializer().serializeToString($('levels')),url=URL.createObjectURL(new Blob([svg],{type:'image/svg+xml'})),image=new Image();try{await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=reject;image.src=url;});const canvas=document.createElement('canvas');canvas.width=1800;canvas.height=700;canvas.getContext('2d').drawImage(image,0,0,1800,700);await download('orbital-levels.png','image/png',canvas.toDataURL('image/png').split(',')[1],'base64');}finally{URL.revokeObjectURL(url);}}catch(e){fail(e);}};
 new ResizeObserver(()=>parent.postMessage({protocol,kind:'resize',height:document.body.scrollHeight},'*')).observe(document.body);
}
boot().catch(e=>{fail(e);parent.postMessage({protocol,kind:'failed',error:e.message},'*');});
