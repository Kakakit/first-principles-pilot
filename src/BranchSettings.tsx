import {useEffect,useState} from 'react';
import type {Project} from './model';
import {requestJson,errorMessage} from './apiClient';
type Binding={state:string;alias?:string;aliases:string[];evidence:{scope:string;alias:string;state:string;attempt_id:string}[]};
export default function BranchSettings({project,nodeId}:{project:Project;nodeId?:string}){
 const [binding,setBinding]=useState<Binding|null>(null),[error,setError]=useState(''),[choice,setChoice]=useState('');
 const load=async()=>{if(!nodeId)return;try{const value=await requestJson('/api/branch-binding',{project,node_id:nodeId});setBinding(value);setError('');}catch(e){setBinding(null);setError(errorMessage(e));}};
 useEffect(()=>{void load();},[project.id,nodeId,JSON.stringify(project.edges)]);
 return <section><p>{binding?binding.state==='UNBOUND'?'尚未绑定；成功提交后固定服务器':binding.state==='CONFLICT'?'旧记录有多个服务器，请明确后续分支归属':'服务器：'+binding.alias+(binding.state==='RESERVED'?' · 回执待核查':' · 已绑定'):error}</p>{binding?.evidence.length? <details><summary>绑定依据与历史归属</summary>{binding.evidence.map((e,i)=><p key={i}>{project.nodes.find(n=>n.id===e.scope)?.data.label||'历史节点'} · {e.alias} · {e.state} · 已绑定任务</p>)}</details>:null}
 {(binding?.state==='CONFLICT'||error.includes('冲突'))&&<><label>后续使用服务器<select value={choice} onChange={e=>setChoice(e.target.value)}><option value="">请选择</option>{(binding?.aliases||Array.from(new Set(error.match(/jutai-\d|gengzi-187/g)||[]))).map(alias=><option key={alias}>{alias}</option>)}</select></label><p>其他服务器的旧终态记录会单独归档，原任务和远端文件保留。运行中、UNKNOWN 与新永久绑定不能解除。</p><button className="button" disabled={!choice||!nodeId} onClick={async()=>{try{setBinding(await requestJson('/api/branch-binding/resolve-legacy',{project,node_id:nodeId,alias:choice}));setError('');}catch(e){setError(errorMessage(e));}}}>整理旧分支归属</button></>}
 {error&&binding&&<p role="alert">{error}</p>}</section>;
}
