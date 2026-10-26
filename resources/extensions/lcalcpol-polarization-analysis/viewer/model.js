// Keep the persisted `integers` key readable for older saved views; values may now be decimal.
export function nearestSelection(data, step = .5) {
 if (![.5, 1].includes(step)) throw Error('搜索步长只能为 0.5 或 1');
 return data.coefficients.map(c => {
  const v=c/step, lower=Math.floor(v), fraction=v-lower;
  const rounded=fraction===.5 ? (lower%2===0 ? lower : lower+1) : Math.round(v);
  return rounded*step || 0;
 });
}
export function calculate(data, integers) {
 if(!Array.isArray(integers)||integers.length!==3||integers.some(n=>typeof n!=='number'||!Number.isFinite(n)||Math.abs(n)>1e6))throw Error('扣除系数须为 −1000000 至 1000000 的三个有限数值，支持小数');
 const removed=[0,1,2].map(j=>integers.reduce((sum,n,i)=>sum+n*data.basis[i][j],0));
 const polarization=data.raw_p.map((p,j)=>p-removed[j]);
 const coefficients=data.coefficients.map((c,j)=>c-integers[j]);
 const dipole=polarization.map(p=>p*data.volume/data.factor);
 const integerOnly=integers.every(Number.isInteger);
 return {integers:[...integers],removed,coefficients,polarization,dipole,magnitude:Math.hypot(...polarization),
  selection_kind:integerOnly?'integer_branch_candidate':'fractional_quantum_calculation',
  diagnostic_gap:data.diagnostic_coefficients?.map((c,j)=>c-coefficients[j])};
}
export function csvReport(data,result){
 const rows=[['group','quantity','unit','x','y','z']];
 for(const [i,end] of data.endpoints.entries())for(const key of ['p_ion_e_ang','p_elc_e_ang','total_dipole_e_ang'])rows.push([String.fromCharCode(65+i),key,'e Å',...end[key]]);
 for(const [key,unit,v] of [['raw_p','C/m²',data.raw_p],['coefficients','1',data.coefficients],['automatic_integer','1',data.automatic],['chosen','1',result.integers],['removed','C/m²',result.removed],['remaining_coefficients','1',result.coefficients],['calculated_p','C/m²',result.polarization],['calculated_p','μC/cm²',result.polarization.map(x=>x*100)],['calculated_dipole_A_cell','e Å',result.dipole]])rows.push(['result',key,unit,...v]);
 rows.push(['result','selection_kind','',result.selection_kind,'',''],['result','physical_branch','','INSUFFICIENT','','']);
 rows.push(['reference','volume','Å³',data.volume,'',''],['reference','conversion_factor','C/m² per eÅ/Å³',data.factor,'','']);
 data.basis.forEach((v,i)=>rows.push(['quantum','Q'+(i+1),'C/m²',...v]));
 for(const [key,v] of Object.entries(data.raw_dipole_difference||{}))rows.push(['B−A',key,'e Å',...v]);
 return '\ufeff'+rows.map(row=>row.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\n');
}
