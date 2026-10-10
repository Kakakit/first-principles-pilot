import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {statusLabel} from './statusLabels';

type Receipt={operation_id:string;token:string;path:string;alias:string;remote_path:string;attempt_id:string;state:string;job_id?:string;scheduler_state?:string;size_bytes:number;link_count:number;expires_at:string;result:string;note?:string;cache_error?:string;references:{kind:string;id:string;state?:string;relation:string;certainty:string}[]};
const labels:Record<string,string>={DELETED:'已删除',UNKNOWN:'删除结果待核验',IN_FLIGHT:'删除结果待核验',NOT_DELETED:'核查后文件仍存在；再次删除需要新预览',CONFLICT:'文件或目录身份变化；请重新预览',PREVIEWED:'尚未执行删除'};
const kinds:Record<string,string>={attempts:'下游任务',chain_runs:'计算链',scan_batches:'扫描批次','analyses-v2':'本机分析',analyses:'本机分析','wannier-bundles':'Wannier 输入','plot-datasets':'保存的图表','wannier-comparisons':'比较记录'};
const size=(n:number)=>n>=1024**3?`${(n/1024**3).toFixed(2)} GiB`:`${(n/1024**2).toFixed(2)} MiB (${n} B)`;

export default function ArtifactDelete({projectId,attemptId,path,onClose,onDeleted}:{projectId:string;attemptId:string;path:string|null;onClose:()=>void;onDeleted:(path:string)=>Promise<void>}){
 const [data,setData]=useState<Receipt|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[attempted,setAttempted]=useState(false);
 const dialog=useRef<HTMLDialogElement>(null),cancel=useRef<HTMLButtonElement>(null),inFlight=useRef(false),ticket=useRef(0),mounted=useRef(true);
 const signature=projectId+':'+attemptId+':'+path,active=useRef(signature);active.current=signature;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;ticket.current++;};},[]);
 useEffect(()=>{if(path&&!busy)cancel.current?.focus();},[busy,data,path]);
 const load=async()=>{
  if(!path||inFlight.current)return;const current=++ticket.current;setBusy(true);setError('');setNotice('');setData(null);setAttempted(false);
  try{const value:Receipt=await requestJson(`/api/execution/${attemptId}/artifact-delete-preview`,{project_id:projectId,path});if(mounted.current&&current===ticket.current&&signature===active.current){setData(value);if(value.result!=='PREVIEWED')setAttempted(true);}}
  catch(e){if(mounted.current&&current===ticket.current&&signature===active.current)setError(errorMessage(e));}
  finally{if(mounted.current&&current===ticket.current)setBusy(false);}
 };
 useEffect(()=>{ticket.current++;setData(null);setError('');setNotice('');setAttempted(false);if(path){dialog.current?.showModal();void load();}else dialog.current?.close();},[signature]);
 const apply=async(value:Receipt)=>{
  if(!mounted.current||signature!==active.current)return;setData(value);
  if(value.result==='DELETED'){
   setNotice('已删除远端文件。本机冻结包、下载副本及历史记录保留。');
   window.dispatchEvent(new Event('fpilot:binding-changed'));
   try{await onDeleted(value.path);if(value.cache_error&&signature===active.current)setError('已删除，缓存待核验：'+value.cache_error);}
   catch(e){if(mounted.current&&signature===active.current)setError('已删除，文件列表待刷新：'+errorMessage(e));}
  }else setNotice(labels[value.result]||value.result);
 };
 const remove=async()=>{
  if(inFlight.current||!data||data.path!==path||data.result!=='PREVIEWED'||attempted)return;
  inFlight.current=true;setBusy(true);setError('');setAttempted(true);
  try{await apply(await requestJson(`/api/execution/${attemptId}/artifact-delete`,{project_id:projectId,path,token:data.token,confirmed:true}));}
  catch(e){if(mounted.current&&signature===active.current){setData({...data,result:'UNKNOWN'});setError('删除结果待核验：'+errorMessage(e));}}
  finally{inFlight.current=false;if(mounted.current&&signature===active.current)setBusy(false);}
 };
 const check=async()=>{
  if(inFlight.current||!data)return;inFlight.current=true;setBusy(true);setError('');
  try{await apply(await requestJson(`/api/execution/${attemptId}/artifact-delete/${data.operation_id}?project_id=${encodeURIComponent(projectId)}`));}
  catch(e){if(mounted.current&&signature===active.current)setError(errorMessage(e));}
  finally{inFlight.current=false;if(mounted.current&&signature===active.current)setBusy(false);}
 };
 return <dialog ref={dialog} className="history-confirm" aria-label="确认删除远端文件" onCancel={e=>{e.preventDefault();if(!busy)onClose();}}>
  <h2>永久删除此文件？</h2><p className="cleanup-record-label">{path}</p>
  <p>仅删除所选远端普通文件，无法撤销。本机冻结包、下载副本及任务历史保留。</p>
  {data&&data.path===path&&<><p>服务器：{data.alias} · 大小：{size(data.size_bytes)}</p><div className="remote-result-path"><code>{data.remote_path}</code></div><p>attempt：{data.attempt_id}<br/>任务：{statusLabel(data.state)} · job ID：{data.job_id||'待核查'} · 调度核查：{data.scheduler_state||'待核查'}</p>
   <p>确认有效至 {new Date(data.expires_at).toLocaleString()}。硬链接或仍打开的文件可能不会立即释放全部空间{data.link_count>1?`（当前硬链接 ${data.link_count} 个）`:''}。</p>
   {data.references.length>0?<><p className="handoff-warning">已知或潜在引用如下，仍可确认删除。缺少此来源的后续准备或派发会停止；原计划与授权保留。</p><ul>{data.references.map(r=><li key={r.kind+r.id}>{kinds[r.kind]||r.kind} · {r.id} · {r.state?statusLabel(r.state):'冻结计划'} · {r.relation} · {r.certainty}</li>)}</ul></>:<p>未发现已记录的下游引用。</p>}
   {data.note&&<p>{data.note}</p>}</>}
  {busy&&<p role="status">{attempted?'正在删除或只读核查结果…':'正在只读核对文件、调度状态与下游引用…'}</p>}
  {notice&&<p role="status">{notice}</p>}{error&&<p className="structure-error" role="alert">{error}</p>}
  <div className="action-row"><button ref={cancel} className="button" autoFocus disabled={busy} onClick={onClose}>{attempted?'关闭':'取消'}</button>
   {data&&attempted&&<button className="button" disabled={busy} onClick={()=>void check()}>只读核查删除结果</button>}
   {!data||attempted&&['PREVIEWED','NOT_DELETED','CONFLICT'].includes(data.result)?<button className="button" disabled={busy} onClick={()=>void load()}>重新只读预览</button>:null}
   {data?.result==='PREVIEWED'&&!attempted&&<button className="button danger" disabled={busy||data.path!==path} onClick={()=>void remove()}>永久删除此文件</button>}
  </div>
 </dialog>;
}
