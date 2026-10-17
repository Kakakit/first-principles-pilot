import {taskSummary} from './taskSummary';
import TaskRecovery from './TaskRecovery';
import CancelTask from './CancelTask';
import ProgramAdaptation from './ProgramAdaptation';
import {useSubmissionDraft} from './useSubmissionDraft';
import {sameCapacityTarget} from './executionUi';
import CandidateNodePool from './CandidateNodePool';
import {poolNodes,fullNodeCandidates} from './candidatePool';
import {chainResourceIssues} from './chainResources';
import SubmitConfirmation from './SubmitConfirmation';
import HistoryList from './HistoryList';
import {DeleteRecord,RecycleBin} from './RecordCleanup';
import {useEffect,useMemo,useRef,useState} from 'react';
import {chainStepPhase,latestChain,pollInitialChain} from './chainDispatch';
import type {InitialDispatch} from './chainDispatch';
import {errorMessage,requestJson} from './apiClient';
import RemoteProgress from './RemoteProgress';
import ServerPanel from './ServerPanel';
import {statusLabel} from './statusLabels';
import type {Project} from './model';
import FlowCanvas from './FlowCanvas';
import {chainNodes} from './workflowUi';
import type {RegisteredProfile,ServerConfig,ServerPreview,ServerProbe} from './executionUi';

type StepPreview={node_id:string;label:string;stage:string;parent_id:string;errors:string[];warnings:string[];output_diff:Record<string,{before:string|null;after:string}>;nelm:string;nsw:string|null;requires_ncl:boolean;input_hashes:Record<string,string>;pending_sources:{node_id:string;file:string;sha256:string}[];conditional_retry?:{source_files:string[];incar_diff:Record<string,string>}|null;path_policy?:{algorithm:string;symprec:number;line_points:string}|null};
type ReuseCandidate={attempt_id:string;job_id?:string|null;alias:string;state:string;eligible:boolean;reason:string;differences:{field:string;before:string|null;after:string|null}[]};
type ReuseSource={node_id:string;label:string;required_files:string[];selected_attempt_id:string|null;reason:string;candidates:ReuseCandidate[]};
type Preview={sources:ReuseSource[];reuse_sources:Record<string,string>;ready:boolean;steps:StepPreview[];node_ids:string[];mismatches:{parent_id:string;node_id:string;field:string;before:string|null;after:string|null}[];mismatch_hash:string;definition_hash:string;max_jobs:number};
type ChainStep=StepPreview&{state:string;bundle_id:string;attempts:string[];attempt_id?:string|null;attempt_state?:string;job_id?:string|null;final_attempt_id?:string|null;convergence?:{status:string;reason?:string}|null;blocker?:string|null;waiting_reason?:string|null;waiting_kind?:string;actual_band_path?:{source_attempt_id:string;structure_hash:string};attempt_evidence?:{scheduler_state?:string;program_done?:boolean;electronic_converged?:boolean;scientific_validation?:string};source_snapshot?:{source_attempt_id:string;files:Record<string,{sha256?:string;size_bytes:number;mtime?:string}>}};
type ChainRun={id:string;created_at?:string;revision?:number;initial_dispatch?:InitialDispatch;project_id:string;state:string;plan_hash:string;mismatch_hash:string;max_jobs:number;source_alias?:string;verification_mode?:string;reuse_bindings?:Record<string,{attempt_id:string;verification_mode:string}>;steps:ChainStep[];mismatches:Preview['mismatches'];authorization?:{config:ServerConfig;resources:Record<string,{node:string;cores:number|null;reason:string}>};last_worker_error?:string};
const api=requestJson;

export default function ChainPanel({project,view='run',onReview,onConfigure}:{project:Project;initialNodeId:string|null;view?:'config'|'run';onReview?:()=>void;onConfigure?:()=>void}){
 const possible=useMemo(()=>chainNodes(project),[project]);
 const [confirmOpen,setConfirmOpen]=useState(false);
 const [draftDifferences,setDraftDifferences]=useState<{node_id:string;label:string;field:string;frozen:unknown;draft:unknown}[]>([]);
 const mounted=useRef(true),pendingAuthorization=useRef(false);
 const [dispatchRunId,setDispatchRunId]=useState<string|null>(null);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const [ids,setIds]=useSubmissionDraft<string[]>('chain:'+project.id+':selection',possible.map(item=>item.id));
 const [reuseSources,setReuseSources]=useState<Record<string,string>>({}),[verificationMode,setVerificationMode]=useState('lightweight');
 const [reason,setReason]=useState(''),[preview,setPreview]=useState<Preview|null>(null);
 const [run,setRun]=useSubmissionDraft<ChainRun|null>('chain:'+project.id+':run',null),[history,setHistory]=useState<ChainRun[]>([]);
 const activeRunId=useRef(run?.id);activeRunId.current=run?.id;
 const draftKey='chain:'+project.id;
 const [projectStamp,setProjectStamp]=useSubmissionDraft(draftKey+':stamp',project.updatedAt||'');
 const [config,setConfig]=useSubmissionDraft<ServerConfig|null>(draftKey+':config',null),[profiles,setProfiles]=useState<RegisteredProfile[]>([]);
 const [probe,setProbe]=useSubmissionDraft<ServerProbe|null>(draftKey+':probe',null),[serverPreview,setServerPreview]=useState<ServerPreview|null>(null);
 const [pool,setPool]=useSubmissionDraft<string[]>(draftKey+':pool',[]);
 const [scripts,setScripts]=useState<Record<string,Record<string,ServerPreview>>>({});
 const [confirmDiff,setConfirmDiff]=useState(false),[busy,setBusy]=useState(false),[busyStep,setBusyStep]=useState('');
 const [error,setError]=useState(''),[notice,setNotice]=useState('');
 const executionIds=chainNodes(project,ids).map(node=>node.id);
 const selectedIds=run?run.steps.map(step=>step.node_id):executionIds;
 const reuseIds=Object.keys(run?.reuse_bindings||preview?.reuse_sources||{});
 const changeSelection=(next:string[])=>{
  if(busy||run)return;
  setIds(chainNodes(project,next).map(node=>node.id));setReuseSources({});setPreview(null);setScripts({});setConfirmDiff(false);setConfirmOpen(false);setError('');setNotice('');
 };
 const selectionNodes=project.nodes.map(node=>({...node,selected:selectedIds.includes(node.id),data:{...node.data,
  chainSelection:selectedIds.includes(node.id)?'execute':reuseIds.includes(node.id)?'reuse':possible.some(item=>item.id===node.id)?'unselected':'context',
  chainLocked:busy||Boolean(run),toggleChain:possible.some(item=>item.id===node.id)?()=>changeSelection(ids.includes(node.id)?ids.filter(id=>id!==node.id):[...ids,node.id]):undefined}}));
 const newSelection=()=>{
  if(busy)return;
  if(run)setIds(chainNodes(project,run.steps.map(step=>step.node_id)).map(node=>node.id));
  setDispatchRunId(null);setRun(null);setPreview(null);setReuseSources({});setScripts({});setConfirmDiff(false);setConfirmOpen(false);setError('');setNotice('已返回新的计算链草稿，原计划保留在历史中。');
 };
 const acceptRun=(value:ChainRun)=>{if(!mounted.current||activeRunId.current!==value.id)return;setRun(old=>latestChain(old,value));setHistory(old=>old.map(item=>item.id===value.id?latestChain(item,value)!:item));};
 const readRun=(id:string):Promise<ChainRun>=>api('/api/chains/'+encodeURIComponent(id)+'?project_id='+encodeURIComponent(project.id));
 const load=async()=>{const [server,result]=await Promise.all([api('/api/server'),api('/api/chains?project_id='+encodeURIComponent(project.id))]);if(!mounted.current)return;setConfig(old=>old||server.config);setProfiles(server.registered_profiles||[]);setHistory(result.runs||[]);if(run&&!result.runs.some((item:ChainRun)=>item.id===run.id)){setRun(null);setDispatchRunId(null);return;}if(run){const value=await readRun(run.id);acceptRun(value);if(value.initial_dispatch?.state==='IN_PROGRESS')setDispatchRunId(value.id);}};
 useEffect(()=>{
  if(!dispatchRunId||dispatchRunId!==run?.id)return;
  return pollInitialChain(()=>readRun(dispatchRunId),value=>{acceptRun(value);if(value.state==='FROZEN'&&!pendingAuthorization.current)setDispatchRunId(null);},()=>setDispatchRunId(null));
 },[dispatchRunId,project.id,run?.id]);
 useEffect(()=>{void load().catch(e=>setError(errorMessage(e)));},[project.id]);
 useEffect(()=>{if(projectStamp===(project.updatedAt||''))return;setDispatchRunId(null);setPreview(null);setScripts({});setProjectStamp(project.updatedAt||'');},[project.updatedAt]);
 useEffect(()=>{let active=true;setDraftDifferences([]);if(run)void api('/api/chains/'+run.id+'/draft-differences',{project}).then(r=>{if(active)setDraftDifferences(r.differences);}).catch(()=>{});return()=>{active=false;};},[run?.id,project]);
 const call=async(label:string,fn:()=>Promise<void>)=>{setBusy(true);setBusyStep(label);setError('');try{await fn();}catch(e){setError(errorMessage(e));}finally{setBusy(false);setBusyStep('');}};
 const inspect=(choices=reuseSources)=>call('正在检查计算链与逐节点输入',async()=>{const value=await api('/api/chains/preview',{project,node_ids:executionIds,source_alias:config?.alias,reuse_sources:choices,verification_mode:verificationMode});setPreview(value);setReuseSources(value.reuse_sources);setRun(null);setConfirmDiff(false);});
 const freeze=()=>call('正在冻结计算链输入与依赖规则',async()=>{if(!preview)return;const value=await api('/api/chains/freeze',{project,node_ids:executionIds,reason,expected_definition_hash:preview.definition_hash,source_alias:config?.alias,reuse_sources:preview.reuse_sources,verification_mode:verificationMode});setRun(value);setHistory(old=>[value,...old]);setScripts({});setNotice('计算链已冻结。选择候选计算节点后，整链预览脚本并授权。');});
 const probeServer=(save=false)=>call('正在诊断目标主机',async()=>{if(!config)return;const value=save?(await api('/api/server',{config},'PUT')).config||config:config;setConfig(value);const report=await api('/api/server/probe',{config:value});setProbe(report);setPool(old=>old.filter(name=>poolNodes(report,value.alias).some(n=>n.NODE===name)));setScripts({});setServerPreview(null);});
 const nodes=poolNodes(probe,config?.alias);
 const candidates=pool.length?fullNodeCandidates(nodes,pool.filter(name=>nodes.some(n=>n.NODE===name&&Number.isInteger(Number(n.CPUS_TOTAL))&&Number(n.CPUS_TOTAL)>0))):[];
 const previewResources=()=>call('正在批量预览候选节点与各阶段脚本',async()=>{if(!run||!config||!probe||!pool.length)return;for(const candidate of candidates){if(!candidate.cores)throw new Error(candidate.node+' 的总核数未知');}const resources=Object.fromEntries(run.steps.map(step=>[step.node_id,{vasp_executable:step.requires_ncl?'vasp_ncl':'vasp_std',candidates}]));const value=await api('/api/chains/'+run.id+'/resources-preview',{project_id:project.id,config,resources});setScripts(value.previews);});
 const authorize=()=>call('正在准备并提交首批节点',async()=>{
  if(!run||!config)return;
  const id=run.id;
  const resources=Object.fromEntries(run.steps.map(step=>[step.node_id,{vasp_executable:step.requires_ncl?'vasp_ncl':'vasp_std',candidates}]));
  pendingAuthorization.current=true;setDispatchRunId(id);
  try{
   const value:ChainRun=await api('/api/chains/'+id+'/authorize',{project_id:project.id,plan_hash:run.plan_hash,mismatch_hash:run.mismatch_hash,config,resources,approved_previews:scripts});
   if(!mounted.current)return;
   acceptRun(value);
   if(value.initial_dispatch?.state!=='IN_PROGRESS')setDispatchRunId(null);
   const submitted=value.steps.filter(step=>step.job_id).length;
   setNotice(submitted?'首批已提交 '+submitted+' 个作业；后续由 worker 按原周期推进。':'授权已保存；请查看逐节点等待或核查原因，后续由 worker 按原周期处理。');
   window.dispatchEvent(new Event('fpilot:binding-changed'));
  }catch(e){
   try{
    const value=await readRun(id);
    if(!mounted.current)return;
    acceptRun(value);
    if(value.initial_dispatch?.state!=='IN_PROGRESS')setDispatchRunId(null);
    if(value.authorization){setNotice('授权已保存，已读取原计算链状态；请查看作业号及等待或核查原因。');return;}
   }catch{ /* Keep reading the same local record when the API reconnects. */ }
   throw e;
  }finally{pendingAuthorization.current=false;}
 });
 const action=(name:'pause'|'resume')=>call(name==='pause'?'正在暂停未派发任务':'正在继续计算链',async()=>{if(!run)return;const value=await api('/api/chains/'+run.id+'/'+name,{project_id:project.id});acceptRun(await readRun(value.id));});
 const refresh=()=>call('正在读取计算链状态',async()=>{if(!run)return;const value=await api('/api/chains/'+run.id+'/refresh',{project_id:project.id});acceptRun(await readRun(value.id));});
 const shown=run?.steps||preview?.steps||[];
 const readyToPrepare=Boolean(run?.state==='FROZEN'&&config&&(!Object.keys(run.reuse_bindings||{}).length||config.alias===run.source_alias)&&probe&&pool.length&&pool.length===candidates.length);
 const prepareSubmission=()=>call('正在审查计算链与候选节点脚本',async()=>{if(!run||!config)return;const resources=Object.fromEntries(run.steps.map(step=>[step.node_id,{vasp_executable:step.requires_ncl?'vasp_ncl':'vasp_std',candidates}]));const value=await api('/api/chains/'+run.id+'/resources-preview',{project_id:project.id,config,resources});setScripts(value.previews);const issues=chainResourceIssues(run.steps,pool,value.previews);if(issues.length)throw new Error('候选节点检查未通过，请查看下方逐节点原因并调整本次脚本或资源。');setConfirmOpen(true);});
 return <section className="input-workspace chain-panel"><h2>{view==='config'?'配置计算链':'提交计算链'}</h2>{view==='config'&&<p>选择需要执行的节点，并在冻结前审阅来源和续算预算。</p>}
  <div hidden={view!=='config'}>  <section className="input-submit-card"><h3>1 · 选择与预览</h3><p>点选画布节点表示本次重新执行。取消选择上游后，保留连线，并从同项目、同主机的已收敛历史结果中选择来源。</p><label>本次执行主机<select disabled={busy||Boolean(run)} value={config?.alias||''} onChange={e=>{setConfig(old=>{const profile=profiles.find(item=>item.alias===e.target.value);return old&&profile?{...old,...Object.fromEntries(Object.entries(profile).filter(([key])=>['alias','queue','mpi_executable','vasp_executable','environment_script','module','launcher','runtime_environment','pmi_library','pmi_type'].includes(key))),scheduler:profile.scheduler.toLowerCase()}:old;});setReuseSources({});setPreview(null);setRun(null);setProbe(null);setScripts({});}}>{Array.from(new Set([config?.alias,...profiles.map(item=>item.alias)])).filter(Boolean).map(alias=><option key={alias} value={alias}>{alias}</option>)}</select></label><label>大文件核验<select disabled={busy||Boolean(run)} value={verificationMode} onChange={e=>{setVerificationMode(e.target.value);setPreview(null);setRun(null);}}><option value="lightweight">轻量：来源、非空文件、大小与修改时间</option><option value="full">完整内容：扫描 SHA-256（较慢）</option></select></label><div className="chain-selection-toolbar"><button type="button" className="text-button" disabled={busy||Boolean(run)} onClick={()=>changeSelection(possible.map(node=>node.id))}>全选计算节点</button><button type="button" className="text-button" disabled={busy||Boolean(run)} onClick={()=>changeSelection([])}>清空选择</button><span role="status">本次执行 {selectedIds.length} 个节点</span>{run&&<><span>已冻结，执行范围已锁定</span><button type="button" className="button" disabled={busy} onClick={newSelection}>重新选择节点</button></>}</div><div className="chain-selection-canvas"><FlowCanvas project={project} nodes={selectionNodes} mode="chain-select" onNodeClick={id=>{if(!possible.some(node=>node.id===id))return;changeSelection(ids.includes(id)?ids.filter(nodeId=>nodeId!==id):[...ids,id]);}}/></div><label>本次输入和依赖选择理由<textarea disabled={busy||Boolean(run)} value={reason} maxLength={2000} onChange={e=>setReason(e.target.value)}/></label><button className="button primary" disabled={busy||Boolean(run)||!executionIds.length} onClick={()=>void inspect()}>预览整条计算链</button>{preview&&<p>{preview.node_ids.length} 个节点 · 最多 {preview.max_jobs} 个作业 · {preview.ready?'输入检查通过':preview.sources.some(source=>source.reason)?'请补选合格历史来源':'有输入错误'}</p>}{preview?.sources.map(source=><div key={source.node_id} className="chain-reuse"><h4>复用上游：{source.label}</h4><p>需要 {source.required_files.join('、')}。{source.reason||'已绑定具体历史结果；不会重新提交此节点。'}</p><label>历史结果<select value={source.selected_attempt_id||''} onChange={e=>{const choices={...reuseSources};if(e.target.value)choices[source.node_id]=e.target.value;else delete choices[source.node_id];setReuseSources(choices);void inspect(choices);}} disabled={busy||Boolean(run)}><option value="">请选择合格来源</option>{source.candidates.map(item=><option key={item.attempt_id} value={item.attempt_id} disabled={!item.eligible}>{source.label} · {item.job_id||statusLabel(item.state)} · {item.alias} · {item.eligible?(item.differences.length?'输入有差异，选择即确认复用':'输入一致'):item.reason}</option>)}</select></label>{source.candidates.filter(item=>item.eligible&&item.differences.length>0).map(item=><details key={item.attempt_id}><summary>{source.label} · {item.job_id||statusLabel(item.state)} 的历史输入与当前草稿差异</summary>{item.differences.map((diff,i)=><p key={i}>{diff.field}：历史 {diff.before||'未设置'} → 当前 {diff.after||'未设置'}</p>)}</details>)}</div>)}{preview?.ready&&!run&&<button className="button primary" disabled={busy} onClick={freeze}>冻结计算链</button>}</section>
{run?.state==='FROZEN'&&<button className="button primary" onClick={onReview}>继续：运行审阅</button>}</div>
  {draftDifferences.length>0&&<div className="handoff-warning" role="status"><strong>当前草稿与冻结计划不同</strong>{draftDifferences.map((d,i)=><p key={i}>{d.label} · {d.field}：{typeof d.frozen==='object'?'旧来源合同':String(d.frozen??'未设置')} → {typeof d.draft==='object'?'当前来源合同':String(d.draft??'未设置')}</p>)}<p>原计划和授权保留。返回新草稿重新预览、冻结，才能应用当前参数。</p></div>}{shown.length>0&&<section hidden={view==='run'&&run?.state==='FROZEN'} className="input-submit-card"><h3>冻结节点与续算预算</h3><div className="table-scroll"><table><thead><tr><th>节点</th><th>依赖与状态</th><th>步数上限</th><th>有效输入</th><th>attempt</th></tr></thead><tbody>{shown.map(s=><tr key={s.node_id}><td>{s.label}<br/>{s.stage}</td><td>{'state' in s?chainStepPhase(s as ChainStep,run?.initial_dispatch?.state==='IN_PROGRESS'):'预览'}<br/>{project.nodes.find(n=>n.id===s.parent_id)?.data.label||'结构'}{(s as ChainStep).blocker&&<p role="alert">{(s as ChainStep).blocker}</p>}{(s as ChainStep).waiting_reason&&<p role="status">{(s as ChainStep).waiting_kind==='CONNECTION'?'等待连接：':''}{(s as ChainStep).waiting_reason}</p>}{(s as ChainStep).attempt_evidence&&<p>调度：{(s as ChainStep).attempt_evidence?.scheduler_state||'未确认'} · 程序结束：{(s as ChainStep).attempt_evidence?.program_done?'是':'否'} · 科学审核：{(s as ChainStep).attempt_evidence?.scientific_validation||'未做'}</p>}</td><td>NELM {s.nelm}<br/>NSW {s.nsw||'—'}<br/>原始 1 次{s.conditional_retry&&<>；条件续算最多 1 次<br/>来源 {s.conditional_retry.source_files.join('、')}<br/>ISTART={s.conditional_retry.incar_diff.ISTART}，ICHARG={s.conditional_retry.incar_diff.ICHARG}</>}</td><td><details><summary>INCAR 差异、输入 hash 与提示</summary>{Object.entries(s.output_diff).map(([tag,d])=><p key={tag}>{tag}: {d.before??'未设置'} → {d.after}</p>)}{Object.entries(s.input_hashes).map(([name,hash])=><p key={name}>{name} {hash}</p>)}{s.pending_sources.map((item,i)=><p key={'pending'+i}>上游 {item.node_id} 的 {item.file}：{item.sha256}</p>)}{(s as ChainStep).source_snapshot&&<p>来源 attempt 已绑定的实际运行；{Object.entries((s as ChainStep).source_snapshot?.files||{}).map(([name,info])=>name+' '+(info.sha256||'轻量元数据核验')).join(' · ')}</p>}{s.path_policy&&<p>能带路径：{s.path_policy.algorithm}；symprec {s.path_policy.symprec}；每段 {s.path_policy.line_points} 点；SCF 实际结构待绑定</p>}{s.warnings.map((w,i)=><p key={i}>{w}</p>)}{s.errors.map((w,i)=><p className="structure-error" key={i}>{w}</p>)}</details></td><td>{(s as ChainStep).attempts?.map(id=><p key={id}>已登记的运行</p>)}{(s as ChainStep).convergence&&<p>数值：{(s as ChainStep).convergence?.status}</p>}</td></tr>)}</tbody></table></div></section>}
  <div hidden={view!=='run'} className="simple-submit"><section className="input-submit-card"><h3>1 · 输入</h3><label>冻结计算链<select value={run?.id||''} disabled={busy} onChange={e=>{setDispatchRunId(null);setRun(history.find(h=>h.id===e.target.value)||null);setScripts({});}}><option value="">请选择已冻结计划</option>{history.map(h=><option key={h.id} value={h.id}>{h.steps.map(s=>s.label).join(' → ')} · {statusLabel(h.state)}</option>)}</select></label><button className="text-button" onClick={onConfigure}>计算链定义与冻结</button>{run&&<><p>{run.steps.length} 个节点 · 最多 {run.max_jobs} 个作业</p>{Object.entries(run.reuse_bindings||{}).map(([id])=><p key={id}>复用 {project.nodes.find(n=>n.id===id)?.data.label||'上游结果'}</p>)}</>}</section>
  {run?.state==='FROZEN'&&<><ServerPanel programs={Array.from(new Set(run.steps.map(step=>step.requires_ncl?'vasp_ncl':'vasp_std')))} program={run.steps[0]?.requires_ncl?'vasp_ncl':'vasp_std'} purpose="chain" project={project} scopeId={run.steps[0]?.node_id} config={config} profiles={profiles} probe={probe} preview={serverPreview} node="" cores="" reason="" busy={busy} canProbe status={notice} error={error} progressLabel={busyStep} onConfigChange={value=>{setConfig(value);if(!sameCapacityTarget(config,value)){setProbe(null);setPool([]);}setScripts({});setServerPreview(null);setConfirmOpen(false);}} onNodeChange={()=>{}} onCoresChange={()=>{}} onReasonChange={()=>{}} onSaveAndProbe={()=>void probeServer(true)} onProbe={()=>void probeServer()} onPreview={()=>{}} resourceExtra={probe&&<CandidateNodePool queued={config?.resource_policy==='scheduler_queue'} nodes={nodes} selected={pool} busy={busy} onChange={next=>{setPool(next);setScripts({});}}/>}/>
  <section className="input-submit-card"><h3>4 · 提交</h3>{run&&Object.keys(scripts).length>0&&<div className="chain-resource-issues">{chainResourceIssues(run.steps,pool,scripts).map(issue=><div key={issue.nodeId+issue.node} role="alert"><strong>{issue.label} · {issue.node}</strong>{issue.reasons.map((text,i)=><p key={i}>{text}</p>)}</div>)}</div>}<button className="button primary" disabled={!readyToPrepare||busy} onClick={()=>void prepareSubmission()}>准备提交</button></section></>}
  {run&&run.state!=='FROZEN'&&<section className="input-submit-card run-history"><p>{statusLabel(run.state)}</p><p role="status">{taskSummary(run.steps)}</p>{run.initial_dispatch?.state==='IN_PROGRESS'&&<p role="status">正在准备并提交首批节点</p>}<ul>{run.steps.map(step=><li key={step.node_id}><strong>{step.label}</strong> · {chainStepPhase(step,run.initial_dispatch?.state==='IN_PROGRESS')}{step.job_id&&<span> · 作业号 {step.job_id}</span>}{step.attempt_id&&<CancelTask key={step.attempt_id} projectId={project.id} attemptId={step.attempt_id} state={step.attempt_state||step.state} label={step.label} onChanged={async()=>acceptRun(await readRun(run.id))}/>} {step.waiting_reason&&<p>{step.waiting_reason}</p>}{step.blocker&&<p role="alert">{step.blocker}</p>}<TaskRecovery projectId={project.id} kind="chains" targetId={run.id} itemId={step.node_id} onChanged={async()=>acceptRun(await readRun(run.id))}/></li>)}</ul>{run.state==='ACTIVE'&&<button className="button" disabled={busy} onClick={()=>void action('pause')}>暂停未派发节点</button>}<TaskRecovery projectId={project.id} kind="chains" targetId={run.id} remaining onChanged={async()=>acceptRun(await readRun(run.id))}/><button className="button" disabled={busy||Boolean(dispatchRunId)} onClick={refresh}>刷新运行状态</button>{run.last_worker_error&&<p role="alert">{run.last_worker_error}</p>}<details><summary>冻结计划与来源证据</summary><p>{run.plan_hash}</p><p>{run.mismatch_hash}</p>{run.mismatches.map((m,i)=><p key={i}>{m.field}：{m.before||'未设置'} → {m.after||'未设置'}</p>)}{Object.values(run.reuse_bindings||{}).map(source=><p key={source.attempt_id}>{source.attempt_id} · {source.verification_mode}</p>)}</details></section>}
  </div>{confirmOpen&&run&&<SubmitConfirmation title="确认计算链授权" busy={busy} onClose={()=>setConfirmOpen(false)} onConfirm={()=>{setConfirmOpen(false);void authorize();}}><p>{config?.alias} · 最多 {run.max_jobs} 个作业</p>{run.mismatches.map((m,i)=><p key={i}>{m.field}：{m.before||'未设置'} → {m.after||'未设置'}</p>)}{run.steps.map(step=><details key={step.node_id} open><summary>{step.label}</summary>{pool.map(name=><div key={name}><h4>{name}</h4>{scripts[step.node_id]?.[name]?.script_warnings?.map((warning,i)=><p key={i}>{warning}</p>)}{scripts[step.node_id]?.[name]?.script_diff&&<details><summary>原稿与本次单节点脚本的差异</summary><pre className="input-text">{scripts[step.node_id]?.[name]?.script_diff}</pre></details>}<ProgramAdaptation change={scripts[step.node_id]?.[name]?.program_adaptation} diff={scripts[step.node_id]?.[name]?.program_diff}/><pre className="input-text">{scripts[step.node_id]?.[name]?.script}</pre></div>)}</details>)}</SubmitConfirmation>}
  <section className="input-submit-card"><details><summary>历史计算链</summary><HistoryList rows={history} kind="chains" limit={3} pinnedId={run?.id} context={project.id} render={item=><div className="history-row" key={item.id}><button className="text-button" disabled={busy} onClick={()=>{setDispatchRunId(null);setRun(item);setPreview(null);void readRun(item.id).then(acceptRun).catch(e=>setError(errorMessage(e)));}}>{new Date(item.created_at||'').toLocaleString()} · {statusLabel(item.state)} · {item.steps.length} 节点</button><DeleteRecord kind="chains" id={item.id} state={item.state} label={'计算链 · '+new Date(item.created_at||'').toLocaleString()} onChanged={async()=>{if(run?.id===item.id){setRun(null);setScripts({});}setHistory((await api('/api/chains?project_id='+encodeURIComponent(project.id))).runs);}}/></div>}/><RecycleBin key={project.id} kind="chains" projectId={project.id} title="计算链回收区" onChanged={async()=>setHistory((await api('/api/chains?project_id='+encodeURIComponent(project.id))).runs)}/></details></section>
  {busy&&<RemoteProgress label={busyStep}/>}{error&&<p role="alert" className="structure-error">{error}</p>}{notice&&<p role="status">{notice}</p>}
 </section>;
}
