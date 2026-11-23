import RunInputDifference from './RunInputDifference';
import {inputVersionLabel} from './humanLabels';
import DisclosureLabel from './DisclosureLabel';
import {recordPresentation} from './recordPresentation';
import {actionCheckKey,showActionIssues,issueFromError} from './actionIssues';
import {useActionCheck} from './useActionCheck';
import {prepareIssues} from './inputActionIssues';
import Feedback from './Feedback';
import RunRecordOverview from './RunRecordOverview';
import TaskRecovery from './TaskRecovery';
import CommandOutput from './CommandOutput';
import CancelTask from './CancelTask';
import ProgramAdaptation from './ProgramAdaptation';
import {useContext} from 'react';
import {RunPreferenceContext,useSubmissionDraft} from './useSubmissionDraft';
import {commandMode,sameCapacityTarget,type DirectRuntime} from './executionUi';
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
type Attempt={status_receipt?:{stdout?:string;stderr?:string};input_note?:string|null;chain_run_id?:string;id:string;state:string;node_id:string;bundle_id:string;created_at:string;job_id?:string;direct_runtime?:DirectRuntime;tracking_error?:string;note?:string;config:ServerConfig;node:string;profile?:{ppn?:number;ntasks?:number;mpi_ranks?:number;gpus?:number;required_gpus?:number};script:string;remote_path:string;remote_path_absolute?:string;input_audit?:unknown;evidence?:Record<string,unknown>;source_contract?:Bundle['source_contract'];source_file_plan?:Record<string,{target:string;mode:string}>;template_snapshot?:{id:string;program_adaptation?:{source_program:string;target_program:string;target_command:string};program_diff?:string}};
const runViews=recordPresentation<{row:Attempt;detailHeight:number}>();
export default function ExecutionPanel({project,initialNodeId,initialBundleId,initialAttemptId,onAttemptChange,onResults}:{project:Project;initialNodeId?:string|null;initialBundleId?:string;initialAttemptId?:string;onAttemptChange?:(id:string,reason:'user'|'default'|'replace')=>void;onResults?:(nodeId:string,attemptId:string,subpage?:string)=>void}){
 const draftKey='single:'+project.id+':'+initialNodeId,requests=useRequestScope(draftKey);
 const previous=runViews.read(project.id,initialNodeId||'',initialAttemptId);
 const preferences=useContext(RunPreferenceContext);
 const [config,setConfig]=useSubmissionDraft<ServerConfig|null>(draftKey+':config',null),[profiles,setProfiles]=useState<RegisteredProfile[]>([]),[probe,setProbe]=useSubmissionDraft<ServerProbe|null>(draftKey+':probe',null);
 const [bundles,setBundles]=useState<Bundle[]>([]),[bundle,setBundle]=useSubmissionDraft(draftKey+':bundle',''),[template,setTemplate]=useSubmissionDraft(draftKey+':template','');
 const [node,setNode]=useSubmissionDraft(draftKey+':node',''),[cores,setCores]=useSubmissionDraft(draftKey+':cores',''),[reason,setReason]=useSubmissionDraft(draftKey+':reason','');
 const pendingMetadata=useRef<Promise<unknown>[]>([]);
 const [settings,setSettings]=useState<WorkbenchSettings>(emptySettings);
 const [cursor,setCursor]=useState<string|null>(null),[detail,setDetail]=useState<Attempt|null>(null);
 const [items,setItems]=useState<Attempt[]>([]),[selected,setSelected]=useState(initialAttemptId||previous?.row.id||''),[confirmation,setConfirmation]=useState<Attempt|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[status,setStatus]=useState('');
 const [readErrors,setReadErrors]=useState<Record<string,string>>({}),[loadingHistory,setLoadingHistory]=useState(true);
 const selectedRef=useRef(selected),mounted=useRef(true);selectedRef.current=selected;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const readError=(key:string,message='')=>setReadErrors(old=>({...old,[key]:message}));
 const chooseAttempt=(id:string,reason:'user'|'default'|'replace'='default')=>{selectedRef.current=id;setSelected(id);onAttemptChange?.(id,reason);};
 const selectRecord=(id:string)=>{requests.start('history');setLoadingHistory(false);setConfirmation(null);chooseAttempt(id,'user');};
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
  }catch(e){if(ticket.current()){runViews.forget(project.id,initialNodeId||'');setDetail(null);readError('history',errorMessage(e));}}
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
   setConfig(old=>{
    const registered:RegisteredProfile[]=server.value.registered_profiles||[];
    if(!old||!old.alias){
     const alias=registered.find(p=>p.selectable&&p.alias===preferences?.get(draftKey+':alias',''))?.alias||server.value.config.alias;
     const sameHost=preferences?.get(draftKey+':alias','')===alias;
     const program=sameHost&&preferences?.get<string>(draftKey+':program','')==='vasp_ncl'?'vasp_ncl':'vasp_std';
     const next=runtimeFor(server.value.config,registered,saved.status==='fulfilled'?saved.value:emptySettings,alias,program);
     const templateId=sameHost&&preferences?.get(draftKey+':template','');
     if(templateId&&preferences?.get<string>(draftKey+':mode','')==='script')return {...next,runtime_source:'script',template_id:templateId,vasp_executable:program};
     return next;
    }
    const profile=registered.find(item=>item.alias===old.alias);
    return profile&&commandMode(profile)!==commandMode(old)?{...old,command_mode:commandMode(profile)||''}:old;
   });
  }else readError('server',errorMessage(server.reason));
 };
 useEffect(()=>{void reload(initialAttemptId||'');pendingMetadata.current=[loadInputs(),loadServer()];},[project.id,initialNodeId,initialBundleId]);
 useEffect(()=>{if(initialAttemptId&&initialAttemptId!==selectedRef.current)void reload(initialAttemptId);},[initialAttemptId]);
 useEffect(()=>{const changed=()=>{setConfirmation(null);void reload();void loadInputs();};window.addEventListener('fpilot:history-changed',changed);return()=>window.removeEventListener('fpilot:history-changed',changed);},[project.id,initialNodeId,initialBundleId]);
 useEffect(()=>{const changed=()=>void loadServer();window.addEventListener('fpilot:settings-changed',changed);return()=>window.removeEventListener('fpilot:settings-changed',changed);},[requests]);
 const input=bundles.find(b=>b.id===bundle),current=detail?.id===selected?detail:null;
 const [prepareChoice,setPrepareChoice]=useState<boolean|null>(null);
 const [logsFor,setLogsFor]=useState('');
 const detailPanel=useRef<HTMLElement>(null);
 const matching=selected&&!readErrors.history&&!readErrors.detail?runViews.read(project.id,initialNodeId||'',selected):undefined;
 const displayRow=items.find(row=>row.id===selected)||(loadingHistory?matching?.row:undefined);
 useEffect(()=>{
  const row=items.find(item=>item.id===selected);
  if(row&&current&&!readErrors.history&&!readErrors.detail)runViews.save(project.id,initialNodeId||'',selected,{row,detailHeight:detailPanel.current?.offsetHeight||0});
  else if(!loadingHistory&&!items.length)runViews.forget(project.id,initialNodeId||'');
 },[items,current,selected,loadingHistory,project.id,initialNodeId,readErrors.history,readErrors.detail]);
 const prepareOpen=prepareChoice??(Boolean(initialBundleId)||(!displayRow&&!loadingHistory));
 const direct=commandMode(current?.config)==='direct';
 useEffect(()=>{const ticket=requests.start('detail');if(!selected||readErrors.history||!items.some(row=>row.id===selected)){setDetail(null);return;}void requestJson('/api/execution/'+encodeURIComponent(selected)+'?project_id='+encodeURIComponent(project.id),undefined,undefined,{signal:ticket.signal}).then(value=>{if(ticket.current()){setDetail(value);readError('detail');}}).catch(e=>{if(ticket.current()){setDetail(null);runViews.forget(project.id,initialNodeId||'',selected);readError('detail',errorMessage(e));}});return()=>{requests.start('detail');};},[selected,items,project.id,readErrors.history]);
 const changeConfig=(next:ServerConfig)=>{setConfig(next);setTemplate(next.template_id||'');setConfirmation(null);if(!sameCapacityTarget(config,next)){setProbe(null);setNode('');}};
 // Input selection is synchronous: late settings responses can no longer restore an older input's runtime.
 const inputRuntime=(current:ServerConfig,alias:string,program:string)=>{
  const next=runtimeFor(current,profiles,settings,alias,program);
  if(current.alias===alias&&current.resource_policy)next.resource_policy=current.resource_policy;
  return next;
 };
 const chooseBundle=(id:string)=>{
  setBundle(id);setConfirmation(null);
  const chosen=bundles.find(item=>item.id===id),source=Object.values(chosen?.source_contract?.roles||{})[0];
  if(config)changeConfig(inputRuntime(config,source?.alias||config.alias,chosen?.requires_ncl?'vasp_ncl':'vasp_std'));
 };
 useEffect(()=>{if(!config||!input)return;const source=Object.values(input.source_contract?.roles||{})[0],program=input.requires_ncl?'vasp_ncl':'vasp_std';if(config.program!==program||source?.alias&&config.alias!==source.alias)changeConfig(inputRuntime(config,source?.alias||config.alias,program));},[input?.id,config?.program,config?.alias,profiles,settings]);
 const detect=async()=>{if(!config)return;const ticket=requests.start('probe');setBusy(true);setError('');try{const value=await requestJson('/api/server/probe',{config},undefined,{signal:ticket.signal,timeoutMs:120_000});if(ticket.current()){setProbe(value);setStatus('节点检测完成');}}catch(e){if(ticket.current())setError(errorMessage(e));}finally{if(ticket.current())setBusy(false);}};
 const checkKey=actionCheckKey('prepare',project.id,initialNodeId||'');
 const localIssues=()=>prepareIssues({projectId:project.id,nodeId:initialNodeId||'',bundle,alias:config?.alias,node,legacySource:Boolean(input?.dependencies?.length&&!input.source_contract)});
 const latestIssues=useRef(localIssues);latestIssues.current=localIssues;
 useActionCheck(checkKey,async()=>{setPrepareChoice(true);await Promise.allSettled(pendingMetadata.current);await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));return latestIssues.current();});
 const prepare=async()=>{const issues=localIssues();if(issues.length){showActionIssues(issues,checkKey);return;}if(!config)return;setBusy(true);setError('');try{if(input?.dependencies?.length&&!input.source_contract)throw new Error('旧冻结输入没有来源合同，请返回计算配置选择来源并重新冻结。');const attempt=await requestJson('/api/execution/prepare',{project_id:project.id,bundle_id:bundle,config,probe_id:probe?.id||'',node,cores:cores?Number(cores):null,reason,template_id:template||null});if(!mounted.current)return;chooseAttempt(attempt.id,'replace');setConfirmation(attempt);await reload(attempt.id);}catch(e){if(mounted.current){setError(errorMessage(e));showActionIssues(issueFromError(e),checkKey);await reload();}}finally{if(mounted.current)setBusy(false);}};
 const submit=async()=>{if(!confirmation)return;const id=confirmation.id;setBusy(true);setError('');try{const attempt=await requestJson('/api/execution/'+id+'/submit',{});if(!mounted.current)return;setConfirmation(null);await reload(id);setStatus(commandMode(confirmation.config)==='direct'?(attempt.state==='PREPARED'?'连接未建立，准备记录已保留':attempt.state==='UNKNOWN'?'启动待核查，可刷新原任务':attempt.state==='FAILED'?'启动失败，请查看运行记录':'启动回执已保存，可查看运行日志'):confirmation.config.scheduler==='custom'?'命令启动回执已保存，可读取原始日志':attempt.job_id?'提交成功 · '+attempt.job_id:attempt.not_dispatched?'实时复核未通过，没有调用调度器；草稿和节点选择保留，可刷新或稍后重新准备。':'回执待核查，已保留服务器预留');}catch(e){if(mounted.current){setConfirmation(null);setError(errorMessage(e));await reload(id);}}finally{if(mounted.current)setBusy(false);}};
 const refresh=async()=>{if(!current)return;setBusy(true);setError('');try{await requestJson('/api/execution/'+current.id+'/refresh',{});if(mounted.current)await reload();}catch(e){if(mounted.current)setError(errorMessage(e));}finally{if(mounted.current)setBusy(false);}};

 return <section className="input-workspace simple-submit">{readErrors.history&&<p role="alert">运行记录：{readErrors.history}</p>}{readErrors.detail&&<p role="alert">记录详情：{readErrors.detail}<button className="text-button" onClick={()=>void reload()}>重试</button></p>}<RunRecordOverview detail={current?{...current,input_note:current.input_note??bundles.find(b=>b.id===current.bundle_id)?.reason}:current} previewOnly={Boolean(displayRow&&!items.includes(displayRow))} failed={Boolean(readErrors.history||readErrors.detail)} loading={loadingHistory} onRefresh={()=>void reload()} nodeLabel={project.nodes.find(n=>n.id===initialNodeId)?.data.label||'当前节点'} rows={(displayRow&&!items.includes(displayRow)?[displayRow,...items]:items).filter(a=>a.node_id===initialNodeId&&!('scan_batch_id' in a))} selected={selected} onSelect={selectRecord}/> {!current&&matching&&displayRow&&<div className="run-detail-reserve" aria-hidden="true" style={{height:matching.detailHeight}}/>}{current&&<section ref={detailPanel} className="input-submit-card">{current.state!=='PREPARED'&&<details key={current.id} className="workbench-disclosure" onToggle={e=>setLogsFor(e.currentTarget.open?current.id:'')}><DisclosureLabel title="查看日志"/>{current.config.scheduler==='custom'?logsFor===current.id&&<CommandOutput key={current.id} attemptId={current.id} alias={current.config.alias} direct={direct} attemptState={current.state} onChanged={direct?refresh:()=>void reload()}/>:<div className="disclosure-body">{current.status_receipt?.stdout||current.status_receipt?.stderr?<><strong>最近调度核查回执</strong><pre className="input-text">{[current.status_receipt.stdout,current.status_receipt.stderr].filter(Boolean).join('\n')}</pre></>:<p>尚无调度日志回执。</p>}{onResults&&<button className="button" onClick={()=>onResults(current.node_id,current.id,'files')}>打开此记录的文件与日志</button>}</div>}</details>} {(direct||current.config.scheduler!=='custom')&&<CancelTask key={current.id} direct={direct} projectId={project.id} attemptId={current.id} state={current.state} onChanged={async()=>{await reload();}}/>}{current.note&&<p role="status">{current.note}</p>}{current.tracking_error&&current.tracking_error!==current.note&&<p role="status">{current.job_id?'显示上次核查状态 · ':''}{current.tracking_error}</p>}{onResults&&<button className="button" onClick={()=>onResults(current.node_id,current.id)}>查看结果与执行证据</button>}{!['PREPARED','COMPLETED','FAILED','CANCELLED'].includes(current.state)&&<button className="button" disabled={busy} onClick={()=>void refresh()}>核查原任务</button>}{!direct&&<TaskRecovery projectId={project.id} kind="attempts" targetId={current.id} onChanged={reload}/>} {!direct&&current.state==='UNKNOWN'&&<button className="button" onClick={async()=>{try{await requestJson('/api/execution/'+current.id+'/release-quota',{project_id:project.id});await reload();setStatus('本机额度占用已解除，原作业继续跟踪');}catch(e){setError(errorMessage(e));}}}>解除本机额度占用</button>}{current.state==='PREPARED'&&(direct?<button className="button" disabled={busy} onClick={()=>{setError('');setConfirmation(current);}}>继续确认启动</button>:<p>未提交的旧预览可查看；请选择输入重新准备后提交。</p>)}<details className="workbench-disclosure"><DisclosureLabel title="记录与维护"/><p>{current.remote_path_absolute||current.remote_path}</p><RemoteDeleteAttempt projectId={project.id} attemptId={current.id} state={current.state} onDeleted={()=>{chooseAttempt('');void reload();}} onOpenAttempt={selectRecord}/><a href={'/api/execution/'+current.id+'/technical-evidence'} download>下载技术证据</a><a href={'/api/execution/'+current.id+'/failure-snapshot'} download>下载失败快照</a><pre className="input-text">{current.script}</pre></details></section>}<details className="workbench-disclosure task-preparation" open={prepareOpen} onToggle={e=>{if(e.currentTarget.open!==prepareOpen)setPrepareChoice(e.currentTarget.open);}}><DisclosureLabel title="准备新任务"/>{['inputs','server','settings'].filter(key=>readErrors[key]).map(key=><p key={key} role="alert">{({inputs:'冻结输入',server:'服务器配置',settings:'默认设置'} as Record<string,string>)[key]}：{readErrors[key]}<button className="text-button" onClick={()=>void (key==='inputs'?loadInputs():loadServer())}>重试读取</button></p>)}<section className="input-submit-card"><h3>1 · 输入</h3><label>当前节点冻结输入<select value={bundle} disabled={busy} onChange={e=>chooseBundle(e.target.value)}><option value="">请选择</option>{bundles.filter(b=>b.node_id===initialNodeId&&isReviewableInput(b)).map(b=><option key={b.id} value={b.id}>{inputVersionLabel(b.id,b.reason)}</option>)}</select></label>{input&&<RunInputDifference project={project} nodeId={input.node_id} bundleId={input.id}/>} {input?.source_contract&&<p>{input.source_contract.mode==='new'?'新计算':Object.values(input.source_contract.roles).map(r=>r.file+' · '+r.alias).join('；')}</p>}{!bundle&&<p>请先在计算配置中审查并冻结输入。</p>}</section>
 <ServerPanel settings={settings} project={project} scopeId={initialNodeId||input?.node_id} program={input?.requires_ncl?'vasp_ncl':'vasp_std'} onTemplate={setTemplate} config={config} profiles={profiles} probe={probe} preview={null} node={node} cores={cores} reason={reason} busy={busy} canProbe={Boolean(bundle)} onConfigChange={changeConfig} onNodeChange={v=>{setNode(v);setConfirmation(null);}} onCoresChange={v=>{setCores(v);setConfirmation(null);}} onReasonChange={v=>{setReason(v);setConfirmation(null);}} onSaveAndProbe={()=>void detect()} onProbe={()=>void detect()} onPreview={()=>{}}/>
 <section className="input-submit-card"><h3>4 · 提交</h3><button className="button primary" data-action-check={checkKey} disabled={busy} onClick={()=>void prepare()}>{busy?'正在核对…':'准备提交'}</button></section></details><Feedback message={status} error={error} busy={busy} label="正在核对本次请求…"/>
 {confirmation&&<SubmitConfirmation title={commandMode(confirmation.config)==='direct'?'确认直接运行':undefined} busy={busy} script={confirmation.script} onClose={()=>setConfirmation(null)} onConfirm={()=>void submit()}><p>输入版本 · {inputVersionLabel(confirmation.bundle_id,bundles.find(b=>b.id===confirmation.bundle_id)?.reason??confirmation.input_note)}</p><p>{confirmation.config.alias} · {confirmation.remote_path_absolute||confirmation.remote_path}</p>{commandMode(confirmation.config)==='direct'&&<p>在本次任务目录启动后台进程。</p>}<p hidden={confirmation.config.scheduler==='custom'}>{confirmation.node} · {confirmation.profile?.ppn||confirmation.profile?.ntasks||cores||'全部可用'} 核</p>{confirmation.profile?.gpus&&<p>整节点 GPU {confirmation.profile.gpus} 张 · 脚本所需 {confirmation.profile.required_gpus} 张 · MPI {confirmation.profile.mpi_ranks} 进程</p>}<ProgramAdaptation change={confirmation.template_snapshot?.program_adaptation} diff={confirmation.template_snapshot?.program_diff}/><p>输入：{input?.reason||'已选冻结输入'}</p>{Object.values(confirmation.source_contract?.roles||{}).map(r=><p key={r.file}>{r.file}：{r.alias} · {items.find(a=>a.id===r.attempt_id)?attemptLabel(items.find(a=>a.id===r.attempt_id)!,project):'历史计算来源（本机记录缺失）'}</p>)}{Array.from(new Set(Object.values(confirmation.source_contract?.roles||{}).flatMap(r=>r.differences||[]))).map(d=><p key={d} className="structure-warning">{d}</p>)}{error&&<p role="alert">{error}</p>}</SubmitConfirmation>}
 <details className="run-history"><summary>最近运行与历史</summary>{cursor&&<button className="button" onClick={async()=>{try{const page=await requestJson(listURL+'&cursor='+encodeURIComponent(cursor));setItems(old=>[...old,...page.items.filter((a:Attempt)=>!old.some(b=>b.id===a.id))]);setCursor(page.next_cursor);}catch(e){setError(errorMessage(e));}}}>再加载 50 条记录</button>}<HistoryList rows={items.filter(a=>a.node_id===initialNodeId&&!('scan_batch_id' in a))} limit={3} context={project.id+initialNodeId} render={a=><div className="history-row" key={a.id}><button className="text-button" title={attemptLabel(a,undefined,true)} onClick={()=>selectRecord(a.id)}>{attemptLabel(a)}</button><DeleteRecord kind="attempts" id={a.id} state={a.state} label="计算记录" onChanged={async()=>{if(selected===a.id)setSelected('');await reload();}}/></div>}/><RecycleBin kind="attempts" projectId={project.id} onChanged={reload}/></details>

 </section>;
}
