import {useSubmissionDraft} from './useSubmissionDraft';
import {sameCapacityTarget} from './executionUi';
import WannierScanPanel from './WannierScanPanel';
import type {WindowDraft,WindowAdoption} from './windowScan';
import CandidateNodePool from './CandidateNodePool';
import {poolNodes,fullNodeCandidates,capacityWait} from './candidatePool';
import {openSettings} from './submissionDefaults';
import SourcePanel from './SourcePanel';
import type {SourceDraft} from './SourcePanel';
import SubmitConfirmation from './SubmitConfirmation';
import HistoryList from './HistoryList';
import {DeleteRecord,RecycleBin} from './RecordCleanup';
import {errorMessage,requestJson} from './apiClient';
import RemoteProgress from './RemoteProgress';
import {statusLabel} from './statusLabels';
import {useEffect,useMemo,useState} from 'react';
import ServerPanel from './ServerPanel';
import type {ServerConfig,RegisteredProfile,ServerProbe,ServerPreview} from './executionUi';
import type {Project,Step} from './model';
import {definitions,managedTags} from './parameters';

type Dimension={kind:'incar'|'u'|'j'|'kmesh';tag?:string;element?:string;start?:string;end?:string;step?:string;meshes?:number[][]};
type Case={id?:string;index:number;parameters:{kind:string;key:string;value:string}[];input_hashes:Record<string,string>;input_diff?:Record<string,string>;errors:string[];warnings:string[];state?:string;attempt_id?:string|null;retry_count?:number};
type Batch={requires_ncl?:boolean;waiting_reason?:string;id:string;scan_node_id:string;base_node_id:string;stage:string;state:string;case_table_hash:string;cases:Case[];authorization?:{config:ServerConfig;node?:string;reason:string;candidates?:{node:string;cores:number}[];max_parallel?:number;preview_profile?:Record<string,unknown>};blocker?:string};
type Preview={case_count:number;ready:boolean;cases:Case[];definition_hash:string};
const api=requestJson;
const empty=():Dimension=>({kind:'incar',tag:'ENCUT',start:'400',end:'600',step:'50'});
const stateLabel=(state?:string)=>state?statusLabel(state):'预览';

function VaspScanPanel({project,scanNode,onDraft,view,onReview,onConfigure,onSource}:{onSource?:(value:SourceDraft)=>void;view?:'config'|'run';onReview?:()=>void;onConfigure?:()=>void;project:Project;scanNode:Step;onDraft:(dimensions:Dimension[],reason:string)=>void}){
 const parentId=project.edges.find(e=>e.target===scanNode.id)?.source||'';
 const parent=project.nodes.find(n=>n.id===parentId);
 const saved=scanNode.data.scanDraft as {dimensions?:Dimension[];reason?:string}|undefined;
 const [confirmOpen,setConfirmOpen]=useState(false);
 const [dimensions,setDimensions]=useState<Dimension[]>(saved?.dimensions?.length?saved.dimensions:[empty()]);
 const [reason,setReason]=useState(saved?.reason||''),[preview,setPreview]=useState<Preview|null>(null),[batch,setBatch]=useSubmissionDraft<Batch|null>('scan:'+project.id+':'+scanNode.id+':batch',null),[batches,setBatches]=useState<Batch[]>([]);
 const draftKey='scan:'+project.id+':'+scanNode.id;
 const [config,setConfig]=useSubmissionDraft<ServerConfig|null>(draftKey+':config',null),[profiles,setProfiles]=useState<RegisteredProfile[]>([]),[probe,setProbe]=useSubmissionDraft<ServerProbe|null>(draftKey+':probe',null),[resource,setResource]=useState<ServerPreview|null>(null);
 const [pool,setPool]=useSubmissionDraft<string[]>(draftKey+':pool',[]),[maxParallel,setMaxParallel]=useSubmissionDraft(draftKey+':maxParallel',1),[scripts,setScripts]=useState<Record<string,ServerPreview>>({});
 const [limits,setLimits]=useState<{max_inflight_total:number;max_inflight_per_host:number;max_cores_per_host:number|null}|null>(null);
 const [busy,setBusy]=useState(false),[busyStep,setBusyStep]=useState(''),[error,setError]=useState(''),[status,setStatus]=useState('');
 const numeric=useMemo(()=>definitions.filter(d=>['number','integer','ediffg'].includes(d.type)&&!managedTags.has(d.tag)&&(!d.applies_to||d.applies_to.includes(parent?.data.kind||''))),[parent?.data.kind]);
 const load=async()=>{const [server,history,control]=await Promise.all([api('/api/server'),api(`/api/scans?project_id=${encodeURIComponent(project.id)}`),api('/api/execution-control')]);setLimits(control.settings);setConfig(old=>old||server.config);setProfiles(server.registered_profiles||[]);setBatches(history.batches.filter((b:Batch)=>b.scan_node_id===scanNode.id));setBatch(old=>old?history.batches.find((b:Batch)=>b.id===old.id)||null:null);};
 useEffect(()=>{void load().catch(e=>setError(e.message));},[project.id,scanNode.id]);
 const safe=(next:Dimension[])=>next.every(d=>!d.meshes||d.meshes.every(mesh=>mesh.every(Number.isFinite)));
 const persist=(next:Dimension[],text=reason)=>{setDimensions(next);if(safe(next))onDraft(next,text);setPreview(null);setBatch(null);};
 const mutate=(index:number,patch:Partial<Dimension>)=>persist(dimensions.map((d,i)=>i===index?{...d,...patch}:d));
 const switchKind=(index:number,kind:Dimension['kind'])=>persist(dimensions.map((d,i)=>i!==index?d:kind==='kmesh'?{kind,meshes:[[4,4,4],[6,6,6]]}:kind==='incar'?empty():{kind,element:'',start:'0',end:'2',step:'0.5'}));
 const call=async(fn:()=>Promise<void>,step='正在处理参数迭代请求')=>{setBusy(true);setBusyStep(step);setError('');try{await fn();}catch(e){setError(errorMessage(e));}finally{setBusy(false);setBusyStep('');}};
 const sourceProject={...project,nodes:project.nodes.map(n=>n.id===parentId?{...n,data:{...n.data,sourceDraft:scanNode.data.sourceDraft||n.data.sourceDraft}}:n)};
 const request={project:sourceProject,scan_node_id:scanNode.id,base_node_id:parentId,dimensions,reason};
 const inspect=()=>call(async()=>{setPreview(await api('/api/scans/preview',request));setBatch(null);},'正在生成并检查全部 case 输入');
 const freeze=()=>call(async()=>{const result=await api('/api/scans/freeze',{...request,expected_definition_hash:preview?.definition_hash});setBatch(result);setBatches(old=>[result,...old]);setStatus('已冻结全部 case；请审核服务器、脚本与来源后授权。');},'正在冻结 case 表与输入哈希');
 const probeServer=(save=false)=>call(async()=>{if(!config)return;const effective=save?(await api('/api/server',{config},'PUT')).config||config:config;setConfig(effective);const report=await api('/api/server/probe',{config:effective});setProbe(report);setResource(null);setPool(old=>old.filter(name=>poolNodes(report,effective.alias).some(n=>n.NODE===name)));setScripts({});},'正在连接主机并诊断运行时与节点资源');
 const nodes=poolNodes(probe,config?.alias);
 const candidates=fullNodeCandidates(nodes,pool.filter(name=>nodes.some(n=>n.NODE===name)));
 const prepareSubmission=()=>call(async()=>{if(!batch||!config||!candidates.length)return;const value=await api(`/api/scans/${batch.id}/resources-preview`,{project_id:project.id,config,candidates});for(const c of candidates){const v=value.previews[c.node];if(!v||!(v.resource_ready||capacityWait(v))||!v.script||!v.profile)throw new Error(c.node+'：'+(v?.blockers.join('；')||'资源或运行时不可用'));}setScripts(value.previews);setConfirmOpen(true);},'正在审查候选节点与扫描批次脚本');
 const authorize=()=>call(async()=>{if(!batch||!config)return;const result=await api(`/api/scans/${batch.id}/authorize`,{project_id:project.id,case_table_hash:batch.case_table_hash,config,candidates,max_parallel:maxParallel,approved_previews:scripts});setBatch(result);setBatches(old=>old.map(b=>b.id===result.id?result:b));setStatus('整批授权已保存；worker 在候选池中按同时运行上限派发，繁忙时等待。');},'正在保存候选节点池与整批授权');
 const action=(verb:'pause'|'resume')=>call(async()=>{if(!batch)return;setBatch(await api(`/api/scans/${batch.id}/${verb}`,{project_id:project.id}));});
 const retry=(caseId:string)=>call(async()=>{if(!batch)return;setBatch(await api(`/api/scans/${batch.id}/cases/${caseId}/retry`,{project_id:project.id}));});
 const refresh=()=>call(async()=>{if(!batch)return;setBatch(await api(`/api/scans/${batch.id}?project_id=${encodeURIComponent(project.id)}`));});
 const authorizationBlockers=[...(!config?['选择服务器']:[]),...(!pool.length?['选择候选计算节点']:[]),...(!probe?['检测节点']:[]),...(!Number.isInteger(maxParallel)||maxParallel<1||maxParallel>pool.length?['填写有效的同时运行上限']:[])];
 const currentCases=batch?.cases||preview?.cases||[];
 return <section className="input-workspace scan-panel"><h2>{view==='run'?'参数迭代运行审阅':'参数迭代配置'}{scanNode.data.label!=='参数迭代'&&` · ${scanNode.data.label}`}</h2><p>基础计算：{parent?.data.label||'请先连接 relax 或 SCF 节点'}。每个 case 使用独立冻结输入与 attempt；当前版本最多 4 维、64 个 case。</p>
  <div hidden={view==='run'}>{parent&&onSource&&<SourcePanel allowSelf={false} project={sourceProject} nodeId={parentId} scopeId={scanNode.id} onChange={onSource}/>}<section className="input-submit-card"><h3>1 · 定义与预览</h3>{dimensions.map((d,i)=><div className="scan-dimension" key={i}><label>维度 {i+1}<select value={d.kind} onChange={e=>switchKind(i,e.target.value as Dimension['kind'])}><option value="incar">数值 INCAR</option><option value="u">元素 U</option><option value="j">元素 J</option><option value="kmesh">规则 k 网格</option></select></label>{d.kind==='incar'?<label>参数<select value={d.tag||''} onChange={e=>mutate(i,{tag:e.target.value})}>{numeric.map(tag=><option key={tag.tag} value={tag.tag}>{tag.tag} · {tag.label}</option>)}</select></label>:d.kind==='u'||d.kind==='j'?<label>元素<input value={d.element||''} placeholder="例如 Cr" onChange={e=>mutate(i,{element:e.target.value})}/></label>:null}{d.kind==='kmesh'?<label>网格列表，每行三个整数<textarea value={(d.meshes||[]).map(m=>m.join(' ')).join('\n')} onChange={e=>mutate(i,{meshes:e.target.value.split('\n').filter(Boolean).map(line=>line.trim().split(/\s+/).map(Number))})}/></label>:<>{(['start','end','step'] as const).map(key=><label key={key}>{key}<input value={d[key]||''} onChange={e=>mutate(i,{[key]:e.target.value})}/></label>)}</>}<button className="button" onClick={()=>persist(dimensions.filter((_,index)=>index!==i))} disabled={dimensions.length===1}>移除</button></div>)}<button className="button" disabled={dimensions.length>=4} onClick={()=>persist([...dimensions,empty()])}>添加维度</button><label>输入选择理由<textarea value={reason} onChange={e=>{setReason(e.target.value);if(safe(dimensions))onDraft(dimensions,e.target.value);setBatch(null);}}/></label><button className="button primary" disabled={busy||!parent} onClick={inspect}>预览全部组合与输入</button>{preview&&<p>共 {preview.case_count} 个 case · {preview.ready?'输入检查通过':'有输入错误'}</p>}{preview?.ready&&<button className="button primary" disabled={busy||!reason.trim()} onClick={freeze}>冻结 case 表</button>}</section>
  </div>
  {currentCases.length>0&&<section hidden={view==='run'&&batch?.state==='FROZEN'} className="input-submit-card"><h3>Case 表 · {currentCases.length} 项</h3><div className="atoms-scroll"><table><thead><tr><th>Case</th><th>扫描值</th><th>状态</th><th>输入来源</th><th>操作</th></tr></thead><tbody>{currentCases.map(c=><tr key={c.id||c.index}><td>{c.index+1}</td><td>{c.parameters.map(p=>`${p.key}=${p.value}`).join(' · ')}</td><td>{stateLabel(c.state)}{c.errors.length>0&&<strong> · {c.errors.join('；')}</strong>}</td><td><details><summary>INCAR/KPOINTS 差异、hash 与警告</summary>{Object.entries(c.input_diff||{}).map(([name,diff])=><pre key={name}>{diff}</pre>)}{Object.entries(c.input_hashes).map(([name,hash])=><p key={name}>{name} {hash}</p>)}{c.warnings.map((w,j)=><p key={j}>{w}</p>)}</details></td><td>{c.attempt_id&&<span>attempt {c.attempt_id.slice(0,12)}</span>}{c.state==='FAILED'&&c.id&&c.retry_count===0&&<button className="button" onClick={()=>void retry(c.id!)}>重试一次</button>}</td></tr>)}</tbody></table></div></section>}
  <div hidden={view==='run'}>{batch&&onReview&&<div className="configuration-next"><p>已冻结 {batch.cases.length} 个 case；下一步核对来源、资源和授权范围。</p><button className="button primary" onClick={onReview}>继续：整批运行审阅</button></div>}</div>
  <div hidden={view==='config'} className="simple-submit">
  {onConfigure&&<div className="run-history"><button className="button" onClick={onConfigure}>返回参数迭代配置</button></div>}
  <section className="input-submit-card"><h3>1 · 输入</h3><label>冻结扫描批次<select value={batch?.id||''} onChange={e=>{setBatch(batches.find(b=>b.id===e.target.value)||null);setResource(null);setScripts({});}}><option value="">请选择已冻结批次</option>{batches.map(b=><option key={b.id} value={b.id}>{b.stage} · {b.cases.length} 项 · {stateLabel(b.state)}</option>)}</select></label>{!batch&&<p>在计算配置中冻结 case 表后选择此处的批次。</p>}{batch?.blocker&&<p role="alert">已暂停派发：{batch.blocker}</p>}</section>
  {batch?.state==='FROZEN'&&<><ServerPanel project={project} scopeId={scanNode.id} purpose="batch" program={batch.requires_ncl?'vasp_ncl':'vasp_std'} progressLabel={busyStep} config={config} profiles={profiles} probe={probe} preview={resource} node="" cores="" reason="" busy={busy} canProbe status={status} error={error} onConfigChange={next=>{setConfig(next);if(!sameCapacityTarget(config,next)){setProbe(null);setPool([]);}setScripts({});setResource(null);setConfirmOpen(false);}} onNodeChange={()=>{}} onCoresChange={()=>{}} onReasonChange={()=>{}} onSaveAndProbe={()=>void probeServer(true)} onProbe={()=>void probeServer()} onPreview={()=>{}} resourceExtra={probe&&<><CandidateNodePool nodes={nodes} selected={pool} busy={busy} onChange={next=>{setPool(next);setMaxParallel(next.length||1);setScripts({});}}/><label className="batch-parallel">本批同时运行上限<input aria-label="本批同时运行上限" type="number" min="1" max={pool.length||1} value={maxParallel} disabled={busy} onChange={e=>{setMaxParallel(Number(e.target.value));setScripts({});}}/></label><p className="submit-help">已选 {pool.length} 个节点；本批最多 {maxParallel} 项同时在途。{limits&&`当前全局总量 ${limits.max_inflight_total} 项，每主机 ${limits.max_inflight_per_host} 项${limits.max_cores_per_host?`，核数上限 ${limits.max_cores_per_host}`:''}；实际执行取各项上限允许的数量。`}</p><button className="text-button" onClick={()=>openSettings('operations')}>调整全局在途额度</button></>}/>
  <section className="input-submit-card"><h3>4 · 提交</h3>{authorizationBlockers.length>0&&<p className="submit-help" role="status">尚需完成：{authorizationBlockers.join('；')}</p>}<button className="button primary" disabled={busy||authorizationBlockers.length>0} onClick={()=>void prepareSubmission()}>准备提交</button></section></>}
  {batch&&<div className="run-history">{batch.waiting_reason&&<p role="status">等待资源：{batch.waiting_reason}</p>}{batch.authorization&&<p>{batch.authorization.config.alias} · 候选节点 {batch.authorization.candidates?.map(c=>c.node).join('、')||batch.authorization.node} · 本批同时运行上限 {batch.authorization.max_parallel||1}</p>}{batch.state==='ACTIVE'&&<button className="button" onClick={()=>void action('pause')}>暂停未派发 case</button>}{['PAUSED','BLOCKED'].includes(batch.state)&&<button className="button" onClick={()=>void action('resume')}>继续批次</button>}<button className="button" onClick={refresh}>刷新批次状态</button></div>}
  </div>
  <section className="input-submit-card"><details><summary>历史批次</summary><HistoryList rows={batches} kind="scans" limit={3} pinnedId={batch?.id} context={project.id+scanNode.id} render={b=><div className="history-row" key={b.id}><button className="text-button" onClick={()=>{setBatch(b);setPreview(null);}}>{b.id.slice(0,12)} · {b.stage} · {stateLabel(b.state)} · {b.cases.length} case</button><DeleteRecord kind="scans" id={b.id} state={b.state} label={'批次 '+b.id.slice(0,12)} onChanged={async()=>{if(batch?.id===b.id)setBatch(null);await load();}}/></div>}/><RecycleBin key={project.id+scanNode.id} kind="scans" projectId={project.id} title="批次回收区" onChanged={load}/></details></section>
  {confirmOpen&&batch&&<SubmitConfirmation title="确认扫描批次授权" busy={busy} onClose={()=>setConfirmOpen(false)} onConfirm={()=>{setConfirmOpen(false);void authorize();}}><p>{config?.alias} · {batch.cases.length} 个 case · {pool.length} 个候选节点 · 本批最多 {maxParallel} 项同时在途</p>{pool.map(name=><details key={name} open><summary>{name} · {candidates.find(c=>c.node===name)?.cores} 核</summary><pre className="input-text">{scripts[name]?.script}</pre></details>)}</SubmitConfirmation>}
  {busy&&<RemoteProgress label={busyStep}/>}{error&&<p role="alert" className="structure-error">{error} <button className="text-button" onClick={()=>void load().catch(e=>setError(errorMessage(e)))}>重试读取页面数据</button></p>}{status&&<p role="status">{status}</p>}
 </section>;
}

export default function ScanPanel(props:Parameters<typeof VaspScanPanel>[0]&{onWindowChange?:(draft:WindowDraft)=>void;onWindowAdopt?:(value:WindowAdoption)=>void}){
 const parent=props.project.nodes.find(n=>n.id===props.project.edges.find(e=>e.target===props.scanNode.id)?.source);
 return parent?.data.kind==='wannier90'?<WannierScanPanel {...props} parent={parent} onChange={props.onWindowChange} onAdopt={props.onWindowAdopt}/>:<VaspScanPanel {...props}/>;
}
