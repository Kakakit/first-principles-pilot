import {useEffect,useRef,useState} from 'react';
import {errorMessage,requestJson} from './apiClient';
import type {Project} from './model';

export type CleanupTarget={kind:string;key:string;project_id?:string;project?:Project};
export type RemoteAsset={id:string;kind:string;key:string;label:string;project_id?:string;alias:string;path:string;state:string;owner_state?:string;blocked?:string;error?:string;requested_at?:string;retained_at?:string;recovery_required?:boolean;operation?:string};
export type CleanupPlan={records:{kind:string;key:string}[];assets:RemoteAsset[];remote_note?:string};
function cleanupError(cause:unknown){
 const message=errorMessage(cause);
 return /^not found$/i.test(message.trim())
  ?'当前工作台服务没有加载远端文件管理接口。请在工作台控制中心重启服务，再重试。'
  :message;
}
export async function previewCleanup(target:CleanupTarget):Promise<CleanupPlan>{
 try{return await requestJson('/api/cleanup/preview',target);}
 catch(cause){throw new Error(cleanupError(cause));}
}
export const lifecycleKinds=new Set(['attempts','analyses','local-analyses','plots']);
const labels:Record<string,string>={attempts:'计算任务',analyses:'扩展分析',scans:'迭代批次',chains:'计算链','local-analyses':'本地分析',plots:'绘图'};
export const assetState:Record<string,string>={retained:'保留',pending:'待清理',running:'核对或清理中',waiting:'等待连接',deleted:'已删除',already_missing:'原目录已不存在',blocked:'暂不可清理',unknown:'等待核对',failed:'清理失败'};

export default function LifecycleDialog({target,label,plan:initial,onClose,onDone}:{target:CleanupTarget;label:string;plan?:CleanupPlan;onClose:()=>void;onDone:(receipt:{assets:RemoteAsset[]})=>void|Promise<void>}){
 const dialog=useRef<HTMLDialogElement>(null);
 const [plan,setPlan]=useState(initial),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [mode,setMode]=useState<'keep'|'clean'>('keep'),[excluded,setExcluded]=useState<Set<string>>(()=>new Set());
 const offered=plan?.assets.filter(a=>!a.blocked&&!a.recovery_required&&['retained','failed','blocked'].includes(a.state))||[];
 const chosen=offered.filter(a=>!excluded.has(a.id)).map(a=>a.id);
 const reload=()=>{setError('');void previewCleanup(target).then(setPlan).catch(e=>setError(errorMessage(e)));};
 useEffect(()=>{dialog.current?.showModal();if(initial)return;let active=true;void previewCleanup(target).then(value=>{if(active)setPlan(value);}).catch(e=>{if(active)setError(errorMessage(e));});return()=>{active=false;};},[]);
 async function execute(){
  setBusy(true);setError('');
  try{const receipt=await requestJson('/api/cleanup',{...target,asset_ids:mode==='clean'?chosen:[]});await onDone(receipt);onClose();}
  catch(e){setError(cleanupError(e));}finally{setBusy(false);}
 }
 const counts=plan?.records.reduce<Record<string,number>>((out,{kind})=>({...out,[kind]:(out[kind]||0)+1}),{});
 return <dialog ref={dialog} className="history-confirm lifecycle-dialog" aria-label="删除记录与远端文件" onCancel={e=>{e.preventDefault();if(!busy)onClose();}}>
  <h2>删除 {label}？</h2>
  {!plan&&!error&&<p role="status">正在读取关联记录和已登记目录…</p>}
  {error&&<p className="structure-error" role="alert">{error} {!plan&&<button className="text-button" onClick={reload}>重试读取</button>}</p>}
  {plan&&<>
   <p>{Object.entries(counts||{}).filter(([kind])=>labels[kind]).map(([kind,count])=>count+' 条'+labels[kind]).join(' · ')||'所选记录将移入回收站。'}</p>
   <fieldset disabled={busy} className="lifecycle-options">
    <legend>远端文件</legend>
    <label><input type="radio" checked={mode==='keep'} onChange={()=>setMode('keep')}/> 移入回收站，保留远端文件</label>
    <label><input type="radio" checked={mode==='clean'} disabled={!offered.length} onChange={()=>setMode('clean')}/> 移入回收站，并清理选中的远端目录</label>
    {!plan.assets.length&&<p>{plan.remote_note||'这条记录没有登记可清理的远端目录。'}</p>}
    {mode==='clean'&&<p>远端删除无法恢复。本地记录仍可恢复。离线或未核实的目录会在回收站显示清理状态。</p>}
   </fieldset>
   {plan.assets.length>0&&<details open={mode==='clean'||!offered.length||undefined} className="lifecycle-assets"><summary>{plan.assets.length} 个已登记目录</summary>
    {plan.assets.map(asset=><article key={asset.id}>
     {mode==='clean'&&offered.includes(asset)?<label><input type="checkbox" checked={!excluded.has(asset.id)} onChange={e=>setExcluded(old=>{const next=new Set(old);if(e.target.checked)next.delete(asset.id);else next.add(asset.id);return next;})}/> {asset.alias} · {asset.label}</label>:<strong>{asset.alias} · {asset.label}</strong>}
     <code>{asset.path}</code>
     {asset.blocked?<small>{asset.blocked}</small>:asset.recovery_required&&<small>请到回收站的「远端文件」重新预览当前目录。</small>}
    </article>)}
   </details>}
  </>}
  <div className="action-row"><button autoFocus className="button" disabled={busy} onClick={onClose}>取消</button><button className="button danger" disabled={busy||!plan||mode==='clean'&&!chosen.length} onClick={()=>void execute()}>{busy?'正在移入回收站…':mode==='clean'?'回收记录并清理选中目录':'移入回收站'}</button></div>
 </dialog>;
}
