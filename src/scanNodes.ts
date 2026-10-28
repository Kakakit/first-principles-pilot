import type {Project,Step,Kind} from './model';
import {clearExecutionBindings,staticDefaults} from './stagePolicy';
export const calculationKinds=['relax','scf','bands','dos','vasp-wannier','wannier90'] as const;
export type CalculationKind=typeof calculationKinds[number];
export type ScanSpec={calculationKind:CalculationKind};
export function calculationKind(node:Step,project?:Project):Kind;
export function calculationKind(node?:Step,project?:Project):Kind|undefined;
export function calculationKind(node?:Step,project?:Project):Kind|undefined {
 if(!node)return undefined;
 if(node.data.kind!=='scan')return node.data.kind;
 const spec=node.data.scanSpec as ScanSpec|undefined;
 if(spec)return spec.calculationKind;
 const parent=project?.nodes.find(n=>project.edges.some(e=>e.source===n.id&&e.target===node.id));
 return parent&&parent.data.kind!=='scan'?parent.data.kind:'scan';
}
export function independentScan(node?:Step){return node?.data.kind==='scan'&&Boolean(node.data.scanSpec);}
export function copyCalculationDraft(data:Step['data']):Step['data'] {
 const clean=clearExecutionBindings(data);
 if(data.wannierDrafts){clean.wannierDrafts=structuredClone(data.wannierDrafts);clean.activeWannierDraft=data.activeWannierDraft;}
 return clean;
}
export function calculationDefaults(node:Step,kind:CalculationKind):Step{
 const values={...node.data.parameters?.values};
 if(kind==='relax'){if(!(Number(values.NSW)>0))values.NSW=null;if(!['1','2','3'].includes(values.IBRION||''))values.IBRION='2';}
 else Object.assign(values,staticDefaults(kind));
 const data=clearExecutionBindings(node.data);
 return {...node,data:{...data,scanSpec:{calculationKind:kind},parameters:{...data.parameters,values}}};
}
export function upgradeScan(project:Project,node:Step):Project {
 const parents=project.edges.filter(e=>e.target===node.id);
 const base=project.nodes.find(n=>n.id===parents[0]?.source);
 if(parents.length!==1||!base||!calculationKinds.includes(base.data.kind as CalculationKind))throw new Error('旧迭代须有唯一基础计算节点');
 const inherited=project.edges.filter(e=>e.target===base.id);
 if(inherited.length!==1)throw new Error('基础节点须有唯一实际上游，升级前请核对连线');
 const copied=copyCalculationDraft(base.data);
 const upgraded={...node,data:{...copied,kind:'scan' as const,executionMode:'scan' as const,label:node.data.label,note:node.data.note,stageSequence:node.data.stageSequence,stageSlug:node.data.stageSlug,scanSpec:{calculationKind:base.data.kind as CalculationKind},scanDraft:node.data.scanDraft,windowScanDraft:node.data.windowScanDraft}};
 return {...project,nodes:project.nodes.map(n=>n.id===node.id?upgraded:n),edges:[...project.edges.filter(e=>e.target!==node.id),{...parents[0],source:inherited[0].source}]};
}
/** Commas between values split cases; quoted INCAR text keeps its own commas. */
export function splitScanValues(text:string):string[]{
 const values:string[]=[];let start=0,quoted=false;
 for(let i=0;i<text.length;i++){
  if(quoted&&text[i]==='\\'&&text[i+1]==='"'){i++;continue;}
  if(text[i]==='"')quoted=!quoted;
  if(text[i]===','&&!quoted){values.push(text.slice(start,i));start=i+1;}
 }
 values.push(text.slice(start));return values;
}

