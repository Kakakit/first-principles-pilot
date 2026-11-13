import FileCollection from './FileCollection';
import ManifestExport from './ManifestExport';
import ContinueScan from './ContinueScan';
import type {ContinuationCreated} from './scanContinuation';
import type {VisitView} from './navigationHistory';
import RunRecordSelect,{recordLabel} from './RunRecordSelect';
import {checkedStructureSnapshot} from './structureSnapshot';
import type {StructureSnapshot} from './structureSnapshot';
import {attemptLabel} from './humanLabels';
import ConnectionStatus from './ConnectionStatus';
import {useVisibleRefresh} from './useVisibleRefresh';
import CommandOutput from './CommandOutput';
import {calculationKind} from './scanNodes';
import CancelTask from './CancelTask';
import {commandMode,directProcessLabel} from './executionUi';
import HistoryList from './HistoryList';
import {DeleteRecord,RecycleBin} from './RecordCleanup';
import {lazy,Suspense,useEffect,useRef,useState} from 'react';
import {Download,FileText,RefreshCw,Layers} from 'lucide-react';
import RemoteDeleteAttempt from './RemoteDeleteAttempt';
import ArtifactDelete from './ArtifactDelete';
import AttemptNotes from './AttemptNotes';
import ProblemNotesButton from './ProblemNotes';
import ResultSummary from './ResultSummary';
import RemoteProgress from './RemoteProgress';
import {requestJson,errorMessage} from './apiClient';
import {statusLabel} from './statusLabels';
import type {Project} from './model';
import type {ResultAttempt,ResultSummary as Summary} from './resultWorkspace';
const HandoffResults=lazy(()=>import('./HandoffResults'));
const CompareResults=lazy(()=>import('./CompareResults'));
const InteractivePlot=lazy(()=>import('./InteractivePlot'));
const WannierCompare=lazy(()=>import('./WannierCompare'));
const AnalysisDatasetRegister=lazy(()=>import('./AnalysisDatasetRegister'));
const ExtensionPanel=lazy(()=>import('./ExtensionPanel'));
type Artifact={path:string;name:string;size_bytes:number;modified_at:string;category:string;previewable:boolean};
type Preview={path:string;text:string;partial:boolean};
type Section='summary'|'files'|'post'|'evidence';
const size=(n:number)=>n<1024?`${n} B`:n<1024*1024?`${(n/1024).toFixed(1)} KiB`:`${(n/1024/1024).toFixed(1)} MiB`;
const time=(value?:string)=>value?new Date(value).toLocaleString():'时间未记录';
const categories:Record<string,string>={input:'输入',output:'日志与结果',checkpoint:'续算文件',evidence:'执行证据',other:'其他'};

export default function NodeResults({project,initialNodeId,initialAttemptId,subpage,onSubpage,viewState,viewKey,onViewState,onAttemptChange,onConfigure,onRun,onAddStructure,onContinuationCreated}:{onContinuationCreated?:(result:ContinuationCreated)=>void;project:Project;initialNodeId?:string|null;initialAttemptId?:string;subpage?:string;onSubpage?:(page:string)=>void;viewState?:VisitView;viewKey?:string;onViewState?:(view:VisitView)=>void;onAttemptChange?:(nodeId:string,id:string,reason:'user'|'default')=>void;onConfigure?:(id:string)=>void;onRun?:(id:string,attemptId?:string)=>void;onAddStructure?:(snapshot:StructureSnapshot)=>void}){
 const resultNodes=project.nodes.filter(n=>['vasp','wannier90','scan'].includes(n.data.executionMode)&&n.data.kind!=='structure');
 const [localMode,setMode]=useState<'node'|'archive'|'compare'>('node'),[nodeId,setNodeId]=useState(resultNodes.some(n=>n.id===initialNodeId)?initialNodeId!:resultNodes[0]?.id||'');
 const [detail,setDetail]=useState<ResultAttempt|null>(null),[cursor,setCursor]=useState<string|null>(null);
 const [attempts,setAttempts]=useState<ResultAttempt[]>([]),[attemptId,setAttemptId]=useState(''),[localSection,setSection]=useState<Section>('summary'),[postSeen,setPostSeen]=useState(false);
 const mode=subpage?(subpage==='compare'?'compare':subpage==='handoff'?'archive':'node'):localMode;
 const section:Section=subpage&&['summary','files','post','evidence'].includes(subpage)?subpage as Section:localSection;
 const [files,setFiles]=useState<Artifact[]>([]),[preview,setPreview]=useState<Preview|null>(null),[analyses,setAnalyses]=useState<any[]>([]),[plots,setPlots]=useState<any[]>([]);
 const [summary,setSummary]=useState<Summary|null>(null),[filesAt,setFilesAt]=useState(''),[filter,setFilter]=useState(''),[category,setCategory]=useState('all');
 const [busyKeys,setBusy]=useState<string[]>([]),[error,setError]=useState<{message:string;label:string;retry:()=>void}|null>(null);
 const [verification,setVerification]=useState<any>(null);
 const [filesIncomplete,setFilesIncomplete]=useState(false);
 const [deleteFile,setDeleteFile]=useState<{context:string;path:string}|null>(null);
 const context=project.id+':'+nodeId+':'+attemptId,nodeContext=project.id+':'+nodeId;
 const postVisible=section==='post';
 const plotKind=calculationKind(project.nodes.find(node=>node.id===nodeId),project);
 useEffect(()=>setDeleteFile(null),[context]);
 const mounted=useRef(true);useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const active=useRef(context),activeNode=useRef(nodeContext);active.current=context;activeNode.current=nodeContext;
 const busy=busyKeys.filter(key=>key.startsWith(context+'|')||key===nodeContext+'|records').map(key=>key.split('|').at(-1)!);
 const nodeRequest=useRef(0),pendingAttempt=useRef(initialAttemptId||'');
 const inFlight=useRef(new Map<string,symbol>());
 const listed=attempts.find(a=>a.id===attemptId);
 const current:ResultAttempt|undefined=detail?.id===attemptId?{...detail,input_note:listed?.input_note??detail.input_note}:undefined;
 const supported=current?.path_schema==='fpilot.remote-attempt-path';
 const work=async(name:string,label:string,fn:()=>Promise<void>)=>{
  const signature=name==='records'?nodeContext:context,key=signature+'|'+name;
  if(inFlight.current.has(key))return;const token=Symbol(key);inFlight.current.set(key,token);setBusy([...inFlight.current.keys()]);setError(null);
  try{await fn();}catch(e){if(signature===(name==='records'?activeNode.current:active.current))setError({message:errorMessage(e),label:'重试'+label,retry:()=>void work(name,label,fn)});}finally{if(inFlight.current.get(key)===token){inFlight.current.delete(key);setBusy([...inFlight.current.keys()]);}}
 };
 const loadAttempts=async(target=pendingAttempt.current||attemptId)=>{
  if(nodeContext!==activeNode.current)return;const ticket=++nodeRequest.current;if(!nodeId){setAttempts([]);setAttemptId('');return;}
  const data=await requestJson(`/api/projects/${encodeURIComponent(project.id)}/nodes/${encodeURIComponent(nodeId)}/attempts?view=summary&limit=50`);
  if(ticket!==nodeRequest.current||nodeContext!==activeNode.current)return;
  if(!Array.isArray(data.attempts))throw new Error('运行记录格式异常，可重试读取原节点');
  setAttempts(data.attempts);setCursor(data.next_cursor||null);
  if(target){setAttemptId(target);if(data.attempts.some((a:ResultAttempt)=>a.id===target))setAttemptId(target);else{const row=await requestJson(`/api/execution/${target}?project_id=${encodeURIComponent(project.id)}`);if(ticket!==nodeRequest.current||nodeContext!==activeNode.current)return;if((row.owner_node_id||row.execution_scope||row.node_id)!==nodeId)throw new Error('指定运行记录不属于当前节点，请重新选择。');setAttempts(old=>[row,...old]);setAttemptId(target);}}
  else setAttemptId(data.attempts[0]?.id||'');
  pendingAttempt.current='';
 };
 useEffect(()=>{setAttempts([]);setAttemptId('');void work('records','读取运行记录',()=>loadAttempts(pendingAttempt.current));return()=>{nodeRequest.current++;inFlight.current.delete(nodeContext+'|records');};},[project.id,nodeId]);
 useEffect(()=>{const controller=new AbortController();if(!attemptId){setDetail(null);return;}void requestJson(`/api/execution/${attemptId}?project_id=${encodeURIComponent(project.id)}`,undefined,undefined,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setDetail(value);}).catch(e=>{if(!controller.signal.aborted)setError({message:errorMessage(e),label:'刷新运行记录',retry:()=>void loadAttempts(attemptId)});});return()=>controller.abort();},[project.id,attemptId,attempts]);
 useEffect(()=>{if(attemptId&&(listed?.owner_node_id||listed?.execution_scope||listed?.node_id)===nodeId)onAttemptChange?.(nodeId,attemptId,'default');},[nodeId,attemptId,listed]);
 useEffect(()=>{if(initialAttemptId&&initialAttemptId!==attemptId&&pendingAttempt.current!==initialAttemptId){pendingAttempt.current=initialAttemptId;void work('records','读取运行记录',()=>loadAttempts(initialAttemptId));}},[initialAttemptId]);
 const loadSummary=async()=>{const signature=context;const data=await requestJson(`/api/results/${attemptId}/summary?project_id=${encodeURIComponent(project.id)}`);if(signature===active.current)setSummary(data);};
 useEffect(()=>{setFiles([]);setFilesAt('');setPreview(null);setSummary(null);setVerification(null);setAnalyses([]);setPlots([]);setPostSeen(false);if(!onSubpage)setSection('summary');setError(null);if(attemptId)void work('summary','读取本机摘要',loadSummary);},[attemptId]);
 useEffect(()=>{setFilter(viewState?.filter||'');setCategory(viewState?.category||'all');},[viewKey,attemptId]);
 const fileList=async(force=false,automatic=false)=>{
  const signature=context;let data=await requestJson(`/api/execution/${attemptId}/artifacts${force?'/refresh':''}`,force?{}:undefined);
  if(signature!==active.current)return;setFiles(data.files);setFilesAt(data.refreshed_at||'');setFilesIncomplete(Boolean(data.truncated));
  if(automatic&&data.needs_refresh&&!data.refreshing&&data.host_state==='READY'){
   data=await requestJson(`/api/execution/${attemptId}/artifacts/refresh`,{});
   if(signature===active.current){setFiles(data.files);setFilesAt(data.refreshed_at||'');setFilesIncomplete(Boolean(data.truncated));}
  }
 };
 const {element:metadataElement}=useVisibleRefresh(mode==='node'&&section==='files'&&Boolean(attemptId),context,()=>work('files','读取文件列表',()=>fileList(false,true)));
 const readMetrics=()=>work('metrics','读取结果指标',async()=>{await requestJson('/api/results/compare',{project_id:project.id,attempt_ids:[attemptId]});await loadSummary();});
 const refreshStatus=()=>work('status','核对原任务状态',async()=>{await requestJson(`/api/execution/${attemptId}/refresh`,{});await loadAttempts(attemptId);await loadSummary();});
 const canExtract=(path:string)=>Boolean(onAddStructure&&current&&['POSCAR','CONTCAR'].includes(path.split('/').at(-1)||''));
 const extractStructure=(path:string)=>work('structure','提取任务结构',async()=>{const signature=context;const value=await checkedStructureSnapshot(await requestJson(`/api/execution/${attemptId}/structure-snapshot`,{project_id:project.id,path}));if(mounted.current&&signature===active.current)onAddStructure?.(value);});
 const structureButton=(path:string)=><button className="text-button" disabled={busy.includes('structure')} onClick={()=>void extractStructure(path)}>{busy.includes('structure')?'正在提取结构…':'添加为结构节点'}</button>;
 const openFile=(path:string)=>work('preview','预览当前文件',async()=>{const signature=context;const data=await requestJson(`/api/execution/${attemptId}/artifact-preview`,{path,mode:'tail',max_bytes:262144});if(signature===active.current)setPreview(data);});
 const loadPost=async()=>{const signature=context;const [history,local]=await Promise.all([requestJson(`/api/execution/${attemptId}/analyses`),requestJson(`/api/local-plots?project_id=${encodeURIComponent(project.id)}&attempt_id=${attemptId}`)]);if(signature===active.current){setAnalyses(history.analyses);setPlots(local.plots);}};
 const selectSection=(next:Section)=>{if(onSubpage)onSubpage(next);else setSection(next);setError(null);};
 useEffect(()=>{
  if(!attemptId||mode!=='node')return;
  if(section==='files')void work('files','读取文件列表',()=>fileList());
  if(section==='post'){setPostSeen(true);void work('post','读取已保存分析',loadPost);}
 },[section,attemptId,mode]);
 const chooseRecord=(id:string,targetNode=nodeId)=>{
  nodeRequest.current++;
  onAttemptChange?.(targetNode,id,'user');
  if(targetNode===nodeId)setAttemptId(id);else {pendingAttempt.current=id;setNodeId(targetNode);}
 };
 const openChild=(id:string)=>void work('source','读取来源记录',async()=>{const child:ResultAttempt=await requestJson(`/api/execution/${id}?project_id=${encodeURIComponent(project.id)}`);if(context!==active.current)return;const childOwner=child?.owner_node_id as string||child?.execution_scope||child?.node_id;if(!childOwner||!resultNodes.some(n=>n.id===childOwner))throw new Error('来源运行记录或流程节点已不存在：'+id);chooseRecord(id,childOwner);});
 const verify=()=>work('verification','完整核验来源',async()=>{const signature=context;const data=await requestJson(`/api/execution/${attemptId}/verify-source-files`,{});if(signature===active.current)setVerification(data);});
 const navigation=<nav className="result-mode" aria-label="结果视图">{([['node','所选结果'],['compare','多个结果比较'],['archive','数据集与历史交接']] as const).map(([key,title])=><button key={key} className="button" aria-pressed={mode===key} onClick={()=>onSubpage?onSubpage(key==='node'?'summary':key==='archive'?'handoff':key):setMode(key)}>{title}</button>)}</nav>;
 if(mode!=='node')return <section data-navigation-scroll className="node-results">{navigation}<Suspense fallback={<RemoteProgress label="正在载入结果视图"/>}>{mode==='compare'?<CompareResults project={project}/>:<HandoffResults projectId={project.id}/>}</Suspense></section>;
 const shownFiles=files.filter(f=>(category==='all'||f.category===category)&&f.path.toLowerCase().includes(filter.toLowerCase()));
 return <section ref={metadataElement} data-navigation-scroll className="node-results"><div className="page-heading"><div><h2>结果与后处理</h2></div>{navigation}</div>
 <div className="result-context"><label>流程节点<select aria-label="结果流程节点" value={nodeId} onChange={e=>chooseRecord('',e.target.value)}>{resultNodes.map(n=><option value={n.id} key={n.id}>{n.data.label}</option>)}</select></label>{project.nodes.find(n=>n.id===nodeId)?.data.kind==='scan'&&current&&<><label>批次<select aria-label="结果迭代批次" value={String(current.scan_batch_id||'')} onChange={e=>chooseRecord(attempts.find(a=>a.scan_batch_id===e.target.value)?.id||'')}><option value="">选择批次</option>{[...new Map(attempts.filter(a=>a.scan_batch_id).map(a=>[String(a.scan_batch_id),a])).values()].map(a=><option key={String(a.scan_batch_id)} value={String(a.scan_batch_id)}>{String(a.batch_reason||'参数测试')} · {time(String(a.batch_created_at||''))}</option>)}</select></label><label>Case<select aria-label="结果迭代 case" value={String(current.scan_case_id||'')} onChange={e=>chooseRecord(attempts.find(a=>a.scan_batch_id===current.scan_batch_id&&a.scan_case_id===e.target.value)?.id||'')} >{[...new Map(attempts.filter(a=>a.scan_batch_id===current.scan_batch_id).map(a=>[String(a.scan_case_id),a])).values()].sort((a,b)=>Number(a.case_index)-Number(b.case_index)).map(a=><option key={String(a.scan_case_id)} value={String(a.scan_case_id)}>Case {Number(a.case_index)+1} · {String(a.parameter_summary||'旧参数记录')}</option>)}</select></label></>}<RunRecordSelect ariaLabel="结果运行记录" rows={attempts.filter(a=>project.nodes.find(n=>n.id===nodeId)?.data.kind!=='scan'||!current||a.scan_batch_id===current.scan_batch_id&&a.scan_case_id===current.scan_case_id)} selected={attemptId} onSelect={chooseRecord}/></div>
 {current?.input_note&&Array.from(current.input_note).length>48&&!current.parameter_summary&&<details className="result-full-note"><summary>完整冻结备注</summary><p>{current.input_note}</p></details>}
 <details data-visit-detail="result-record-tools" className="result-record-tools"><summary>记录与管理 · {attempts.length} 条</summary> {cursor&&<button className="button" onClick={()=>void work('records','读取更多运行记录',async()=>{const data=await requestJson(`/api/projects/${encodeURIComponent(project.id)}/nodes/${encodeURIComponent(nodeId)}/attempts?view=summary&limit=50&cursor=${encodeURIComponent(cursor)}`);if(nodeContext!==activeNode.current)return;setAttempts(old=>[...old,...data.attempts.filter((a:ResultAttempt)=>!old.some(b=>b.id===a.id))]);setCursor(data.next_cursor);})}>再加载 50 条运行记录</button>}<details className="result-record-history"><summary>管理当前节点运行记录 · {attempts.length} 条</summary><HistoryList rows={attempts} pinnedId={attemptId} context={nodeContext} render={a=><div className="history-row" key={a.id}><button className="text-button" title={attemptLabel(a,undefined,true)} aria-pressed={a.id===attemptId} onClick={()=>chooseRecord(a.id)}>{recordLabel(a)}{a.id===attemptId?' · 当前查看':''}</button><DeleteRecord kind="attempts" id={a.id} state={a.state} label={a.job_id||'未提交'} onChanged={()=>loadAttempts(a.id===attemptId?'':attemptId)}/></div>}/></details><RecycleBin key={project.id+nodeId} kind="attempts" projectId={project.id} title="计算记录回收区" onChanged={()=>loadAttempts(attemptId)}/></details>
 <div className="result-feedback">
 {error&&<div className="action-error" role="alert"><p>{error.message}</p><button className="button" onClick={error.retry}>{error.label}</button></div>}
 {busy.length>0&&<RemoteProgress label={{records:'正在读取本机运行记录',summary:'正在读取本机结果摘要',files:'正在读取远端文件列表',metrics:'正在提取所选结果指标',status:'正在核对原运行回执与结束证据',preview:'正在读取文件有限预览',post:'正在读取已保存分析',source:'正在定位冻结来源',structure:'正在提取任务结构',verification:'正在完整核验来源文件'}[busy[0]]||'正在读取结果'}/>}</div>
 {current&&<><ConnectionStatus alias={current.config?.alias||''}/>{current.tracking_error&&<p role="status">状态采集未更新，保留上次记录：{statusLabel(current.state)} · {current.state_checked_at?time(String(current.state_checked_at)):'尚无成功采集时间'}。连接恢复后继续核查。</p>}</>}{!current?<div className="empty-workspace"><Layers size={32}/><h3>{initialAttemptId&&!current?'原运行记录暂不可用':'当前节点尚未选定运行记录'}</h3><p>先配置、审查并冻结输入，再进入运行审阅。结果读取失败时可原位重试。</p>{nodeId&&onConfigure&&<button className="button primary" onClick={()=>onConfigure(nodeId)}>打开此节点配置</button>}</div>:<>
 {!supported&&<p className="handoff-warning">此记录目录不受支持；仅展示本机证据。</p>}
 <nav className="result-sections" aria-label="所选结果内容">{([['summary','结果摘要'],['files','文件与日志'],['post','绘图与后处理'],['evidence','来源与执行证据']] as const).map(([id,label])=><button key={id} className={section===id?'active':''} aria-current={section===id?'page':undefined} disabled={!supported&&['files','post'].includes(id)} onClick={()=>selectSection(id)}>{label}</button>)}</nav>
 <AttemptNotes key={current.id} id={current.id}/>
 {section==='summary'&&(summary?.input.stage||plotKind)==='relax'&&typeof current.scan_batch_id==='string'&&onContinuationCreated&&<section className="workspace-card"><h3>下一步</h3><ContinueScan key={current.scan_batch_id+current.id} project={project} batchId={current.scan_batch_id} initialAttemptId={current.id} onCreated={onContinuationCreated}/></section>}
 {section==='summary'&&<ResultSummary attempt={current} data={summary} busy={busy.includes('metrics')} onRead={()=>void readMetrics()} onSection={selectSection} onConfigure={()=>onConfigure?.(nodeId)} onRun={()=>onRun?.(nodeId,attemptId)} onOpenSource={openChild}/>}
 {section==='files'&&current.config?.scheduler==='custom'&&current.state!=='PREPARED'&&<CommandOutput key={current.id} attemptId={current.id} alias={current.config.alias||''} direct={commandMode(current.config)==='direct'} attemptState={current.state} onChanged={commandMode(current.config)==='direct'?refreshStatus:undefined}/>}
 <ArtifactDelete key={context} projectId={project.id} attemptId={attemptId} path={deleteFile?.context===context?deleteFile.path:null} onClose={()=>setDeleteFile(null)} onDeleted={async path=>{const signature=context;setFiles(items=>items.filter(item=>item.path!==path));setPreview(value=>value?.path===path?null:value);if(signature===active.current)await fileList(true);}}/>{section==='files'&&<section className="workspace-card"><div className="section-action-heading"><div><h3>文件与日志</h3><FileCollection key={'collect'+current.id} projectId={project.id} kind="attempt" id={current.id}/><ManifestExport key={current.id} projectId={project.id} kind="attempt" id={current.id}/><p>{filesAt?"上次更新："+time(filesAt)+(filesIncomplete?" · 列表未完整读取":""):"文件列表尚未更新"}</p></div><button className="button" disabled={busy.includes('files')} onClick={()=>void work('files','刷新文件列表',()=>fileList(true))}><RefreshCw size={15}/>刷新文件列表</button></div><div className="filter-bar"><label>文件类别<select value={category} onChange={e=>{setCategory(e.target.value);onViewState?.({category:e.target.value});}}><option value="all">全部类别</option>{Object.entries(categories).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><label>搜索文件<input value={filter} onChange={e=>{setFilter(e.target.value);onViewState?.({filter:e.target.value});}} placeholder="例如 OUTCAR 或 vasp.err"/></label><span>{shownFiles.length} / {files.length} 个文件</span></div><div className="atoms-scroll" data-visit-scroll="result-files"><table><thead><tr><th>类别</th><th>文件</th><th>大小</th><th>修改时间</th><th>操作</th></tr></thead><tbody>{shownFiles.map(f=><tr key={f.path}><td>{categories[f.category]||f.category}</td><td>{f.path}</td><td>{size(f.size_bytes)}</td><td>{time(f.modified_at)}</td><td>{canExtract(f.path)&&structureButton(f.path)}{f.previewable&&<button className="text-button" disabled={busy.includes('preview')} onClick={()=>void openFile(f.path)}>预览</button>}{f.size_bytes<=10*1024*1024?<a className="text-button" href={`/api/execution/${attemptId}/artifact-download?path=${encodeURIComponent(f.path)}`}><Download size={14}/>下载</a>:<span className="muted">保留远端</span>} <button className="button danger" aria-label={`删除远端文件 · ${f.path}`} onClick={()=>setDeleteFile({context,path:f.path})}>删除</button></td></tr>)}</tbody></table></div>{!shownFiles.length&&<p className="empty-note">{filesAt?(filesIncomplete?"列表未完整读取，不能据此判断文件缺失。":"当前筛选没有文件。") :"文件列表尚未更新。"}</p>}{preview&&<div className="file-preview"><h4>{preview.path}{preview.partial?' · 有限预览':''}</h4>{!preview.partial&&canExtract(preview.path)&&structureButton(preview.path)}<pre className="input-text" data-problem-log="true">{preview.text}</pre><p className="kmesh-reference">选中需要记录的日志文字，再点击下方按钮。预览可能只包含文件末尾，位置按预览记录。</p><ProblemNotesButton key={attemptId+preview.path} id={attemptId} excerpt={()=>{const selection=window.getSelection();const container=selection?.anchorNode?.parentElement?.closest('[data-problem-log]');const text=selection?.toString()||'';if(!text.trim()||!container||!selection?.focusNode||!container.contains(selection.focusNode))throw new Error('请先在当前日志预览中选中需要记录的文字');const range=selection.getRangeAt(0),prefix=range.cloneRange();prefix.selectNodeContents(container);prefix.setEnd(range.startContainer,range.startOffset);const offset=prefix.toString().length;return {file:preview.path,location:(preview.partial?'文件末尾有限预览':'完整预览')+(offset>=0?' · 预览第 '+(preview.text.slice(0,offset).split('\n').length)+' 行起':''),text};}}/></div>}</section>}
 {postSeen&&<div style={{display:postVisible?'block':'none'}}><Suspense fallback={<RemoteProgress label="正在载入绘图工具"/>}><div className="extension-grid">{postVisible&&(plotKind==='bands'||plotKind==='dos')&&<InteractivePlot key={context} project={project} draftNodeId={nodeId} initialBandId={plotKind==='bands'?attemptId:''} initialDosId={plotKind==='dos'?attemptId:''}/>} {current.kind==='wannier90'&&<WannierCompare project={project} attemptId={attemptId} showResults={postVisible}/>}<ExtensionPanel key={attemptId} source={{kind:'attempt',attempt_id:attemptId}} projectId={project.id} contextVersion={filesAt}/>{postVisible&&(plots.length>0||analyses.length>0)&&<details className="legacy-reports"><summary>此前保存的绘图与分析 · {plots.length+analyses.length} 项</summary><HistoryList rows={plots} context={context} render={plot=><article className="history-row" key={plot.id}><DeleteRecord kind="plots" id={plot.id} state={plot.state} label={plot.mode||'后处理绘图'} onChanged={loadPost}/><h3>{plot.mode} · {statusLabel(plot.state)}</h3><AnalysisDatasetRegister kind="plot" sourceId={plot.id} state={plot.state}/>{plot.error&&<p>{plot.error}</p>}{plot.outputs?.map((name:string)=><div key={name}>{name.endsWith('.png')&&<img className="local-plot" src={`/api/local-plots/${plot.id}/files/${name}`} alt={plot.mode}/>}<a className="button" href={`/api/local-plots/${plot.id}/files/${name}`} download>{name}</a></div>)}</article>}/><HistoryList rows={analyses} context={context} render={item=><article className="history-row" key={item.id}><DeleteRecord kind="local-analyses" id={item.id} state={item.state} label={item.extension?.name||'历史分析'} onChanged={loadPost}/><h3>{item.extension?.name} · {statusLabel(item.state)}</h3><AnalysisDatasetRegister kind="v1" sourceId={item.id} state={item.state}/><p>{time(item.created_at)} · 科学审核 {statusLabel(item.scientific_validation)}</p>{item.error&&<p>{item.error}</p>}{item.outputs?.map((name:string)=><a className="button" key={name} href={`/api/execution/${attemptId}/analyses/${item.id}/files/${encodeURIComponent(name)}`} download>{name}</a>)}</article>}/></details>}<RecycleBin key={context+'plots'} kind="plots" projectId={project.id} title="此前绘图回收区" onChanged={loadPost}/><RecycleBin key={context+'local'} kind="local-analyses" projectId={project.id} title="此前分析回收区" onChanged={loadPost}/></div></Suspense></div>}
 {section==='evidence'&&<section className="workspace-card"><div className="section-action-heading"><div><h3>来源与执行证据</h3></div><button className="button" disabled={busy.includes('status')||current.state==='PREPARED'||!supported} onClick={()=>void refreshStatus()}>核对原任务状态</button></div>{commandMode(current.config)!=='opaque'&&<CancelTask key={current.id} direct={commandMode(current.config)==='direct'} projectId={project.id} attemptId={current.id} state={current.state} label={project.nodes.find(n=>n.id===nodeId)?.data.label} onChanged={async()=>{await loadAttempts(attemptId);await loadSummary();}}/>}<div className="remote-result-path"><strong>{current.config?.alias||'主机未记录'} · {commandMode(current.config)==='direct'?directProcessLabel(current.state,current.direct_runtime):'job ID '+(current.job_id||'未确认')}</strong><code>{current.remote_path_absolute||current.remote_path||'路径未记录'}</code><button className="button" onClick={()=>void navigator.clipboard.writeText(current.remote_path_absolute||current.remote_path||'').catch(e=>setError({message:errorMessage(e),label:'重试复制路径',retry:()=>void navigator.clipboard.writeText(current.remote_path_absolute||current.remote_path||'')}))}>复制路径</button></div><div className="action-row"><a className="button" href={`/api/execution/${current.id}/technical-evidence`} download><FileText size={15}/>下载技术证据</a>{['FAILED','UNKNOWN'].includes(current.state)&&<a className="button" href={`/api/execution/${current.id}/failure-snapshot`} download>下载故障快照</a>}{Boolean(summary?.sources.length)&&supported&&<button className="button" disabled={busy.includes('verification')} onClick={()=>void verify()}>主动完整核验来源（较慢）</button>}</div>{verification&&<p role="status">{time(verification.checked_at)} · {verification.changed.length?'来源变化：'+verification.changed.join('、'):'未发现已记录身份变化'} · {verification.content_comparison}</p>}
 <details open className="evidence-details"><summary>程序、数值与物理证据</summary><pre>{JSON.stringify(current.evidence||{},null,2)}</pre></details>{summary&&<details className="evidence-details"><summary>实际冻结输入与结果指标来源</summary><pre>{JSON.stringify({input:summary.input,sources:summary.sources,metrics:summary.metrics,metrics_checked_at:summary.metrics_checked_at},null,2)}</pre></details>}<details className="recovery-guide"><summary>失败或状态未知时如何继续</summary><ol><li>核对原路径与运行回执。</li><li>在文件与日志中查看 vasp.err、调度器错误和程序末尾输出。</li><li>确认输入或环境问题后修改草稿，重新冻结和审阅。</li><li>新提交须单独授权，现有 UNKNOWN 保留核查。</li></ol><div className="action-row"><button className="button" onClick={()=>onConfigure?.(nodeId)}>打开此节点配置</button><button className="button" onClick={()=>onRun?.(nodeId,attemptId)}>打开运行审阅</button></div></details><RemoteDeleteAttempt key={current.id} projectId={project.id} attemptId={current.id} state={current.state} onDeleted={()=>void work('records','读取运行记录',()=>loadAttempts(''))} onOpenAttempt={openChild}/></section>}
 </>}</section>;
}
