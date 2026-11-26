import {catalog,inspectFlow,uid} from './model';
import type {Kind,Project,Step} from './model';
import {clearExecutionBindings,nextStageSequence,staticDefaults,STAGE_SEQUENCES,VASP_KINDS} from './stagePolicy';

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

export function relatedVaspDraft(project:Project,source:Step,name:string,scfAttemptId?:string,baselineAttemptId?:string){
  if(!VASP_KINDS.includes(source.data.kind as VaspKind)||!name.trim()||name.trim().length>100)throw new Error('请填写 1 至 100 字的计算节点名称');
  if(scfAttemptId&&source.data.kind!=='scf')throw new Error('DOS 需要以上游 SCF 为来源');
  const parents=scfAttemptId?[source.id]:project.edges.filter(e=>e.target===source.id).map(e=>e.source);
  if(project.nodes.length>=500||project.edges.length+parents.length>2000)throw new Error('项目节点或连线数量已达上限');
  const node=copyDraftNode(project,source);
  node.data.label=name.trim();
  if(scfAttemptId){
    node.data.kind='dos';
    node.data.stageSlug='dos';
    node.data.stageSequence=nextStageSequence(project,'dos');
    const layer=node.data.parameters;
    node.data.parameters={...layer,values:{...staticDefaults('dos'),...layer?.values,NSW:'0',IBRION:'-1',ISTART:'0',ICHARG:'11'}};
    node.data.sourceDraft={mode:'upstream',purpose:'charge',files:'charge',read_charge:true,attempt_id:scfAttemptId};
    node.position={x:source.position.x+270,y:source.position.y+185};
  }else{
    if(baselineAttemptId)node.data.comparisonBaseline={nodeId:source.id,attemptId:baselineAttemptId};
    const draft=source.data.sourceDraft as {mode?:string}|undefined;
    if(draft?.mode==='upstream'||draft?.mode==='new'){
      const binding={...draft} as Record<string,unknown>;
      delete binding.acknowledge_unverified;delete binding.acknowledge_differences;
      node.data.sourceDraft=binding;
    }
  }
  while(project.nodes.some(other=>Math.abs(other.position.x-node.position.x)<220&&Math.abs(other.position.y-node.position.y)<125))node.position.y+=185;
  return {node,edges:parents.map(parent=>({id:uid(),source:parent,target:node.id}))};
}
