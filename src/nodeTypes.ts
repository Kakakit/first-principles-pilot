import {catalog,inspectFlow,uid} from './model';
import type {Kind,Project,Step} from './model';
import {clearExecutionBindings,staticDefaults,STAGE_SEQUENCES,VASP_KINDS} from './stagePolicy';

export type VaspKind=typeof VASP_KINDS[number];
export function copyDraftNode(project:Project,node:Step):Step {
  const data=clearExecutionBindings(node.data);
  if(Object.keys(staticDefaults(data.kind)).length)data.parameters={...data.parameters,values:{...staticDefaults(data.kind),...data.parameters?.values}};
  data.label=`${node.data.label} 副本`.slice(0,100);
  if(project.stagePolicy==='kind-fixed-v1')data.stageSequence=STAGE_SEQUENCES[data.kind];
  return {...structuredClone(node),id:uid(),selected:false,position:{x:node.position.x+55,y:node.position.y+185},data};
}
export function typeSwitchPreview(project:Project,node:Step,kind:Kind){
  if(!VASP_KINDS.includes(node.data.kind as VaspKind)||!VASP_KINDS.includes(kind as VaspKind))throw new Error('仅支持 VASP 计算类型之间切换');
  const data=clearExecutionBindings(node.data),values={...data.parameters?.values};
  const before={...values};
  if(kind!=='relax'&&node.data.kind==='relax'){values.NSW='0';values.IBRION='-1';}
  if(kind==='relax'&&node.data.kind!=='relax'){
    const nsw=values.NSW??project.parameters?.shared.values.NSW;
    values.NSW=nsw!==null&&nsw!==undefined&&Number(nsw)>0?nsw:null;
    const ibrion=values.IBRION??project.parameters?.shared.values.IBRION;
    values.IBRION=['1','2','3'].includes(ibrion||'')?ibrion!:'2';
  }
  if(kind==='vasp-wannier')values.NCORE='1';
  if(kind!=='relax'){
    if(!Object.hasOwn(values,'NSW'))values.NSW='0';
    if(!Object.hasOwn(values,'IBRION'))values.IBRION='-1';
  }
  data.parameters={...data.parameters,values};
  data.kind=kind;
  if(project.stagePolicy==='kind-fixed-v1')data.stageSequence=STAGE_SEQUENCES[kind];
  if(data.stageSlug===node.data.kind)data.stageSlug=kind;
  const next={...node,data};
  const changes=Object.keys(values).filter(tag=>before[tag]!==values[tag]).map(tag=>({tag,before:before[tag]??project.parameters?.shared.values[tag]??null,after:values[tag]}));
  return {node:next,changes,issues:inspectFlow({...project,nodes:project.nodes.map(n=>n.id===node.id?next:n)}).filter(i=>i.level==='error'),label:catalog[kind].label};
}
