import {useEffect,useState} from 'react';
import {errorMessage,readJson} from './apiClient';
import type {Analysis} from './extensionModel';

export type ArtifactRule={field:string;producer:string;path:string;title:string;match:'project'|'same_sources';source_count?:number;auto_when?:string};
export type ProjectArtifact={id:string;created_at:string;size_bytes:number;sources:Array<{attempt_id?:string;name?:string}>};

export function matchingArtifacts(rule:ArtifactRule, candidates:ProjectArtifact[], attempts:string[]){
 if(rule.match==='project')return candidates;
 if(rule.source_count!==undefined&&rule.source_count!==attempts.length)return [];
 const ids=[...attempts].sort();
 return candidates.filter(item=>{
  const sourceIds=item.sources.map(source=>source.attempt_id).sort();
  return sourceIds.length===ids.length&&ids.every((id,i)=>id===sourceIds[i]);
 });
}

export async function loadProjectArtifacts(lookup:string, signal:AbortSignal):Promise<ProjectArtifact[]>{
 const base='/api/extension-host';
 const response=await fetch(base+'/project-artifacts?'+lookup,{signal});
 if(response.status!==404)return (await readJson(response)).artifacts;
 // A running older host already supports project history and artifact downloads.
 const query=new URLSearchParams(lookup);
 const projectId=query.get('project_id')||'';
 const producer=query.get('producer');
 const name=query.get('name');
 const data=await readJson(await fetch(base+'/analyses?'+new URLSearchParams({project_id:projectId}),{signal}));
 return (data.analyses as Analysis[]).flatMap(record=>{
  if(record.state!=='COMPLETED'||record.extension_id!==producer)return [];
  const output=record.outputs?.find(item=>item.path===name&&item.available);
  if(!output)return [];
  return [{id:record.id,created_at:record.created_at,size_bytes:output.size_bytes,
   sources:record.sources.map(source=>({attempt_id:source.attempt_id,name:source.name}))}];
 });
}

function dataUrl(blob:Blob,name:string):Promise<string>{
 return new Promise((resolve,reject)=>{
  const reader=new FileReader();
  reader.onload=()=>resolve(String(reader.result).replace(';base64,',';name='+encodeURIComponent(name)+';base64,'));
  reader.onerror=()=>reject(reader.error);
  reader.readAsDataURL(new File([blob],name,{type:'application/json'}));
 });
}

export function useProjectArtifact(rule:ArtifactRule|undefined, projectId:string|undefined, attempts:string[], manual:boolean, allowAuto:boolean, contextVersion:string){
 const project=rule&&projectId&&attempts.length?new URLSearchParams({project_id:projectId,producer:rule.producer,name:rule.path}).toString():'';
 const lookup=project&&(!rule?.source_count||attempts.length===rule.source_count)?project:'';
 const [response,setResponse]=useState<{key:string;items:ProjectArtifact[];error?:string}>();
 const [choice,setChoice]=useState<{scope:string;id:string}>();
 const [download,setDownload]=useState<{key:string;value?:string;error?:string}>();
 const [reload,setReload]=useState(0);
 const lookupKey=lookup?JSON.stringify([lookup,contextVersion,[...attempts].sort(),reload]):'';
 useEffect(()=>{
  if(!lookupKey)return;
  const controller=new AbortController();
  const timer=window.setTimeout(()=>{
   controller.abort();
   setResponse({key:lookupKey,items:[],error:'读取项目报告超时，请刷新项目报告或手动导入。'});
  },15000);
  void loadProjectArtifacts(lookup,controller.signal)
   .then(items=>{if(!controller.signal.aborted)setResponse({key:lookupKey,items});})
   .catch(error=>{if(!controller.signal.aborted)setResponse({key:lookupKey,items:[],error:'无法读取项目报告：'+errorMessage(error)});})
   .finally(()=>window.clearTimeout(timer));
  return ()=>{controller.abort();window.clearTimeout(timer);};
 },[lookupKey,lookup]);
 const scope=JSON.stringify([project,rule?.field,[...attempts].sort()]);
 const list=lookupKey&&response?.key===lookupKey?response.items:[];
 const candidates=rule?matchingArtifacts(rule,list,attempts):[];
 const auto=allowAuto&&rule?.match==='same_sources'&&candidates.length===1?candidates[0].id:'';
 const selected=choice?.scope===scope?choice.id:auto;
 const candidate=candidates.find(item=>item.id===selected);
 const key=!manual&&candidate&&rule?JSON.stringify([scope,candidate.id,rule.path]):'';
 useEffect(()=>{
  if(!key||!candidate||!rule)return;
  const controller=new AbortController();
  const timer=window.setTimeout(()=>{
   controller.abort();
   setDownload({key,error:'读取项目报告超时，请重新选择或手动导入。'});
  },15000);
  const path='/api/extension-host/analyses/'+encodeURIComponent(candidate.id)+'/files/'+encodeURIComponent(rule.path);
  void fetch(path,{signal:controller.signal}).then(async result=>{
   if(!result.ok)throw new Error('无法读取所选项目产物');
   const blob=await result.blob();
   if(blob.size!==candidate.size_bytes)throw new Error('项目产物大小已变化，请重新选择');
   return dataUrl(blob,rule.path);
  }).then(value=>{if(!controller.signal.aborted)setDownload({key,value});})
   .catch(error=>{if(!controller.signal.aborted)setDownload({key,error:errorMessage(error)});})
   .finally(()=>window.clearTimeout(timer));
  return ()=>{controller.abort();window.clearTimeout(timer);};
 },[key]);
 const loading=Boolean(lookupKey&&response?.key!==lookupKey);
 const waitsForMatch=allowAuto&&rule?.match==='same_sources'||choice?.scope===scope&&Boolean(choice.id);
 const pending=Boolean(!manual&&((waitsForMatch&&loading)||(key&&download?.key!==key)));
 const error=(key&&download?.key===key?download.error:undefined)||(lookupKey&&response?.key===lookupKey?response.error:undefined);
 return {visible:Boolean(project),candidates,selected:candidate?.id||'',auto:!!candidate&&candidate.id===auto&&choice?.scope!==scope,
  pending,loading,error,value:key&&download?.key===key?download.value:undefined,
  select:(id:string)=>setChoice({scope,id}),refresh:()=>setReload(value=>value+1)};
}
