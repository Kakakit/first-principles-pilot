import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {statusLabel} from './statusLabels';

type Preview={state:string;token?:string;job_id?:string;alias?:string;remote_path?:string;local?:boolean;error?:string;attempt_state?:string;facts?:{scheduler_state?:string;process_pid?:number;command_output?:string};references?:{kind:string;label:string;state:string}[]};
const messages:Record<string,string>={ACCEPTED:'调度器已接受取消请求，尚待核查是否停止。',UNKNOWN:'取消结果待核验。请核查原请求；不会自动重复取消。',IN_FLIGHT:'取消请求已登记，正在核查原请求。',CONFIRMED:'已确认停止，任务记录和文件保留。',FINISHED:'调度器显示原任务已结束，请查看原任务的完成或失败证据。',REJECTED:'调度器拒绝取消，请查看原因后重新预览。',NOT_SENT:'连接未建立，已确认取消命令未发送。恢复连接后请重新预览并确认。'};

export default function CancelTask({projectId,attemptId,batchId,caseId,state,label,direct=false,onChanged}:{direct?:boolean;projectId:string;attemptId?:string|null;batchId?:string;caseId?:string;state?:string;label?:string;onChanged:()=>void|Promise<void>}){
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[preview,setPreview]=useState<Preview|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const dialog=useRef<HTMLDialogElement>(null),keep=useRef<HTMLButtonElement>(null),generation=useRef(0);
 const context=[projectId,attemptId,batchId,caseId].join(':');
 useEffect(()=>{generation.current++;setBusy(false);setOpen(false);setPreview(null);setError('');setNotice('');return()=>{generation.current++;};},[context]);
 useEffect(()=>{if(open){dialog.current?.showModal();keep.current?.focus();}else dialog.current?.close();},[open]);
 const verb=direct?'停止':'取消';
 const directMessages:Record<string,string>={ACCEPTED:'已发送停止请求，正在核查进程退出。',FINISHED:'原任务已结束，请查看完成或失败证据。',REJECTED:'停止请求未接受，请查看原因。',UNKNOWN:'停止结果待核查，可查询原请求。',RETRYABLE:'停止执行器未完成，可重试原停止操作。'};
 const pending=['ACCEPTED','UNKNOWN','IN_FLIGHT'].includes(preview?.state||'');
 const retryable=direct&&preview?.state==='RETRYABLE';
 const work=async(action:'preview'|'cancel'|'status'|'retry')=>{
  const current=generation.current;setBusy(true);setError('');
  try{
   const value:Preview=attemptId?await requestJson(`/api/execution/${attemptId}/${action==='preview'?'cancel-preview':action==='status'?'cancel-status':'cancel'}`,{project_id:projectId,...(action==='cancel'||action==='retry'?{token:preview?.token,confirm:true}:{}),...(action==='retry'?{retry:true}:{})}):action==='preview'?{state:'PREVIEW',local:true}:await requestJson(`/api/scans/${batchId}/cases/${caseId}/cancel`,{project_id:projectId,confirm:true}).then(()=>({state:'CONFIRMED',local:true}));
   if(current!==generation.current)return;
   setPreview(value);
   if(action==='preview'&&value.state==='PREVIEW')setOpen(true);
   else{
    setOpen(false);setNotice((direct?directMessages[value.state]:undefined)||messages[value.state]||'原请求已核查。');
    // A history reload remounts this control in the run panel. Keep the stop
    // receipt available while only the operation state, not the task, changed.
    if(!direct||value.attempt_state&&value.attempt_state!==state){
     try{await onChanged();}catch(e){setError('操作已记录，页面刷新失败：'+errorMessage(e));}
    }
   }
  }catch(e){if(current===generation.current)setError(errorMessage(e));}finally{if(current===generation.current)setBusy(false);}
 };
 if(['COMPLETED','FAILED','CANCELLED'].includes(state||'')&&!notice&&!pending)return null;
 if(!attemptId&&(!batchId||!caseId||state!=='PENDING')&&!notice)return null;
 return <div className="task-cancel"><button className="text-button" disabled={busy||!pending&&['COMPLETED','FAILED','CANCELLED'].includes(state||'')} onClick={()=>void work(retryable?'retry':pending?'status':'preview')}>{busy?'正在核查…':retryable?'重试停止':pending?'核查'+verb+'结果':verb+'任务'}</button>{notice&&<p role="status">{notice}</p>}{error&&<p className="structure-error" role="alert">{error}</p>}{preview?.error&&<p role="alert">{preview.error}</p>}{preview?.facts?.command_output&&<p>{preview.facts.command_output}</p>}<dialog ref={dialog} className="history-confirm" aria-label={'确认'+verb+'任务'} onCancel={e=>{e.preventDefault();if(!busy)setOpen(false);}}><h3>{verb}此任务？</h3><p><strong>{label||'当前任务'}</strong> · {preview?.alias} · {preview?.job_id||(preview?.facts?.process_pid?'PID '+preview.facts.process_pid:preview?.local?'尚未启动':'待核查')}</p>{preview?.remote_path&&<code>{preview.remote_path}</code>}<p>{preview?.local?'取消本机待派发任务，不向服务器提交。':direct?'向本次任务的进程组发送停止信号。':'向调度器取消这个准确作业号。排队任务可能在确认前开始运行；运行任务也将停止。'}</p><p>保留输入、文件和历史记录。{!direct&&'所属批次或计算链的后续派发会暂停，其他已运行任务保持。'}</p>{preview?.references?.map((r,i)=><p key={i}>{r.kind==='scans'?'迭代批次':'计算链'}：{r.label} · {statusLabel(r.state)}</p>)}<div className="action-row"><button className="button" ref={keep} autoFocus disabled={busy} onClick={()=>setOpen(false)}>保留任务</button><button className="button danger" disabled={busy} onClick={()=>void work('cancel')}>确认{verb}此任务</button></div></dialog></div>;
}
