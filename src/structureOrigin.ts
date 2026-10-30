import type {Project} from './model';
import type {StructureDocument} from './structure';
export type StructureOrigin={version:1;project_id:string;node_id:string;attempt_id:string;job_id:string;alias:string;path:string;sha256:string;input_note:string;state:string;program_done:boolean|null;extracted_at:string;observed_at:string;cached:boolean};
export function validateStructureOrigin(value:unknown,structure?:StructureDocument):asserts value is StructureOrigin{
 const v=value as StructureOrigin,fields=['project_id','node_id','attempt_id','alias','path','sha256','state','extracted_at'] as const;
 if(!v||v.version!==1||fields.some(k=>typeof v[k]!=='string'||!v[k]||v[k].length>2000||/[\0\r\n]/.test(v[k]))||v.path.startsWith('/')||v.path.split('/').includes('..')||!['POSCAR','CONTCAR'].includes(v.path.split('/').at(-1)!)||!/^[a-f0-9]{64}$/.test(v.sha256)||v.sha256!==structure?.source.sha256||typeof v.cached!=='boolean'||v.program_done!==null&&typeof v.program_done!=='boolean'||['job_id','input_note','observed_at'].some(k=>typeof (v as any)[k]!=='string'||(v as any)[k].length>2000))throw new Error('结构来源记录与原文件不一致。');
}
/** Presentation edges are deliberately absent from Project.edges and all execution paths. */
export function structureOriginEdges(project:Project){
 return project.nodes.flatMap(node=>{
  const origin=node.data.structureOrigin as StructureOrigin|undefined;
  const source=typeof node.data.structureOriginNodeId==='string'?node.data.structureOriginNodeId:origin?.project_id===project.id?origin.node_id:undefined;
  if(!origin||!source||source===node.id||!project.nodes.some(n=>n.id===source))return [];
  return [{id:'structure-origin:'+node.id,source,target:node.id,targetHandle:'structure-origin',type:'smoothstep',label:origin.path.split('/').at(-1),selectable:false,deletable:false,focusable:false,animated:false,ariaLabel:'结构来源：'+origin.path}];
 });
}
