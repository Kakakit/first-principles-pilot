export const PRESETS={
 'E–V':{x:'volume_a3',ys:['energy_sigma0_ev']},'P–V':{x:'volume_a3',ys:['external_pressure_gpa']},'V–P':{x:'external_pressure_gpa',ys:['volume_a3']},
 'a/b/c–P':{x:'external_pressure_gpa',ys:['a_ang','b_ang','c_ang']},'a/b/c–V':{x:'volume_a3',ys:['a_ang','b_ang','c_ang']},
 'c/a–P':{x:'external_pressure_gpa',ys:['c_over_a']},'最大力–V':{x:'volume_a3',ys:['force_max_ev_ang']},'最大力–P':{x:'external_pressure_gpa',ys:['force_max_ev_ang']}};
export function defaults(data){return {identity:data.identity,preset:'a/b/c–V',x:'volume_a3',ys:['a_ang','b_ang','c_ang'],normalization:'cell',pressureUnit:'GPa',mode:'scatter',energy:data.options.energy,model:data.options.model,groups:{},excluded:[],xmin:'',xmax:'',ymin:'',ymax:''};}
export function divisor(point,state){return state.normalization==='atom'?point.n_atoms:state.normalization==='formula'?point.formula_units:1;}
export function value(point,key,state,fields){const v=point.values[key];if(v===null||v===undefined||!Number.isFinite(v))return null;const kind=fields[key].kind;return (['energy','volume'].includes(kind)?v/divisor(point,state):v)*(kind==='pressure'&&state.pressureUnit==='kbar'?10:1);}
export function unit(key,state,fields){const field=fields[key];if(field.kind==='pressure')return state.pressureUnit;return field.unit+(['energy','volume'].includes(field.kind)?({atom:'/atom',formula:'/f.u.'}[state.normalization]||'/cell'):'');}
export function group(point,state){return String(state.groups[point.id]||point.group);}
export function normalizedFit(fit,reference,state){const factor=reference.n_atoms/divisor(reference,state);return {e0:fit.params.e0_ev_atom*factor,v0:fit.params.v0_a3_atom*factor,b0:fit.params.b0_gpa,b1:fit.params.b1,rmse:fit.rmse_ev_atom*factor};}
export function fitMatches(state,data){return ['energy','model'].every(k=>state[k]===data.fit_selection[k])&&data.points.every(p=>group(p,state)===String((data.fit_selection.groups||{})[p.id]||p.group))&&JSON.stringify([...state.excluded].sort())===JSON.stringify([...(data.fit_selection.excluded||[])].sort());}
export function validate(state,data){
 const fields=data.fields;
 if(!fields[state.x]||!Array.isArray(state.ys)||!state.ys.length||state.ys.length>12||state.ys.some(k=>!fields[k]))throw Error('请选择有效 X/Y 字段（最多 12 条 Y）');
 if(!['cell','atom','formula'].includes(state.normalization)||!['GPa','kbar'].includes(state.pressureUnit)||!['scatter','line'].includes(state.mode))throw Error('绘图设置无效');
 if(new Set(state.ys.map(k=>unit(k,state,fields))).size>1)throw Error('同一 Y 轴需要相同单位，请分别绘图');
 for(const key of ['xmin','xmax','ymin','ymax'])if(state[key]!==''&&!Number.isFinite(Number(state[key])))throw Error('轴范围必须是有限数值或留空');
 for(const [lo,hi] of [['xmin','xmax'],['ymin','ymax']])if(state[lo]!==''&&state[hi]!==''&&Number(state[lo])>=Number(state[hi]))throw Error('轴范围下限必须小于上限');
 if(!['energy_sigma0_ev','energy_without_entropy_ev','free_energy_ev'].includes(state.energy)||!['birch_murnaghan','vinet','murnaghan'].includes(state.model))throw Error('拟合选项无效');
 if(state.excluded.some(id=>!data.points.some(p=>p.id===id))||Object.entries(state.groups).some(([id,g])=>!data.points.some(p=>p.id===id)||typeof g!=='string'||g.length>80))throw Error('分组或排除点无效');
 return state;
}
export function series(data,state){
 const curves=new Map();
 for(const p of data.points){const x=value(p,state.x,state,data.fields);if(x===null)continue;for(const y of state.ys){const v=value(p,y,state,data.fields);if(v===null)continue;const name=group(p,state)+' · '+data.fields[y].label;if(!curves.has(name))curves.set(name,[]);curves.get(name).push({value:[x,v],source_id:p.id,label:p.label,excluded:state.excluded.includes(p.id)||p.fit_blockers.length>0});}}
 return [...curves].map(([name,points])=>({name,points:state.mode==='line'?[...points].sort((a,b)=>a.value[0]-b.value[0]):points}));
}
export function fitSeries(data,state){
 if(!fitMatches(state,data))return [];
 return data.fits.filter(f=>f.status==='FITTED').flatMap(f=>{
  const reference=data.points.find(p=>f.point_ids.includes(p.id));const factor=reference.n_atoms/divisor(reference,state),pf=state.pressureUnit==='kbar'?10:1;
  if(state.x==='volume_a3')return state.ys.flatMap(y=>y===f.energy?[{name:f.group+' · EOS '+f.model,points:f.curve.map(c=>[c.volume_a3_atom*factor,c.energy_ev_atom*factor])}]:y==='eos_pressure_gpa'?[{name:f.group+' · EOS '+f.model,points:f.curve.map(c=>[c.volume_a3_atom*factor,c.pressure_gpa*pf])}]:[]);
  if(state.x==='eos_pressure_gpa'&&state.ys.includes('volume_a3'))return [{name:f.group+' · EOS '+f.model,points:f.curve.map(c=>[c.pressure_gpa*pf,c.volume_a3_atom*factor]).sort((a,b)=>a[0]-b[0])}];
  return [];
 });
}
export function csvReport(data,state){
 const quote=x=>'"'+String(x??'').replaceAll('"','""')+'"';
 const keys=Object.keys(data.fields),fits=data.fits||[];
 const header=['source_id','name','job_id','step','group','n_atoms','formula_units',...keys.map(k=>k+' ['+unit(k,state,data.fields)+']'),...keys.map(k=>'raw_'+k+' ['+data.fields[k].unit+']'),'fit_blockers','user_excluded','normalization','x_field','y_fields','energy_field','model','fit_status','E0_selected_units','V0_selected_units','B0_GPa','B0_prime','RMSE_selected_energy_units'];
 const rows=data.points.map(p=>{const f=fits.find(f=>f.group===group(p,state)),n=f?.status==='FITTED'?normalizedFit(f,p,state):null;return [p.id,p.label,p.job_id,p.step,group(p,state),p.n_atoms,p.formula_units,...keys.map(k=>value(p,k,state,data.fields)),...keys.map(k=>p.values[k]),p.fit_blockers.join('; '),state.excluded.includes(p.id),state.normalization,state.x,state.ys.join(';'),state.energy,state.model,f?.status||'UNAVAILABLE',n?.e0,n?.v0,n?.b0,n?.b1,n?.rmse];});
 return '\uFEFF'+[header,...rows].map(row=>row.map(quote).join(',')).join('\r\n');
}

export function yOptions(data,state,selection){
 const chosenUnit=selection.length?unit(selection[0],state,data.fields):null;
 return Object.entries(data.fields).map(([key,field])=>{
  const selected=selection.includes(key),u=unit(key,state,data.fields);
  const available=data.points.some(p=>value(p,key,state,data.fields)!==null);
  const reason=!selected&&(!available?'全部来源未提供':chosenUnit&&u!==chosenUnit?'与当前 Y 轴单位不兼容':selection.length>=12?'最多选择 12 项':'');
  return {key,label:field.label,unit:u,selected,reason};
 });
}
export function batchGroup(state,data,ids,name){
 const text=name.trim();
 if(!ids.length)throw Error('请先勾选要批量命名的数据点');
 if(!text||text.length>80)throw Error('组名须为 1–80 个字符');
 if(ids.some(id=>!data.points.some(p=>p.id===id)))throw Error('批量选择含无效来源');
 state.groups={...state.groups,...Object.fromEntries(ids.map(id=>[id,text]))};
 return state;
}
