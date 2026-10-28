export function occupationSelection(atom,spinId='up',frame='local',kind='diagonal'){
 const spin=atom.spins.find(s=>s.id===spinId);if(!spin)throw Error('当前自旋通道不存在');
 const actualFrame=frame==='local'&&spin.local?'local':'global',m=spin[actualFrame];
 const values=kind==='natural'?m.natural_occupations:m.diagonal;
 return {spin:spin.label,frame:actualFrame,kind,unit:'electron',labels:kind==='natural'?values.map((_,i)=>'自然轨道 '+(i+1)):atom.orbital_labels,values,trace:m.trace,matrix:m.matrix,weights:kind==='natural'?m.weights:null,groups:actualFrame==='local'?spin.groups:null,spin_difference:atom.spin_moment_d};
}
export function occupationCSV(source,atom,selected){
 const rows=[['source','atom','element','polyhedron','spin','frame','kind','label','occupation','unit']];
 selected.labels.forEach((label,i)=>rows.push([source,atom.atom,atom.element,atom.polyhedron||'',selected.spin,selected.frame,selected.kind,label,selected.values[i],'electron']));
 rows.push([source,atom.atom,atom.element,atom.polyhedron||'',selected.spin,selected.frame,'trace','d壳总占据',selected.trace,'electron']);
 if(selected.groups)for(const [label,value] of [[selected.groups.e_label,selected.groups.e_occupation],[selected.groups.t_label,selected.groups.t_occupation]])rows.push([source,atom.atom,atom.element,atom.polyhedron||'',selected.spin,selected.frame,'approximate_group',label,value,'electron']);
 return '\uFEFF'+rows.map(row=>row.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n');
}
