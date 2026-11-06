// Native WF matrix sums and reversible display aggregation; no probability weighting.
export const percent=value=>Number.isFinite(value)?(value*100).toFixed(4)+'%':'—';
const mean=xs=>xs.reduce((s,x)=>s+x,0)/xs.length;
const T=new Set(['dxy','dxz','dyz']),E=new Set(['dz2','dx2-y2']);
export const CHANNELS=['tt','te','et','ee'];
export function automaticGroups(rows,atoms){
 const map=new Map(),diagnostics=[];const channels=[...new Set(rows.map(r=>r.channel))];
 for(const a of atoms){let reason='',candidates=[];
  if(channels.length!==2||a.orbitals!==5)reason='需两通道完整五维d参考壳';
  for(let oi=1;oi<=a.orbitals;oi++){
   const selected=channels.map(ch=>rows.filter(r=>r.atom_index===a.index&&r.orbital_index===oi&&r.channel===ch));let group=null,confidence=0;
   if(!reason&&selected.every(s=>s.length===1)){
    const scores=selected.map(([r])=>{const t=(r.components||[]).filter(c=>c.l===2&&T.has(c.label)).reduce((s,c)=>s+c.relative_weight,0),e=(r.components||[]).filter(c=>c.l===2&&E.has(c.label)).reduce((s,c)=>s+c.relative_weight,0);return {r,t,e,g:t>=e?'t':'e',score:Math.max(t,e)};});
    const eligible=scores.every(s=>Number.isFinite(s.score)&&s.score>=.99&&s.r.coverage>=.8&&s.r.owner_fraction>=.9&&s.r.status!=='ATOM_ASSIGNMENT_CONFLICT')&&scores[0].g===scores[1].g;
    if(eligible){group=scores[0].g;confidence=Math.min(...scores.map(s=>s.score));}
   }
   candidates.push({atom:a.index,orbital:oi,group,confidence});
  }
  if(!reason&&(candidates.filter(r=>r.group==='t').length!==3||candidates.filter(r=>r.group==='e').length!==2))reason='未同时满足99%组内权重、80%覆盖、90%原子归属、通道一致及3+2维数';
  for(const r of candidates)map.set(a.index+':'+r.orbital,{...r,group:reason?null:r.group});
  diagnostics.push({atom:a.index,symbol:a.symbol,ready:!reason,reason,minConfidence:reason?null:Math.min(...candidates.map(r=>r.confidence))});
 }
 return {map,diagnostics};
}
export function groupBonds(bonds,entries,membership,atoms){
 const byid=new Map(bonds.map(b=>[b.id,{...b,values:{tt:0,te:0,et:0,ee:0,unknown:0},matrixSum:0,cells:new Set()}]));
 for(const v of entries){const b=byid.get(v[0]);if(!b)continue;const key=v[1]+':'+v[2];if(b.cells.has(key))throw Error('轨道矩阵元素重复');if(!Number.isFinite(v[3]))throw Error('轨道交换数值无效');b.cells.add(key);b.matrixSum+=v[3];const gi=membership.get(b.i+':'+v[1])?.group,gj=membership.get(b.j+':'+v[2])?.group;b.values[gi&&gj?gi+gj:'unknown']+=v[3];}
 const sizes=new Map(atoms.map(a=>[a.index,a.orbitals]));
 return [...byid.values()].map(b=>{const expected=sizes.get(b.i)*sizes.get(b.j),complete=expected>0&&b.cells.size===expected;return {...b,complete,matrixSum:complete?b.matrixSum:null,values:Object.fromEntries(Object.entries(b.values).map(([k,v])=>[k,complete?v:null]))};});
}
function oriented(b){
 const reverse=b.si>b.sj||b.si===b.sj&&(b.i>b.j||b.i===b.j&&b.R.join(',')>b.R.map(x=>-x).join(','));
 const i=reverse?b.j:b.i,j=reverse?b.i:b.j,R=reverse?b.R.map(v=>-v):b.R;
 return {...b,i,j,R,key:JSON.stringify([i,j,R]),values:{...b.values,te:b.values?.[reverse?'et':'te']??null,et:b.values?.[reverse?'te':'et']??null}};
}
export function reciprocalMeans(rows){
 const bins=new Map(),seen=new Set();
 for(const original of rows){const native=JSON.stringify([original.i,original.j,original.R]);if(seen.has(native))throw Error('重复的原始交换键');seen.add(native);const b=oriented(original);if(!bins.has(b.key))bins.set(b.key,[]);bins.get(b.key).push({...b,native:original});}
 return [...bins.values()].map(members=>{const b=members[0],values=Object.fromEntries([...CHANNELS,'unknown'].map(k=>[k,members.every(v=>Number.isFinite(v.values?.[k]))?mean(members.map(v=>v.values[k])):null]));return {...b,r:mean(members.map(v=>v.r)),J:mean(members.map(v=>v.J)),matrixSum:members.every(v=>Number.isFinite(v.matrixSum))?mean(members.map(v=>v.matrixSum)):null,values,memberIds:members.map(v=>v.id),sourceCount:members.length,members:members.map(v=>v.native)};});
}
const stats=xs=>xs.length?{mean:mean(xs),min:Math.min(...xs),max:Math.max(...xs)}:null;
export function distanceMeans(rows,tolerance=.001){
 if(!Number.isFinite(tolerance)||tolerance<0)throw Error('距离容差须为非负有限数');const result=[];
 for(const pair of [...new Set(rows.map(v=>v.pair))].sort()){
  const sorted=rows.filter(r=>r.pair===pair).sort((a,b)=>a.r-b.r),bins=[];
  for(const b of sorted){if(!bins.length||b.r-bins.at(-1)[0].r>tolerance)bins.push([]);bins.at(-1).push(b);}
  for(const bs of bins){const series={total:stats(bs.map(b=>b.J))};for(const key of [...CHANNELS,'unknown'])series[key]=bs.every(b=>Number.isFinite(b.values?.[key]))?stats(bs.map(b=>b.values[key])):null;
   const delta=bs.every(b=>CHANNELS.every(k=>Number.isFinite(b.values?.[k])))?stats(bs.map(b=>CHANNELS.reduce((s,k)=>s+b.values[k],0)-b.J)):null;
   result.push({pair,r:mean(bs.map(b=>b.r)),minR:bs[0].r,maxR:bs.at(-1).r,connectionCount:bs.length,sourceCount:bs.reduce((s,b)=>s+b.sourceCount,0),memberIds:bs.flatMap(b=>b.memberIds),series,delta});
  }
 }
 return result;
}
export function groupNames(pair,atoms,sites=[]){
 const elements=pair.split('–'),names=elements.map(symbol=>{const ids=atoms.filter(a=>a.symbol===symbol).map(a=>a.index),cn=[...new Set(ids.map(id=>sites.find(s=>s.atom===id)?.coordination))];return cn.length===1&&cn[0]===6?{t:'t₂g',e:'e_g'}:cn.length===1&&cn[0]===4?{t:'t₂',e:'e'}:{t:'三重组',e:'二重组'};});
 return {total:'总 Jᵢⱼ',...Object.fromEntries(CHANNELS.map(k=>[k,names[0][k[0]]+' → '+names[1][k[1]]])),unknown:'未确定贡献'};
}
export function groupCsv(groups,names,keys=Object.keys(names)){
 const q=x=>'"'+String(x??'').replaceAll('"','""')+'"',header=['元素对','平均距离_A','距离下界_A','距离上界_A','通道','平均J_meV','最小J_meV','最大J_meV','独立连接数','原始记录数','原始记录索引','四通道和减总J_meV'];
 const rows=groups.flatMap(g=>Object.entries(g.series).filter(([k,s])=>s&&keys.includes(k)).map(([k,s])=>[g.pair,g.r,g.minR,g.maxR,names[k]||k,s.mean,s.min,s.max,g.connectionCount,g.sourceCount,JSON.stringify(g.memberIds),g.delta?.mean]));return '\ufeff'+[header,...rows].map(r=>r.map(q).join(',')).join('\r\n');
}
export function compactIdentity(report,basis=[]){
 const selected=new Set(basis.map(b=>b.wannier_index));
 // Saved view state is limited to 64 KiB by the existing generic host.
 const models=report.models.map(m=>({channel:m.channel,num_wann:m.num_wann,binding:{hr_sha256:m.binding.hr_sha256,win_sha256:m.binding.win_sha256,udis_sha256:m.binding.udis_sha256},window_validation:m.window_validation?{status:m.window_validation.status}:undefined,local_axes:m.local_axes?.map(s=>({atom:s.atom,coordination:s.coordination,polyhedron:s.polyhedron})),orbitals:m.orbitals.map(r=>selected.size&&!selected.has(r.wf)?{wf:r.wf}:({wf:r.wf,atom:r.atom,label:r.label,status:r.status,coverage:r.coverage,home_fraction:r.home_fraction,owner_fraction:r.owner_fraction,dominant_fraction:r.dominant_fraction,components:r.components.map(c=>({l:c.l,label:c.label,relative_weight:c.relative_weight}))}))}));
 return {schema:report.schema,representation:'view-attachment-v1',models,spin_alignment:report.spin_alignment};
}
