import type {Analysis} from './extensionModel';
export type AnalysisKind='v2'|'v1'|'local'|'plot'|'comparison';
export type AnalysisRecord={id?:string;dataset_id?:string|null;dataset_recycled?:boolean;source_kind:AnalysisKind;source_id:string;project_id?:string|null;project_name:string;source_names:string[];analysis_name:string;analysis_version:string;created_at:string;registered_at?:string;title:string;note?:string;summary:string;scientific_validation:string;warnings:string[];source_deleted?:boolean};
export type AnalysisFile={path:string;media_type:string;available:boolean;reason?:string;sha256?:string;size_bytes?:number};
export type AnalysisDetail=AnalysisRecord&{analysis:Analysis|Record<string,any>;files:AnalysisFile[];comparison_context?:Record<string,unknown>};
export const analysisKey=(row:AnalysisRecord)=>`${row.source_kind}:${row.source_id}`;
const historyRecycleKinds:Record<AnalysisKind,string>={v2:'analyses',v1:'local-analyses',local:'local-analyses',plot:'plots',comparison:'comparisons'};
export const analysisRecycleKind=(kind:AnalysisKind)=>historyRecycleKinds[kind];
export const openDataset=(id:string)=>window.dispatchEvent(new CustomEvent('fpilot:dataset',{detail:{id}}));
export function filterAnalyses(rows:AnalysisRecord[],projectId:string,type:string,query:string){
 const needle=query.trim().toLocaleLowerCase();
 return rows.filter(row=>(!projectId||(projectId==='__unlinked'?!row.project_id:row.project_id===projectId))&&(!type||row.analysis_name===type)&&
  [row.title,row.project_name,...row.source_names,row.analysis_name,row.summary,row.note||''].join(' ').toLocaleLowerCase().includes(needle));
}
export const analysisFileUrl=(base:string,name:string)=>`${base}/files/${name.split('/').map(encodeURIComponent).join('/')}`;
