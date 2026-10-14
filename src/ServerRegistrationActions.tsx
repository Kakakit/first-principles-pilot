import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import type {RegisteredProfile,ServerConfig} from './executionUi';

type Status={config:ServerConfig;registered_profiles:RegisteredProfile[];removed_hosts:Record<string,{removed_at:string}>};
type Preview={alias:string;revision:string;blocked:boolean;note:string;attempts:Array<{id:string;state:string;job_id?:string}>};
export default function ServerRegistrationActions({alias,disabled,onChanged}:{alias?:string;disabled:boolean;onChanged:(value:Status)=>void}){
 const [removed,setRemoved]=useState<Status['removed_hosts']>({}),[preview,setPreview]=useState<Preview|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const currentAlias=useRef(alias);currentAlias.current=alias;
 useEffect(()=>{setPreview(null);setError('');void requestJson('/api/sites').then(v=>setRemoved(v.removed_hosts||{})).catch(e=>setError(errorMessage(e)));},[alias]);
 async function act(action:'preview'|'remove'|'restore',target=alias){
  if(!target)return;setBusy(true);setError('');
  try{const value=await requestJson(`/api/sites/${encodeURIComponent(target)}/${action==='preview'?'remove-preview':action}`,action==='remove'?{revision:preview?.revision,confirm:true}:{});
   if(action==='preview'){if(target===currentAlias.current)setPreview(value);}else{setPreview(null);setRemoved(value.removed_hosts);onChanged(value);window.dispatchEvent(new Event('fpilot:settings-changed'));}
  }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 }
 return <div className="server-registration-actions">
  {alias&&<button className="button danger" disabled={disabled||busy} onClick={()=>void act('preview')}>移除此服务器登记</button>}
  {Object.keys(removed).length>0&&<details><summary>已移除的服务器 · 可恢复</summary>{Object.entries(removed).map(([name,value])=><div className="action-row" key={name}><span>{name} · {new Date(value.removed_at).toLocaleString()}</span><button className="button" disabled={disabled||busy} onClick={()=>void act('restore',name)}>恢复 {name}</button></div>)}</details>}
  {preview&&<div className="modal-scrim"><section className="modal" role="dialog" aria-modal="true" aria-label="移除服务器登记"><h3>移除 {preview.alias} 的本机登记？</h3><p>{preview.note}</p><p>已有 {preview.attempts.length} 条任务记录。已排队或运行的远端任务继续执行，历史仍可查看和刷新。</p>{preview.blocked&&<p role="alert">有任务正在派发，等待提交回执完成后才能移除。</p>}<div className="action-row"><button autoFocus className="button" disabled={busy} onClick={()=>setPreview(null)}>取消</button><button className="button danger" disabled={busy||preview.blocked} onClick={()=>void act('remove')}>确认移除登记</button></div></section></div>}
  {error&&<p role="alert" className="structure-error">{error}</p>}
 </div>;
}
