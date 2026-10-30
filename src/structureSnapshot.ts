import {makeStep} from './model';
import type {Project} from './model';
import type {StructureDocument} from './structure';
import {validateStructure,verifySource} from './structure';
import {validateStructureOrigin} from './structureOrigin';
import type {StructureOrigin} from './structureOrigin';
export type StructureSnapshot={structure:StructureDocument;origin:StructureOrigin;warnings:string[]};
export async function checkedStructureSnapshot(value:StructureSnapshot){validateStructure(value.structure);validateStructureOrigin(value.origin,value.structure);await verifySource(value.structure);return value;}
export function addStructureSnapshot(project:Project,value:StructureSnapshot){
 validateStructure(value.structure);validateStructureOrigin(value.origin,value.structure);
 if(value.origin.project_id!==project.id)throw new Error('结构来源不属于当前项目。');
 const existing=project.nodes.find(n=>{const o=n.data.structureOrigin as StructureOrigin|undefined;return o&&o.project_id===value.origin.project_id&&o.attempt_id===value.origin.attempt_id&&o.path===value.origin.path&&o.sha256===value.origin.sha256&&!n.data.structure?.edit;});
 if(existing)return {project,nodeId:existing.id,reused:true};
 if(project.nodes.length>=500)throw new Error('项目节点已达上限，请先整理画布。');
 const source=project.nodes.find(n=>n.id===value.origin.node_id);
 const x=(source?.position.x||0)+300;let y=source?.position.y||80;
 for(let i=0;i<=project.nodes.length;i++,y+=240)if(!project.nodes.some(n=>Math.abs(n.position.x-x)<280&&Math.abs(n.position.y-y)<220))break;
 const node=makeStep('structure',{x,y}),file=value.origin.path.split('/').at(-1),baseLabel=file+' 结构';let label=baseLabel;for(let count=2;project.nodes.some(n=>n.data.label===label);count++)label=baseLabel+' '+count;
 node.data={...node.data,label,params:{material:value.structure.formula},structure:structuredClone(value.structure),structureOrigin:structuredClone(value.origin),structureOriginNodeId:value.origin.node_id,note:value.warnings.join('\n').slice(0,2000)};
 return {project:{...project,nodes:[...project.nodes,node]},nodeId:node.id,reused:false};
}
