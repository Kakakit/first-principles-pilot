// Package-owned, pure display operations. Native values and pair identities survive.
export const pairName=(a,b)=>[a,b].sort().join('–');
export function bonds(raw,atoms){
 const symbols=new Map(atoms.map(a=>[a.index,a.symbol]));
 return raw.map(v=>({id:v[0],i:v[1],j:v[2],R:v.slice(3,6),r:v[6],J:v[7],DMI:v[8],Jani:v[9],si:symbols.get(v[1]),sj:symbols.get(v[2]),pair:pairName(symbols.get(v[1]),symbols.get(v[2]))}));
}
export function selectBonds(rows,f){
 return rows.filter(r=>f.pairs.includes(r.pair)&&(!f.atomI||r.i===Number(f.atomI))&&(!f.atomJ||r.j===Number(f.atomJ))&&(f.rMin===''||r.r>=Number(f.rMin))&&(f.rMax===''||r.r<=Number(f.rMax)));
}
export function selectJ(rows,f){return rows.filter(r=>(f.jMin===''||r.J>=Number(f.jMin))&&(f.jMax===''||r.J<=Number(f.jMax)));}
export function orderRows(rows,key='r',direction='asc'){
 const sign=direction==='desc'?-1:1;
 return [...rows].sort((a,b)=>{const x=a[key],y=b[key];const d=typeof x==='number'&&typeof y==='number'?x-y:String(x??'').localeCompare(String(y??''));return sign*d||a.id-b.id||(a.oi||0)-(b.oi||0)||(a.oj||0)-(b.oj||0);});
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
  return [{...b,J:v[3],totalJ:b.J,DMI:v[4],Jani:v[5],oi:v[1],oj:v[2],li:mi?.label||'',lj:mj?.label||'',gi:mi?.group||'',gj:mj?.group||''}];});
}
export function sumOrbitals(rows){
 const sums=new Map();for(const row of rows){const old=sums.get(row.id);if(old){old.J+=row.J;old.count++;}else sums.set(row.id,{...row,J:row.J,count:1});}
 return [...sums.values()];
}
export function csv(rows,orbital=false){
 const columns=['symbol_i','atom_i','symbol_j','atom_j','R','distance_A','J_meV',...(orbital?['orbital_i','label_i','group_i','orbital_j','label_j','group_j','total_J_meV']:[]),'DMI_meV','Jani_meV'];
 const quote=value=>'"'+String(value??'').replaceAll('"','""')+'"';
 const body=rows.map(r=>[r.si,r.i,r.sj,r.j,JSON.stringify(r.R),r.r,r.J,...(orbital?[r.oi,r.li,r.gi,r.oj,r.lj,r.gj,r.totalJ]:[]),r.DMI?JSON.stringify(r.DMI):'',r.Jani?JSON.stringify(r.Jani):''].map(quote).join(','));
 return '\ufeff'+columns.join(',')+'\n'+body.join('\n')+'\n';
}
