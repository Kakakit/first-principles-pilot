import {useEffect,useState} from 'react';
import type {Project} from './model';
import {requestJson,errorMessage} from './apiClient';
type Difference={field:string;frozen:unknown;draft:unknown};
export default function RunInputDifference({project,nodeId,bundleId}:{project:Project;nodeId:string;bundleId:string}){
 const [comparison,setComparison]=useState<{project:Project;id:string;rows:Difference[];errors:string[]}|null>(null);
 useEffect(()=>{
  const controller=new AbortController();
  const timer=setTimeout(()=>{void requestJson('/api/inputs/'+encodeURIComponent(bundleId)+'/draft-differences',{project,node_id:nodeId},'POST',{signal:controller.signal})
   .then(r=>{if(!controller.signal.aborted)setComparison({project,id:bundleId,rows:r.differences,errors:r.errors||[]});})
   .catch(e=>{if(!controller.signal.aborted)setComparison({project,id:bundleId,rows:[],errors:[errorMessage(e)]});});},200);
  return()=>{controller.abort();clearTimeout(timer);};
 },[project,nodeId,bundleId]);
 if(comparison?.project!==project||comparison.id!==bundleId)return <p role="status">正在比较当前草稿…</p>;
 const {rows,errors}=comparison;
 if(!rows.length&&!errors.length)return <p>当前草稿与所选输入版本一致。</p>;
 return <details><summary>当前草稿与所选版本{rows.length?'有 '+rows.length+' 处差异':''}{errors.length?' · 对比受限':''}</summary>
 {rows.map(row=><p key={row.field}>{row.field}：{String(row.frozen??'未设置')} → {String(row.draft??'未设置')}</p>)}
 {errors.map((error,i)=><p key={i}>{error}</p>)}
 </details>;
}
