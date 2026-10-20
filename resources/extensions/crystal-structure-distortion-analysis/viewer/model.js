export const number=v=>!Number.isFinite(v)?'—':v!==0&&(Math.abs(v)<1e-4||Math.abs(v)>=1e5)?v.toExponential(5).replace(/e([+-]?)(\d+)/,(_,sign,digits)=>'×10'+(sign==='-'?'⁻':'')+digits.split('').map(c=>'⁰¹²³⁴⁵⁶⁷⁸⁹'[Number(c)]).join('')):Number(v.toPrecision(8)).toString();
export const kindName={cis:'顺式',trans:'对向',tetrahedral:'四面体'};
export const subscript=s=>s.replace(/[0-9]/g,c=>'₀₁₂₃₄₅₆₇₈₉'[Number(c)]);
export const imageLabel=v=>v?.some(x=>x!==0)?' · 镜像 ['+v.join(', ')+']':'';
export const siteLabel=s=>s.element+' 第 '+s.site_number+' 号原子 · '+subscript(s.polyhedron);
export const ligandLabel=(s,i)=>s.ligand+' 第 '+s.ligand_site_numbers[i]+' 号'+imageLabel(s.ligand_images[i]);
export function bridgeType(b){return [b.element_i,b.element_j].sort().join('–'+b.ligand+'–');}
export function sortSites(sites,type='',key='site_number',direction='asc'){
 return sites.filter(s=>!type||s.polyhedron===type).slice().sort((a,b)=>((a[key]-b[key])*(direction==='desc'?-1:1))||a.site_number-b.site_number);
}
export function bondRows(s){return s.bond_lengths_angstrom.map((length,index)=>({index,label:ligandLabel(s,index),length,deviation:(length/s.mean_bond_length_angstrom-1)*100}));}
export function angleRows(s,kind='',direction='asc'){
 return (s.angles||[]).map((a,index)=>({...a,index,label:a.ligand_local_indices.map(i=>ligandLabel(s,i)).join(' — '+s.element+' 第 '+s.site_number+' 号 — ')})).filter(a=>!kind||a.kind===kind).sort((a,b)=>(a.angle_degree-b.angle_degree)*(direction==='desc'?-1:1)||a.index-b.index);
}
export function selectBridges(rows,type='',center='',direction='asc'){
 return rows.filter(b=>(!type||bridgeType(b)===type)&&(!center||b.center_i===Number(center)||b.center_j===Number(center))).slice().sort((a,b)=>(a.angle_degree-b.angle_degree)*(direction==='desc'?-1:1)||a.id-b.id);
}
export function csv(columns,rows){return '\uFEFF'+[columns.map(c=>c[1]),...rows.map(row=>columns.map(c=>row[c[0]]??''))].map(row=>row.map(v=>'"'+(Array.isArray(v)?JSON.stringify(v):String(v)).replaceAll('"','""')+'"').join(',')).join('\r\n');}
