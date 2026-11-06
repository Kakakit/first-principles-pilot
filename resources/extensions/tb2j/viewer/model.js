// Package-owned, pure display operations. Native values and pair identities survive.
export const pairName=(a,b)=>[a,b].sort().join('–');
export function bonds(raw,atoms){
 const symbols=new Map(atoms.map(a=>[a.index,a.symbol]));
 return raw.map(v=>({id:v[0],i:v[1],j:v[2],R:v.slice(3,6),r:v[6],J:v[7],DMI:v[8],Jani:v[9],si:symbols.get(v[1]),sj:symbols.get(v[2]),pair:pairName(symbols.get(v[1]),symbols.get(v[2]))}));
}
export function selectBonds(rows,f){
 return rows.filter(r=>f.pairs.includes(r.pair)&&(!f.atomI||r.i===Number(f.atomI))&&(!f.atomJ||r.j===Number(f.atomJ))&&(f.rMin===''||r.r>=Number(f.rMin))&&(f.rMax===''||r.r<Number(f.rMax))&&['Rx','Ry','Rz'].every((key,index)=>f[key]===undefined||f[key]===''||r.R[index]===Number(f[key])));
}
export function selectJ(rows,f){return rows.filter(r=>(f.jMin===''||r.J>=Number(f.jMin))&&(f.jMax===''||r.J<=Number(f.jMax)));}
export function orderRows(rows,key='r',direction='asc'){
 const sign=direction==='desc'?-1:1;
 return [...rows].sort((a,b)=>{const x=key==='absJ'?Math.abs(a.J):a[key],y=key==='absJ'?Math.abs(b.J):b[key];const d=typeof x==='number'&&typeof y==='number'?x-y:String(x??'').localeCompare(String(y??''));return sign*d||a.id-b.id||(a.oi||0)-(b.oi||0)||(a.oj||0)-(b.oj||0);});
}
export function validateMapping(rows,atoms){
 const bounds=new Map(atoms.map(a=>[a.index,a.orbitals]));const seen=new Set();
 for(const row of rows){
  const k=row.atom_index+':'+row.orbital_index;
  if(!Number.isInteger(row.atom_index)||!Number.isInteger(row.orbital_index)||row.orbital_index<1||!bounds.has(row.atom_index)||row.orbital_index>bounds.get(row.atom_index)||seen.has(k)||typeof row.label!=='string'||!row.label.trim()||row.label.length>80||typeof(row.group??'')!=='string'||(row.group||'').length>80)throw Error('轨道映射含重复、越界、空标签或过长名称');
  seen.add(k);
 }
 return rows.map(r=>({...r,label:r.label.trim(),group:(r.group||'').trim()}));
}
export const mappingMap=rows=>new Map(rows.map(r=>[r.atom_index+':'+r.orbital_index,r]));
export function orbitalRows(entries,bondRows,oi,oj,map,gi='',gj=''){
 const lookup=new Map(bondRows.map(r=>[r.id,r]));
 return entries.flatMap(v=>{const b=lookup.get(v[0]);if(!b||!oi.includes(v[1])||!oj.includes(v[2]))return [];
  const mi=map.get(b.i+':'+v[1]),mj=map.get(b.j+':'+v[2]);if(gi&&mi?.group!==gi||gj&&mj?.group!==gj)return [];
  return [{...b,J:v[3],totalJ:b.J,DMI:v[4],Jani:v[5],oi:v[1],oj:v[2],li:mi?.label||'',lj:mj?.label||'',gi:mi?.group||'',gj:mj?.group||'',wfi:mi?.wannier_index,wfj:mj?.wannier_index,ei:mi?.source||'原始序号',ej:mj?.source||'原始序号',ci:mi?.components_by_channel||mi?.components,cj:mj?.components_by_channel||mj?.components}];});
}
export function sumOrbitals(rows){
 const sums=new Map();for(const row of rows){const old=sums.get(row.id);if(old){old.J+=row.J;old.count++;}else sums.set(row.id,{...row,J:row.J,count:1});}
 return [...sums.values()];
}
export function csv(rows,orbital=false){
 const columns=['symbol_i','atom_i','symbol_j','atom_j','R','distance_A','J_meV',...(orbital?['orbital_i','label_i','group_i','orbital_j','label_j','group_j','total_J_meV','wf_i','wf_j','evidence_i','evidence_j','components_i','components_j']:[]),'DMI_meV','Jani_meV'];
 const quote=value=>'"'+String(value??'').replaceAll('"','""')+'"';
 const body=rows.map(r=>[r.si,r.i,r.sj,r.j,JSON.stringify(r.R),r.r,r.J,...(orbital?[r.oi,r.li,r.gi,r.oj,r.lj,r.gj,r.totalJ,r.wfi,r.wfj,r.ei,r.ej,r.ci?JSON.stringify(r.ci):'',r.cj?JSON.stringify(r.cj):'']:[]),r.DMI?JSON.stringify(r.DMI):'',r.Jani?JSON.stringify(r.Jani):''].map(quote).join(','));
 return '\ufeff'+columns.join(',')+'\n'+body.join('\n')+'\n';
}
export function shells(rows,tolerance=0.001){
 const output=[];
 for(const pair of [...new Set(rows.map(r=>r.pair))].sort()){
  const sorted=orderRows(rows.filter(r=>r.pair===pair),'r');let current=[];
  const flush=()=>{if(!current.length)return;const values=current.map(r=>r.J).sort((a,b)=>a-b),middle=Math.floor(values.length/2);output.push({pair,minR:current[0].r,maxR:current.at(-1).r,minJ:values[0],maxJ:values.at(-1),medianJ:values.length%2?values[middle]:(values[middle-1]+values[middle])/2,count:current.length,representative:current.reduce((a,b)=>Math.abs(b.J-values[middle])<Math.abs(a.J-values[middle])?b:a).id});};
  for(const row of sorted){if(current.length&&row.r-current[0].r>tolerance){flush();current=[];}current.push(row);}flush();
 }
 return output;
}
export function validateFilters(f){
 for(const key of ['rMin','rMax','jMin','jMax'])if(f[key]!==''&&f[key]!==undefined&&!Number.isFinite(Number(f[key])))throw Error('筛选值必须为有限数字');
 if(f.rMin!==''&&Number(f.rMin)<0||f.rMax!==''&&Number(f.rMax)<=0)throw Error('距离下界不能为负，上界须大于零');
 if(f.rMin!==''&&f.rMax!==''&&Number(f.rMin)>=Number(f.rMax))throw Error('距离下界须小于上界');
 if(f.jMin!==''&&f.jMax!==''&&Number(f.jMin)>Number(f.jMax))throw Error('J 下界不能大于上界');
}
