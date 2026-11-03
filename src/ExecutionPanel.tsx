import Feedback from './Feedback';
import RunRecordOverview from './RunRecordOverview';
import TaskRecovery from './TaskRecovery';
import CommandOutput from './CommandOutput';
import CancelTask from './CancelTask';
import ProgramAdaptation from './ProgramAdaptation';
import {useSubmissionDraft} from './useSubmissionDraft';
import {sameCapacityTarget} from './executionUi';
import {useEffect,useRef,useState} from 'react';
import type {Project} from './model';
import type {ServerConfig,ServerProbe,RegisteredProfile} from './executionUi';
import {requestJson,errorMessage} from './apiClient';
import {inputForReview,isReviewableInput} from './workflowUi';
import {runtimeFor,emptySettings} from './submissionDefaults';
import type {WorkbenchSettings} from './submissionDefaults';
import ServerPanel from './ServerPanel';
import SubmitConfirmation from './SubmitConfirmation';
import HistoryList from './HistoryList';
import {DeleteRecord,RecycleBin} from './RecordCleanup';
import {useRequestScope} from './useRequestScope';
import RemoteDeleteAttempt from './RemoteDeleteAttempt';
import {statusLabel} from './statusLabels';
import {attemptLabel} from './humanLabels';
type Bundle={id:string;node_id:string;stage:string;reason:string;status:string;created_at?:string;requires_ncl?:boolean;dependencies?:unknown[];source_contract?:{mode:string;roles:Record<string,{attempt_id:string;alias:string;file:string;differences?:string[]}>}};
type Attempt={input_note?:string|null;chain_run_id?:string;id:string;state:string;node_id:string;bundle_id:string;created_at:string;job_id?:string;note?:string;config:ServerConfig;node:string;profile?:{ppn?:number;ntasks?:number;mpi_ranks?:number;gpus?:number;required_gpus?:number};script:string;remote_path:string;remote_path_absolute?:string;input_audit?:unknown;evidence?:Record<string,unknown>;source_contract?:Bundle['source_contract'];source_file_plan?:Record<string,{target:string;mode:string}>;template_snapshot?:{id:string;program_adaptation?:{source_program:string;target_program:string;target_command:string};program_diff?:string}};
export default function ExecutionPanel({project,initialNodeId,initialBundleId,initialAttemptId,onAttemptChange,onResults}:{project:Project;initialNodeId?:string|null;initialBundleId?:string;initialAttemptId?:string;onAttemptChange?:(id:string)=>void;onResults?:(nodeId:string,attemptId:string)=>void}){
 const draftKey='single:'+project.id+':'+initialNodeId,requests=useRequestScope(draftKey);
 const [config,setConfig]=useSubmissionDraft<ServerConfig|null>(draftKey+':config',null),[profiles,setProfiles]=useState<RegisteredProfile[]>([]),[probe,setProbe]=useSubmissionDraft<ServerProbe|null>(draftKey+':probe',null);
 const [bundles,setBundles]=useState<Bundle[]>([]),[bundle,setBundle]=useSubmissionDraft(draftKey+':bundle',''),[template,setTemplate]=useSubmissionDraft(draftKey+':template','');
 const [node,setNode]=useSubmissionDraft(draftKey+':node',''),[cores,setCores]=useSubmissionDraft(draftKey+':cores',''),[reason,setReason]=useSubmissionDraft(draftKey+':reason','');
 const [settings,setSettings]=useState<WorkbenchSettings>(emptySettings);
 const [cursor,setCursor]=useState<string|null>(null),[detail,setDetail]=useState<Attempt|null>(null);
 const [items,setItems]=useState<Attempt[]>([]),[selected,setSelected]=useState(initialAttemptId||''),[confirmation,setConfirmation]=useState<Attempt|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[status,setStatus]=useState('');
 const [readErrors,setReadErrors]=useState<Record<string,string>>({}),[loadingHistory,setLoadingHistory]=useState(true);
 const selectedRef=useRef(selected),mounted=useRef(true);selectedRef.current=selected;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const readError=(key:string,message='')=>setReadErrors(old=>({...old,[key]:message}));
 const chooseAttempt=(id:string)=>{selectedRef.current=id;setSelected(id);onAttemptChange?.(id);};
 const selectRecord=(id:string)=>{requests.start('history');setLoadingHistory(false);setConfirmation(null);chooseAttempt(id);};
 const listURL='/api/execution?view=summary&limit=50&project_id='+encodeURIComponent(project.id)+'&node_id='+encodeURIComponent(initialNodeId||'');
 const reload=async(target=selectedRef.current)=>{
  const ticket=requests.start('history');setLoadingHistory(true);
  try{
   const page=await requestJson(listURL,undefined,undefined,{signal:ticket.signal});if(!ticket.current())return;
   let rows:Attempt[]=page.items;setItems(rows);setCursor(page.next_cursor);readError('history');
   if(target&&!rows.some(row=>row.id===target)){
    const row:Attempt=await requestJson('/api/execution/'+encodeURIComponent(target)+'?project_id='+encodeURIComponent(project.id),undefined,undefined,{signal:ticket.signal});
    if(!ticket.current())return;
    if(row.node_id!==initialNodeId)throw new Error('指定运行记录不属于当前节点，请重新选择。');
    rows=[row,...rows];setItems(rows);
   }
   if(ticket.current())chooseAttempt(target||rows[0]?.id||'');
  }catch(e){if(ticket.current())readError('history',errorMessage(e));}
  finally{if(ticket.current())setLoadingHistory(false);}
 };
 const loadInputs=async()=>{
  const ticket=requests.start('inputs');
  try{
   const inputs:Bundle[]=await requestJson('/api/inputs/history?project_id='+encodeURIComponent(project.id),undefined,undefined,{signal:ticket.signal});if(!ticket.current())return;
   const chosen=inputForReview(inputs,initialNodeId,initialBundleId||null);setBundles(inputs);
   setBundle(old=>initialBundleId?chosen:inputs.some(b=>b.id===old&&b.node_id===initialNodeId&&isReviewableInput(b))?old:chosen);
   readError('inputs',initialBundleId&&!chosen?'此冻结版本已不可用，请返回计算配置重新审查。':'');
  }catch(e){if(ticket.current())readError('inputs',errorMessage(e));}
 };
 const loadServer=async()=>{
  const ticket=requests.start('server');
  const [server,saved]=await Promise.allSettled([requestJson('/api/server',undefined,undefined,{signal:ticket.signal}),requestJson('/api/workbench-settings',undefined,undefined,{signal:ticket.signal})]);
  if(!ticket.current())return;
  if(saved.status==='fulfilled'){setSettings(saved.value);readError('settings');}else readError('settings',errorMessage(saved.reason));
  if(server.status==='fulfilled'){
   setProfiles(server.value.registered_profiles||[]);readError('server');
   setConfig(old=>old||runtimeFor(server.value.config,server.value.registered_profiles||[],saved.status==='fulfilled'?saved.value:emptySettings,server.value.config.alias,'vasp_std'));
  }else readError('server',errorMessage(server.reason));
 };
 useEffect(()=>{void reload();void loadInputs();void loadServer();},[project.id,initialNodeId,initialBundleId]);
 useEffect(()=>{if(initialAttemptId&&initialAttemptId!==selectedRef.current)void reload(initialAttemptId);},[initialAttemptId]);
 useEffect(()=>{const changed=()=>{setConfirmation(null);void reload();void loadInputs();};window.addEventListener('fpilot:history-changed',changed);return()=>window.removeEventListener('fpilot:history-changed',changed);},[project.id,initialNodeId,initialBundleId]);
 const input=bundles.find(b=>b.id===bundle),current=detail?.id===selected?detail:null;
 useEffect(()=>{const ticket=requests.start('detail');setDetail(null);if(!selected)return;void requestJson('/api/execution/'+encodeURIComponent(selected)+'?project_id='+encodeURIComponent(project.id),undefined,undefined,{signal:ticket.signal}).then(value=>{if(ticket.current()){setDetail(value);readError('detail');}}).catch(e=>{if(ticket.current())readError('detail',errorMessage(e));});return()=>{requests.start('detail');};},[selected,items,project.id]);
 const changeConfig=(next:ServerConfig)=>{setConfig(next);setTemplate(next.template_id||'');setConfirmation(null);if(!sameCapacityTarget(config,next)){setProbe(null);setNode('');}};
 // Input selection is synchronous: late settings responses can no longer restore an older input's runtime.
 const chooseBundle=(id:string)=>{
  setBundle(id);setConfirmation(null);
  const chosen=bundles.find(item=>item.id===id),source=Object.values(chosen?.source_contract?.roles||{})[0];
  if(config)changeConfig(runtimeFor(config,profiles,settings,source?.alias||config.alias,chosen?.requires_ncl?'vasp_ncl':'vasp_std'));
 };
 useEffect(()=>{if(!config||!input)return;const source=Object.values(input.source_contract?.roles||{})[0],program=input.requires_ncl?'vasp_ncl':'vasp_std';if(config.program!==program||source?.alias&&config.alias!==source.alias)changeConfig(runtimeFor(config,profiles,settings,source?.alias||config.alias,program));},[input?.id,config?.program,config?.alias,profiles,settings]);
 const detect=async()=>{if(!config)return;const ticket=requests.start('probe');setBusy(true);setError('');try{const value=await requestJson('/api/server/probe',{config},undefined,{signal:ticket.signal,timeoutMs:120_000});if(ticket.current()){setProbe(value);setStatus('节点检测完成');}}catch(e){if(ticket.current())setError(errorMessage(e));}finally{if(ticket.current())setBusy(false);}};
 const prepare=async()=>{if(!config||!node||!bundle)return;setBusy(true);setError('');try{if(input?.dependencies?.length&&!input.source_contract)throw new Error('旧冻结输入没有来源合同，请返回计算配置选择来源并重新冻结。');const attempt=await requestJson('/api/execution/prepare',{project_id:project.id,bundle_id:bundle,config,probe_id:probe?.id||'',node,cores:cores?Number(cores):null,reason,template_id:template||null});if(!mounted.current)return;chooseAttempt(attempt.id);setConfirmation(attempt);await reload(attempt.id);}catch(e){if(mounted.current){setError(errorMessage(e));await reload();}}finally{if(mounted.current)setBusy(false);}};
 const submit=async()=>{if(!confirmation)return;const id=confirmation.id;setBusy(true);setError('');try{const attempt=await requestJson('/api/execution/'+id+'/submit',{});if(!mounted.current)return;setConfirmation(null);await reload(id);setStatus(config?.scheduler==='custom'?'命令启动回执已保存，可读取原始日志':attempt.job_id?'提交成功 · '+attempt.job_id:attempt.not_dispatched?'实时复核未通过，没有调用调度器；草稿和节点选择保留，可刷新或稍后重新准备。':'回执待核查，已保留服务器预留');}catch(e){if(mounted.current){setConfirmation(null);setError(errorMessage(e)+' 已保留本次运行记录；请核查原任务，不要重复提交。');await reload(id);}}finally{if(mounted.current)setBusy(false);}};
 const refresh=async()=>{if(!current)return;setBusy(true);setError('');try{await requestJson('/api/execution/'+current.id+'/refresh',{});if(mounted.current)await reload();}catch(e){if(mounted.current)setError(errorMessage(e));}finally{if(mounted.current)setBusy(false);}};

 return <section className="input-workspace simple-submit"><div className="action-row"><button className="text-button" disabled={loadingHistory} onClick={()=>void reload()}>{loadingHistory?'正在读取运行记录…':'刷新运行记录'}</button></div>{readErrors.history&&<p role="alert">运行记录：{readErrors.history}</p>}{readErrors.detail&&<p role="alert">记录详情：{readErrors.detail}<button className="text-button" onClick={()=>void reload()}>重试</button></p>}<RunRecordOverview rows={items.filter(a=>a.node_id===initialNodeId&&!('scan_batch_id' in a))} selected={selected} onSelect={selectRecord}/> {current&&<section className="input-submit-card"><h3>{statusLabel(current.state)} · {current.job_id||(current.config.scheduler==='custom'?'自定义命令':current.state==='PREPARED'?'尚未提交':'未取得 job ID')}</h3>{current.config.scheduler==='custom'?<CommandOutput key={current.id} attemptId={current.id} alias={current.config.alias} onChanged={()=>void reload()}/>:<CancelTask key={current.id} projectId={project.id} attemptId={current.id} state={current.state} onChanged={reload}/>}{current.note&&<p role="status">{current.note}</p>}{onResults&&<button className="button" onClick={()=>onResults(current.node_id,current.id)}>查看结果与执行证据</button>}{!['PREPARED','COMPLETED','FAILED','CANCELLED'].includes(current.state)&&<button className="button" disabled={busy} onClick={()=>void refresh()}>核查原任务</button>}<TaskRecovery projectId={project.id} kind="attempts" targetId={current.id} onChanged={reload}/>{current.state==='UNKNOWN'&&<button className="button" onClick={async()=>{try{await requestJson('/api/execution/'+current.id+'/release-quota',{project_id:project.id});await reload();setStatus('本机额度占用已解除，原作业继续跟踪');}catch(e){setError(errorMessage(e));}}}>解除本机额度占用</button>}{current.state==='PREPARED'&&<p>未提交的旧预览可查看；请选择输入重新准备后提交。</p>}<details><summary>记录与维护</summary><p>{current.remote_path_absolute||current.remote_path}</p><RemoteDeleteAttempt projectId={project.id} attemptId={current.id} state={current.state} onDeleted={()=>{chooseAttempt('');void reload();}} onOpenAttempt={selectRecord}/><a href={'/api/execution/'+current.id+'/technical-evidence'} download>下载技术证据</a><a href={'/api/execution/'+current.id+'/failure-snapshot'} download>下载失败快照</a><pre className="input-text">{current.script}</pre></details></section>}<h2 className="new-submission-heading">准备新任务</h2>{['inputs','server','settings'].filter(key=>readErrors[key]).map(key=><p key={key} role="alert">{({inputs:'冻结输入',server:'服务器配置',settings:'默认设置'} as Record<string,string>)[key]}：{readErrors[key]}<button className="text-button" onClick={()=>void (key==='inputs'?loadInputs():loadServer())}>重试读取</button></p>)}<section className="input-submit-card"><h3>1 · 输入</h3><label>当前节点冻结输入<select value={bundle} disabled={busy} onChange={e=>chooseBundle(e.target.value)}><option value="">请选择</option>{bundles.filter(b=>b.node_id===initialNodeId&&isReviewableInput(b)).map(b=><option key={b.id} value={b.id}>{b.created_at?new Date(b.created_at).toLocaleString():b.stage} · {b.reason}</option>)}</select></label>{input?.source_contract&&<p>{input.source_contract.mode==='new'?'新计算':Object.values(input.source_contract.roles).map(r=>r.file+' · '+r.alias).join('；')}</p>}{!bundle&&<p>请先在计算配置中审查并冻结输入。</p>}</section>
 <ServerPanel settings={settings} project={project} scopeId={initialNodeId||input?.node_id} program={input?.requires_ncl?'vasp_ncl':'vasp_std'} onTemplate={setTemplate} config={config} profiles={profiles} probe={probe} preview={null} node={node} cores={cores} reason={reason} busy={busy} canProbe={Boolean(bundle)} onConfigChange={changeConfig} onNodeChange={v=>{setNode(v);setConfirmation(null);}} onCoresChange={v=>{setCores(v);setConfirmation(null);}} onReasonChange={v=>{setReason(v);setConfirmation(null);}} onSaveAndProbe={()=>void detect()} onProbe={()=>void detect()} onPreview={()=>{}}/>
 <section className="input-submit-card"><h3>4 · 提交</h3><button className="button primary" disabled={busy||!config||!bundle||!node} onClick={()=>void prepare()}>{busy?'正在核对…':'准备提交'}</button><Feedback message={status} error={error} busy={busy} label="正在核对本次请求…"/></section>
 {confirmation&&<SubmitConfirmation busy={busy} script={confirmation.script} onClose={()=>setConfirmation(null)} onConfirm={()=>void submit()}><p>{confirmation.config.alias} · {confirmation.remote_path_absolute||confirmation.remote_path}</p><p hidden={confirmation.config.scheduler==='custom'}>{confirmation.node} · {confirmation.profile?.ppn||confirmation.profile?.ntasks||cores||'全部可用'} 核</p>{confirmation.profile?.gpus&&<p>整节点 GPU {confirmation.profile.gpus} 张 · 脚本所需 {confirmation.profile.required_gpus} 张 · MPI {confirmation.profile.mpi_ranks} 进程</p>}<ProgramAdaptation change={confirmation.template_snapshot?.program_adaptation} diff={confirmation.template_snapshot?.program_diff}/><p>输入：{input?.reason||'已选冻结输入'}</p>{Object.values(confirmation.source_contract?.roles||{}).map(r=><p key={r.file}>{r.file}：{r.alias} · {items.find(a=>a.id===r.attempt_id)?attemptLabel(items.find(a=>a.id===r.attempt_id)!,project):'历史计算来源（本机记录缺失）'}</p>)}{Array.from(new Set(Object.values(confirmation.source_contract?.roles||{}).flatMap(r=>r.differences||[]))).map(d=><p key={d} className="structure-warning">{d}</p>)}{error&&<p role="alert">{error}</p>}</SubmitConfirmation>}
 <details className="run-history"><summary>最近运行与历史</summary>{cursor&&<button className="button" onClick={async()=>{try{const page=await requestJson(listURL+'&cursor='+encodeURIComponent(cursor));setItems(old=>[...old,...page.items.filter((a:Attempt)=>!old.some(b=>b.id===a.id))]);setCursor(page.next_cursor);}catch(e){setError(errorMessage(e));}}}>再加载 50 条记录</button>}<HistoryList rows={items.filter(a=>a.node_id===initialNodeId&&!('scan_batch_id' in a))} limit={3} context={project.id+initialNodeId} render={a=><div className="history-row" key={a.id}><button className="text-button" title={attemptLabel(a,undefined,true)} onClick={()=>selectRecord(a.id)}>{attemptLabel(a)}</button><DeleteRecord kind="attempts" id={a.id} state={a.state} label="计算记录" onChanged={async()=>{if(selected===a.id)setSelected('');await reload();}}/></div>}/><RecycleBin kind="attempts" projectId={project.id} onChanged={reload}/></details>

 </section>;
}
