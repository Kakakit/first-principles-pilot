import {useEffect,useState} from 'react';
import type {Project} from './model';
import type {ParameterLayer} from './parameters';
import {requestJson,errorMessage} from './apiClient';

type Impact={node_id:string;label:string;tags:string[];files:string[];changed:boolean;overrides?:string[];issues:string[]};
export default function SharedParameterImpact({project,baseline}:{project:Project;baseline:ParameterLayer}){
 const [result,setResult]=useState<{project:Project;rows:Impact[];error?:string}|null>(null);
 const changed=JSON.stringify(baseline)!==JSON.stringify(project.parameters?.shared??{values:{}});
 useEffect(()=>{
  if(!changed)return;
  const controller=new AbortController();
  const timer=setTimeout(()=>{void requestJson('/api/inputs/shared-impact',{project,previous_shared:baseline},'POST',{signal:controller.signal})
   .then(value=>{if(!controller.signal.aborted)setResult({project,rows:value.nodes});})
   .catch(error=>{if(!controller.signal.aborted)setResult({project,rows:[],error:errorMessage(error)});});},650);
  return()=>{clearTimeout(timer);controller.abort();};
 },[changed,project,baseline]);
 if(!changed)return null;
 const rows=result?.project===project?result.rows:null;
 return <details className="workbench-disclosure"><summary>{rows&&!result?.error?'共享修改 · '+rows.filter(r=>r.changed).length+' 个节点输入变化'+(rows.some(r=>r.issues.length)?' · 部分待核对':''):'共享参数影响范围'}</summary>
  <p>与进入共享编辑时的设置比较。已冻结的输入继续使用原版本。</p>
  {result?.project===project&&result.error?<p role="alert">{result.error}</p>:rows?<ul>{rows.map(row=><li key={row.node_id}><strong>{row.label}</strong> · {row.changed?(row.tags.join('、')||row.files.join('、')||'来源设置')+' 有变化':row.issues.length?'待核对':'生成输入一致'}{Boolean(row.overrides?.length)&&<small> · 节点已有覆盖：{row.overrides?.join('、')}</small>}{row.issues.length>0&&<small> · 对比受限：{row.issues.join('；')}</small>}</li>)}</ul>:<p role="status">正在比较各节点草稿…</p>}
 </details>;
}
