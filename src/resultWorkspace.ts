import {newProject,makeStep,uid} from './model';
import type {Project,Kind} from './model';
export type ResultAttempt={id:string;node_id?:string;execution_scope?:string;state:string;kind?:string;created_at?:string;job_id?:string|null;path_schema?:string;stage_slug?:string;remote_path_absolute?:string;remote_path?:string;bundle_id?:string;evidence?:Record<string,unknown>;config?:{alias?:string};[key:string]:unknown};
export type FrozenInput={id?:string;display_name?:string;stage?:string;parameters:Record<string,string>;parameter_origin?:string;species?:string[];files:Record<string,{sha256?:string}>;geometry_sha256?:string;kpoints?:string;warning?:string};
export type Scalar={value:number;unit:string;source:{file:string;sha256?:string;host?:string;method?:string;line?:number}}|null;
export type ResultSummary={attempt_id:string;state:string;evidence:Record<string,unknown>;input:FrozenInput;sources:Array<{type:string;display_name?:string;attempt_id?:string;files:string[];reason?:string;path?:string}>;metrics:Record<string,Scalar|number|boolean|null>|null;metrics_error?:string;metrics_checked_at?:string};
export const metricDefinitions=[['energy','总能','eV/cell'],['energy_per_atom','每原子能','eV/atom'],['magnetization','晶胞磁矩','μB/cell'],['elapsed','运行时间','s']] as const;
export const formatValue=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)?String(Number(value.toPrecision(8))):'—';
export function evidenceLabel(value:unknown){return value===true?'已见证据':value===false?'未见证据':'尚未检查';}
export function programDone(attempt:ResultAttempt){return (attempt.evidence||{})[attempt.kind==='wannier90'?'wannier_all_done':'vasp_timing']===true;}
export function nextAction(attempt?:ResultAttempt){
 if(!attempt)return {view:'config',label:'配置并冻结输入',reason:'当前节点尚无运行记录'};
 if(attempt.state==='UNKNOWN'||attempt.state==='DISPATCHING')return {view:'results',label:'核对原任务',reason:'先读取原 job ID 与结束证据，状态未确认前不重新提交'};
 if(attempt.state==='FAILED')return {view:'results',label:'查看失败与恢复',reason:'先查看日志和输入；重新计算需另行准备、审阅和授权'};
 if(attempt.state==='PREPARED')return {view:'run',label:'继续运行审阅',reason:'已准备，尚未提交；核对已有版本、来源与脚本'};
 if(attempt.state==='COMPLETED')return {view:'results',label:'查看结果与分析',reason:programDone(attempt)?'程序结束与数值、物理验证分别查看':'调度已结束，程序完成证据仍需核对'};
 return {view:'run',label:'查看运行状态',reason:'等待现有任务；不重新创建或提交'};
}
export function latestByNode(rows:ResultAttempt[]){
 const result:Record<string,ResultAttempt>={};
 for(const row of [...rows].sort((a,b)=>(b.created_at||'').localeCompare(a.created_at||''))){const owner=row.execution_scope||row.node_id;if(owner&&!result[owner])result[owner]=row;}
 return result;
}
export const taskTemplates=[
 {id:'scf',name:'首次电子自洽',description:'结构 → SCF；先完成结构、物理参数和输入审查',kinds:['structure','scf'],edges:[[0,1]]},
 {id:'relax-scf',name:'结构优化后自洽',description:'结构 → relax → SCF；下游绑定已完成的优化结果',kinds:['structure','relax','scf'],edges:[[0,1],[1,2]]},
 {id:'electronic',name:'能带与态密度',description:'结构 → SCF → bands / DOS；分支分别配置',kinds:['structure','scf','bands','dos'],edges:[[0,1],[1,2],[1,3]]}
] as const;
export function taskProject(id:string,name?:string):Project {
 const template=taskTemplates.find(t=>t.id===id);if(!template)throw new Error('未知任务模板');
 const project=newProject(name||template.name);
 project.nodes=template.kinds.map((kind,index)=>makeStep(kind as Kind,{x:60+index*270,y:kind==='dos'?340:140}));
 project.edges=template.edges.map(([a,b])=>({id:uid(),source:project.nodes[a].id,target:project.nodes[b].id}));
 return project;
}
const physicalTags=['GGA','METAGGA','LHFCALC','AEXX','HFSCREEN','LDAU','LDAUTYPE','LDAUL','LDAUU','LDAUJ','ISPIN','LSORBIT','LNONCOLLINEAR','SAXIS','MAGMOM'];
export function physicsSignature(input?:FrozenInput){
 if(!input||input.warning)return '';
 return JSON.stringify([input.species,physicalTags.map(tag=>[tag,input.parameters[tag]||null])]);
}
export function comparisonModel(input:FrozenInput|undefined,legacy:string|undefined,atoms:unknown){
 const physics=physicsSignature(input);
 return legacy&&physics&&typeof atoms==='number'&&Number.isInteger(atoms)&&atoms>0?JSON.stringify([legacy,physics,atoms]):undefined;
}
export function inputDifferences(inputs:FrozenInput[]):string[]{
 if(inputs.length<2)return [];
 const notes:string[]=[];
 if(inputs.some(i=>i.warning||!i.id))notes.push('部分冻结输入不可读，可比性尚未建立');
 const different=(get:(i:FrozenInput)=>unknown)=>new Set(inputs.map(i=>JSON.stringify(get(i)))).size>1;
 if(different(i=>i.stage))notes.push('计算阶段不同，不能据此解释参数收敛');
 if(different(i=>i.files.POTCAR?.sha256))notes.push('赝势不同，需单独核对能量可比性');
 if(different(i=>i.geometry_sha256))notes.push('结构不同，能量差同时包含几何变化');
 const tags=[...physicalTags,'ENCUT','EDIFF','ISMEAR','SIGMA'];
 const changed=tags.filter(tag=>different(i=>i.parameters[tag]||null));
 if(changed.length)notes.push('输入差异：'+changed.join('、'));
 if(different(i=>i.files.KPOINTS?.sha256||i.kpoints))notes.push('k 点设置不同');
 return notes;
}
