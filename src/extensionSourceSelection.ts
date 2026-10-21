import {attemptLabel} from './humanLabels';
export type SourceCandidate={id:string;node_id?:string;name:string;input_reason?:string;input_note?:string|null;chain_run_id?:string;config?:{name?:string};parameter_summary?:string;stage:string;alias:string;state:string;job_id?:string;created_at?:string;seed?:string};
export function sourceCaption(source:SourceCandidate,full=false){
 if('input_note' in source&&!source.parameter_summary)return [source.name,attemptLabel(source,undefined,full)].join(' · ');
 const channel=source.seed?.endsWith('.1')?'通道 1':source.seed?.endsWith('.2')?'通道 2':'';
 const date=source.created_at?new Date(source.created_at):null;
 return [source.name,channel,source.job_id||(date&&Number.isFinite(date.getTime())?date.toLocaleString():'未提交'),source.input_reason].filter(Boolean).join(' · ');
}
export function selectedSourceOrder(current:string[],selected:string[],catalog:SourceCandidate[],primary:string,maximum:number){
 const chosen=new Set(selected);chosen.delete(primary);
 const known=new Set(catalog.map(source=>source.id));
 if([...chosen].some(id=>!known.has(id)))throw new Error('任务列表已变化，请重新选择来源');
 const ordered=[...new Set([...current,...catalog.map(source=>source.id)])].filter(id=>chosen.has(id));
 if(ordered.length+1>maximum)throw new Error(`包含当前任务最多可选 ${maximum} 个来源；请减少选择`);
 return ordered;
}
export function retainedSourceFiles(primary:string,current:string[],next:string[],files:Record<string,Record<string,string>>){
 const before=[primary,...current.filter(Boolean)];
 return Object.fromEntries([primary,...next].flatMap((id,index)=>{
  const choices=files[String(before.indexOf(id))];
  return choices?[[String(index),{...choices}]]:[];
 }));
}
