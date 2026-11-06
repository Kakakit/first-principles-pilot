// Package-owned portable report bridge. No J values are transformed.
export function identityMapping(report,bindings,basis){
 const hint = '请在「轨道占据、能级与劈裂分析」中打开同一对上、下自旋的 Wannier 能级结果，点击「下载给 TB2J 用的文件」，选入 wf-identity.json。';
 if (['fpilot.local-orbital-reference.v1', 'fpilot.local-orbital-references.v1'].includes(report?.schema)) {
   throw Error('你选的是 VASP 局域坐标参考，请把它放在 Wannier 能级分析的「VASP 局域坐标参考」。' + hint);
 }
 if (report?.schema !== 'fpilot.wf-identity.v1' || !Array.isArray(report.models)) {
   throw Error('这里需要 TB2J 的轨道成分文件。' + hint);
 }
 if (!bindings?.length) throw Error('这次 TB2J 结果缺少模型对应记录，无法核对轨道成分文件。');
 const models=bindings.map(binding=>{const matches=report.models.filter(m=>m.channel===binding.channel&&m.binding?.hr_sha256===binding.hr_sha256&&m.binding?.win_sha256===binding.win_sha256);if(matches.length!==1)throw Error('身份报告与本次冻结模型/自旋通道不符或存在重复');return matches[0];});
 if(models.length!==2||new Set(models.map(m=>m.channel)).size!==2)throw Error('身份衔接首版需共线上下自旋两个来源；请导入包含两个模型的报告');
 if(!Array.isArray(basis)||!basis.length)throw Error('本次结果没有可核对的orb到WF编号对应，不强行套用标签');
 const rows=[];
 for(const m of models){if(m.binding?.udis_sha256&&m.window_validation?.status!=='MATCHED_HR_DIAGONAL')throw Error('旧解缠结身份报告未核对外窗口能带行，请加入同来源eig重新分析');if(m.orbitals?.length!==m.num_wann||!m.orbitals.every((r,i)=>r.wf===i+1))throw Error('WF编号与报告维度不符');for(const b of basis){const r=m.orbitals[b.wannier_index-1];if(!r)throw Error('TB2J的WF编号超出报告');for(const key of ['coverage','home_fraction','owner_fraction','dominant_fraction'])if(!Number.isFinite(r[key])||r[key]<-1e-8||r[key]>1+1e-8)throw Error('身份报告权重无效');if(!Array.isArray(r.components)||r.components.some(c=>typeof c.label!=='string'||!Number.isFinite(c.relative_weight)||c.relative_weight<0||c.relative_weight>1+1e-8))throw Error('身份报告成分无效');const same=r.atom===b.atom_index;rows.push({...b,channel:m.channel,label:same?(['CROSS_CELL_MIXED','LOW_HOME_OVERLAP'].includes(r.status)?'胞内参考占比较低 WF':String(r.label)):'WF'+r.wf+' · 原子归属待核对',group:'',source:'最终WF参考重叠',status:same?r.status:'ATOM_ASSIGNMENT_CONFLICT',coverage:r.coverage,home_fraction:r.home_fraction,owner_fraction:r.owner_fraction,dominant_fraction:r.dominant_fraction,components:r.components});}}
 const automatic=basis.map(b=>{const selected=rows.filter(r=>r.atom_index===b.atom_index&&r.orbital_index===b.orbital_index);return {...selected[0],label:selected.every(r=>r.label===selected[0].label)?selected[0].label:'WF'+b.wannier_index+' · 自旋成分不同',components_by_channel:Object.fromEntries(selected.map(r=>[r.channel,r.components]))};});
 return {rows,automatic,models,spin_alignment:report.spin_alignment};
}
