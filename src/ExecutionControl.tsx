import Feedback from './Feedback';
import {useEffect,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {useRequestScope} from './useRequestScope';
import {useSettingsDraft} from './useSubmissionDraft';

type Limits={max_inflight_total:number|null;max_inflight_per_host:number|null;max_cores_per_host:number|null;worker_interval_seconds?:number};
type Status={healthy:boolean;running:boolean;pid?:number;heartbeat_age_seconds?:number;interval_seconds?:number;interval_source?:string};
type Draft=Partial<Pick<Limits,'max_inflight_total'|'max_inflight_per_host'|'max_cores_per_host'>>&{interval?:string};
const limitFields=[['max_inflight_total','总在途任务上限'],['max_inflight_per_host','每主机在途任务上限'],['max_cores_per_host','每主机在途核数上限']] as const;

export default function ExecutionControl(){
 const [saved,setSaved]=useState<Limits|null>(null),[worker,setWorker]=useState<Status|null>(null);
 const [message,setMessage]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [draft,setDraft]=useSettingsDraft<Draft>('worker-limits',{});
 const requests=useRequestScope('worker-limits');
 const {interval:intervalDraft,...limitDraft}=draft;
 const form={...saved,...limitDraft},interval=intervalDraft??String(saved?.worker_interval_seconds??300);
 const dirty=Object.keys(draft).length>0;
 const update=(key:keyof Draft,value:Draft[keyof Draft])=>setDraft(old=>{
  const baseline=key==='interval'?String(saved?.worker_interval_seconds??300):saved?.[key];
  const next={...old,[key]:value};if(value===baseline)delete next[key];return next;
 });
 const load=async()=>{
  const request=requests.start('status');setError('');
  try{
   const data=await requestJson('/api/execution-control',undefined,undefined,{signal:request.signal});
   if(request.current()){setSaved(data.settings);setWorker(data.worker);}
  }catch(e){if(request.current())setError(errorMessage(e));}
 };
 useEffect(()=>{void load();},[requests]);
 const save=async()=>{
  setBusy(true);setError('');setMessage('');
  try{
   const seconds=Number(interval);
   if(!interval.trim()||!Number.isSafeInteger(seconds)||seconds<1)throw new Error('监控周期须为正整数秒');
   const data=await requestJson('/api/execution-control',{...form,worker_interval_seconds:seconds},'PUT');
   setSaved(data);setDraft({});
   setMessage(worker?.interval_source==='command-line'?'已保存。当前启动参数优先，正常重启后采用保存值。':'已保存。后台监控将在后续周期采用设置。');
  }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 };
 return <section className="input-submit-card">
  <h3>刷新周期与在途额度</h3>
  <p>当前监控 · {worker?.healthy?'正在运行':worker?.running?'心跳已过期':'未运行'}{worker?.interval_seconds?' · '+worker.interval_seconds+' 秒':''}。已保存周期 {saved?.worker_interval_seconds??300} 秒。</p>
  {saved&&<fieldset disabled={busy}>
   <label>核查周期（秒）<input type="number" min="1" step="1" value={interval} onChange={e=>update('interval',e.target.value)}/></label>
   {limitFields.map(([key,label])=><label key={key}>{label}<input type="number" min="1" value={form[key]??''} onChange={e=>update(key,e.target.value===''?null:Number(e.target.value))}/></label>)}
   <p>上限留空时不额外限制。已授权的计算链和迭代按依赖与额度推进。</p>
   <div className="action-row"><button className="button primary" disabled={!dirty} onClick={()=>void save()}>保存监控设置</button>{dirty&&<><button className="button" onClick={()=>setDraft({})}>放弃修改</button><span role="status">有未保存修改</span></>}</div>
  </fieldset>}
  <details><summary>监控诊断</summary><p>进程 {worker?.pid||'—'} · 心跳距今 {worker?.heartbeat_age_seconds??'—'} 秒</p><button className="button" onClick={()=>void load()}>刷新监控状态</button></details>
  <Feedback message={message} error={error} busy={busy} label="正在保存监控设置…"/>
 </section>;
}
