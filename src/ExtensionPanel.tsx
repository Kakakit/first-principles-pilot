import HistoryList from './HistoryList';
import {useVisibleRefresh} from './useVisibleRefresh';
import {DeleteRecord,RecycleBin} from './RecordCleanup';
import {lazy,Suspense,useEffect,useRef,useState} from 'react';
import {readJson,errorMessage,requestJson} from './apiClient';
import {pollSerial} from './pollSerial';
import RemoteProgress from './RemoteProgress';
import ExtensionResult from './ExtensionResult';
import ExtensionSourcePicker from './ExtensionSourcePicker';
import {sourceCaption,retainedSourceFiles} from './extensionSourceSelection';
import {useProjectArtifact} from './extensionProjectArtifact';
import type {ArtifactRule} from './extensionProjectArtifact';
import type {SourceCandidate} from './extensionSourceSelection';
import {defaults,manageExtensions,runningAnalysis,analysisLabel,supportedLocation,sourceRuntime,sourceOptionsSchema,readableSource,runtimeChoice,environmentVersion} from './extensionModel';
import type {Analysis,ExtensionSource,ExtensionManifest,ResolvedSource,EnvironmentData,EnvironmentDoctor} from './extensionModel';
const ExtensionForm=lazy(()=>import('./ExtensionForm'));
const api=async(path:string,body?:unknown)=>readJson(await fetch('/api/extension-host'+path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
export default function ExtensionPanel({source,projectId,title='运行后处理',contextVersion=''}:{contextVersion?:string;source:ExtensionSource;projectId?:string;title?:string}){
 const [extensions,setExtensions]=useState<ExtensionManifest[]>([]),[resolved,setResolved]=useState<ResolvedSource[]>([]),[selected,setSelected]=useState(''),[options,setOptions]=useState<Record<string,unknown>>({}),[extra,setExtra]=useState<string[]>([]),[roleFiles,setRoleFiles]=useState<Record<string,Record<string,string>>>({});
 const [attempts,setAttempts]=useState<SourceCandidate[]>([]),[records,setRecords]=useState<Analysis[]>([]),[runtimes,setRuntimes]=useState<Record<string,any>>({}),[location,setLocation]=useState('local'),[alias,setAlias]=useState('');
 const [busy,setBusy]=useState(''),[loading,setLoading]=useState(false),[error,setError]=useState(''),[check,setCheck]=useState<{status:string;reason:string}|null>(null),[preview,setPreview]=useState<any>(null);
 const [historySearch,setHistorySearch]=useState(''),[historyState,setHistoryState]=useState('all'),[uncertain,setUncertain]=useState<{id?:string}|null>(null);
 const [environmentData,setEnvironmentData]=useState<EnvironmentData>({local:{python:'',inherited:true},runtimes:{},overrides:{}}),[environmentDoctor,setEnvironmentDoctor]=useState<EnvironmentDoctor|null>(null),[environmentRevision,setEnvironmentRevision]=useState(0);
 const creating=useRef(false);
 const historySequence=useRef(0);
 const locationPreference=useRef('local');
 const identity=source.kind==='attempt'?source.attempt_id:source.poscar;
 const currentSource=useRef('');currentSource.current=JSON.stringify([identity,projectId]);
 const sources:ExtensionSource[]=[{...source,files:roleFiles['0']||{}},...extra.filter(Boolean).map((id,index)=>({kind:'attempt' as const,attempt_id:id,files:roleFiles[String(index+1)]||{}}))];
 const currentRequest=useRef('');
 const selectionKey=JSON.stringify(sources),manifest=extensions.find(e=>e.id===selected);
 const chosenRuntime=runtimeChoice(selected,location,alias,environmentData);
 const artifactRule=manifest?.ui_schema['ui:projectArtifact'] as ArtifactRule|undefined;
 const artifactManual=Boolean(artifactRule&&options[artifactRule.field]);
 const allowAuto=!artifactRule?.auto_when||options[artifactRule.auto_when]===true;
 const artifact=useProjectArtifact(artifactRule,projectId,sources.flatMap(s=>s.kind==='attempt'?[s.attempt_id]:[]),artifactManual,allowAuto,contextVersion);
 const effectiveOptions=artifactRule&&artifact.value?{...options,[artifactRule.field]:artifact.value}:options;
 const payload={extension_id:selected,project_id:projectId,sources,options:effectiveOptions,location,alias,...(chosenRuntime?.python?{runtime_python:chosenRuntime.python}:{})};
 currentRequest.current=JSON.stringify(payload);
 useEffect(()=>{setCheck(null);setPreview(null);},[JSON.stringify(extra),JSON.stringify(roleFiles),alias]);
 useEffect(()=>{setCheck(null);setPreview(null);},[artifact.value,artifact.selected]);
 const history=():Promise<void>=>{
  const signature=JSON.stringify([identity,projectId]);
  const sequence=++historySequence.current;
  return requestJson('/api/extension-host/analyses'+(projectId?`?project_id=${encodeURIComponent(projectId)}`:source.kind==='attempt'?`?attempt_id=${source.attempt_id}`:'?project_id=geometry'),undefined,undefined,{timeoutMs:15000})
   .then(data=>{if(sequence===historySequence.current&&signature===currentSource.current)setRecords(data.analyses.filter((r:Analysis)=>source.kind==='attempt'?r.sources.some(s=>s.attempt_id===source.attempt_id):r.sources.some((s:any)=>s.sha256===resolved[0]?.sha256||s.poscar===source.poscar)));})
   .catch(error=>{if(sequence===historySequence.current&&signature===currentSource.current)throw error;});
 };
 const historyCallback=useRef(history);historyCallback.current=history;
 useEffect(()=>()=>{historySequence.current++;},[identity,projectId]);
 useEffect(()=>{setExtra([]);setAttempts([]);setRoleFiles({});setRecords([]);setSelected('');setCheck(null);setPreview(null);},[identity,projectId]);
 useEffect(()=>{let live=true;void api('/runtimes').then(data=>{if(live){setRuntimes(data.runtimes);setEnvironmentData(data);setAlias('');}}).catch(e=>{if(live)setError(errorMessage(e));});return()=>{live=false;};},[identity,projectId]);
 useEffect(()=>{let live=true;if(source.kind==='attempt'&&projectId)void api(`/sources?project_id=${encodeURIComponent(projectId)}`).then(data=>{if(live)setAttempts(data.attempts);}).catch(e=>{if(live)setError(errorMessage(e));});return()=>{live=false;};},[identity,projectId]);
 useEffect(()=>{let live=true;setLoading(extensions.length===0);setCheck(null);setPreview(null);void api('/context',{project_id:projectId,sources,location,alias}).then(data=>{if(live){setExtensions(data.extensions);setResolved(data.sources);setSelected(old=>data.extensions.some((e:ExtensionManifest)=>e.id===old)?old:data.extensions[0]?.id||'');setError('');}}).catch(e=>{if(live)setError(errorMessage(e));}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[selectionKey,location,alias,projectId,contextVersion,environmentRevision]);
 useEffect(()=>{setOptions(manifest?defaults(manifest.options_schema)||{}:{});setCheck(null);setPreview(null);},[selected]);
 useEffect(()=>{if(manifest)setLocation(supportedLocation(manifest,locationPreference.current));},[selected,JSON.stringify(manifest?.execution)]);
 useEffect(()=>{setAlias(sourceRuntime(resolved[0]?.alias,runtimes));},[resolved[0]?.alias,JSON.stringify(Object.keys(runtimes))]);
 useEffect(()=>{void history().catch(e=>setError(errorMessage(e)));},[identity,projectId,JSON.stringify(resolved.map(s=>s.id))]);
 useEffect(()=>{setEnvironmentDoctor(null);setPreview(null);},[selected,location,alias,chosenRuntime?.python]);
 const refreshEnvironment=async()=>{setBusy('environment');setError('');try{const data=await api('/runtimes');setRuntimes(data.runtimes);setEnvironmentData(data);setEnvironmentDoctor(null);setPreview(null);setEnvironmentRevision(v=>v+1);}catch(e){setError(errorMessage(e));}finally{setBusy('');}};
 const refreshSources=async()=>{const signature=currentRequest.current;setBusy('sources');setError('');setCheck(null);setPreview(null);try{const data=await api('/context',{project_id:projectId,sources,location,alias,refresh_sources:true});if(signature===currentRequest.current){setExtensions(data.extensions);setResolved(data.sources);}}catch(e){if(signature===currentRequest.current)setError(errorMessage(e));}finally{setBusy('');}};
 const inspectEnvironment=async()=>{const before=currentRequest.current;setBusy('environment');setError('');try{const response=await api('/packages/'+selected+'/doctor',{location,alias});if(before===currentRequest.current){setEnvironmentDoctor(response.doctor);setPreview(null);setEnvironmentRevision(v=>v+1);}}catch(e){if(before===currentRequest.current)setError(errorMessage(e));}finally{setBusy('');}};
 const refreshMetadata=async()=>{
  const before=currentRequest.current;
  try{
   let data=await api('/context',{project_id:projectId,sources,location,alias});
   if(before!==currentRequest.current)return;
   if(data.sources.some((item:ResolvedSource)=>item.needs_refresh&&!item.refreshing&&item.host_state==='READY'))data=await api('/context',{project_id:projectId,sources,location,alias,refresh_sources:true});
   if(before===currentRequest.current){setExtensions(data.extensions);setResolved(data.sources);}
  }catch(e){if(before===currentRequest.current)setError(errorMessage(e));}
 };
 const {element:metadataElement,visible:panelVisible}=useVisibleRefresh(source.kind==='attempt',selectionKey+'|'+location+'|'+alias,refreshMetadata);
 const historyRunning=records.some(r=>runningAnalysis(r.state)||r.presentation?.status==='GENERATING');
 useEffect(()=>{if(!panelVisible||!historyRunning)return;return pollSerial(()=>historyCallback.current(),e=>setError(errorMessage(e)));},[historyRunning,panelVisible,identity,projectId]);
 useEffect(()=>{
  if(!panelVisible||!extensions.some(item=>item.environment_checking))return;
  const before=currentRequest.current;
  const timer=window.setTimeout(()=>void api('/context',{project_id:projectId,sources,location,alias}).then(data=>{if(before===currentRequest.current)setExtensions(data.extensions);}).catch(e=>{if(before===currentRequest.current)setError(errorMessage(e));}),1000);
  return()=>window.clearTimeout(timer);
 },[extensions,panelVisible,location,alias,selectionKey]);
 const perform=async(action:'check'|'preview'|'run')=>{
  if(action==='run'&&(creating.current||uncertain))return;
  const signature=currentRequest.current;let receiptId='';
  if(action==='run')creating.current=true;setBusy(action);setError('');
  try{
   if(action==='check'){const response=await api('/check',payload);if(signature===currentRequest.current)setCheck(response);}
   else if(action==='preview'){const response=await api('/preview',payload);if(signature===currentRequest.current)setPreview(response);}
   else{
    const receipt=await api('/analyses',{...payload,preview_id:preview?.preview_id});
    if(typeof receipt.analysis_id!=='string'||!receipt.analysis_id)throw new Error('无法读取分析创建回执；先核对分析历史。');
    receiptId=receipt.analysis_id;const record=await api('/analyses/'+receiptId);
    if(signature!==currentRequest.current)return;
    historySequence.current++;
    setRecords(old=>[record,...old.filter(r=>r.id!==record.id)]);setPreview(null);
   }
  }catch(e){if(signature===currentRequest.current){setError(errorMessage(e));if(action==='run'&&(receiptId||e instanceof TypeError||/无法连接|空响应|无法读取|HTTP (5\d\d|408)/.test(errorMessage(e))))setUncertain({id:receiptId||undefined});}}
  finally{creating.current=false;setBusy('');}
 };
 const verifyReceipt=async()=>{
  const signature=currentSource.current;setBusy('receipt');
  try{
   if(uncertain?.id){const record=await api('/analyses/'+uncertain.id);if(signature!==currentSource.current)return;setRecords(old=>[record,...old.filter(r=>r.id!==record.id)]);setUncertain(null);}
   else await history();
   if(signature===currentSource.current)setError('');
  }catch(e){if(signature===currentSource.current)setError(errorMessage(e));}finally{setBusy('');}
 };
 const shownRecords=records.filter(r=>(historyState==='all'||r.state===historyState)&&`${r.manifest.name} ${r.id} ${r.job_id||''}`.toLowerCase().includes(historySearch.toLowerCase()));
 const blockers=[...(manifest?.blockers||[]),...(location!=='local'&&!alias?['尚未登记 '+(resolved[0]?.alias||'来源服务器')+' 的已有 Python；点击“登记服务器 Python”，填写该服务器已有解释器的绝对路径']:[]),...(environmentDoctor&&!environmentDoctor.ready?['所选 Python 环境不可用，请修改环境或核对检查结果']:[]),...(extra.some(id=>!id)?['请选择已添加的来源，或移除此来源']:[])];
 return <section ref={metadataElement} className="extension-workspace"><div className="extension-heading"><div><h3>{title}</h3></div><button className="button" onClick={manageExtensions}>扩展管理</button></div>
 <div className="extension-selection"><label>运行位置<select aria-label="运行位置" value={location} onChange={e=>{locationPreference.current=e.target.value;setLocation(e.target.value);setPreview(null);}}>{(manifest?.execution.locations||['local','ssh']).filter(mode=>mode==='local'||mode==='ssh').map(mode=><option key={mode} value={mode}>{{local:'本地运行',ssh:'服务器运行 · 直接命令行'}[mode]||mode}</option>)}</select></label>{location!=='local'&&<label>已有运行时<select aria-label="已有运行时" value={alias} onChange={e=>{setAlias(e.target.value);setPreview(null);}}><option value="">先在扩展管理登记</option>{Object.keys(runtimes).map(key=><option key={key}>{key}</option>)}</select></label>}<label>扩展<select aria-label="扩展" value={selected} onChange={e=>setSelected(e.target.value)}><option value="">选择适用的扩展</option>{extensions.map(e=><option value={e.id} key={e.id}>{e.name}</option>)}</select></label></div>
 <div className="button-row"><button className="text-button" disabled={!!busy||loading} onClick={()=>void refreshSources()}>{busy==='sources'?'正在刷新来源…':'刷新来源文件清单'}</button></div><details className="extension-runtime-choice"><summary>运行环境与检查</summary><p>{chosenRuntime?.python?'选定 Python：'+chosenRuntime.python+' · '+(chosenRuntime.inherited?'继承默认环境':'扩展单独指定'):'先登记来源服务器的已有 Python，填写参数不受影响。'}</p><div className="button-row"><button className="text-button" onClick={manageExtensions}>{location==='local'?'配置本地 Python':'登记服务器 Python'}</button><button className="text-button" disabled={!!busy||!manifest||!chosenRuntime?.python} onClick={()=>void inspectEnvironment()}>检查所选环境</button><button className="text-button" disabled={!!busy} onClick={()=>void refreshEnvironment()}>刷新环境设置</button></div>{environmentDoctor&&<p className={environmentDoctor.ready?'extension-notice':'handoff-warning'} role="status">所选环境：{environmentDoctor.ready?'可用':'不可用'}{environmentVersion(environmentDoctor)&&` · ${environmentVersion(environmentDoctor)}`} · {environmentDoctor.missing?.join('、')||environmentDoctor.reason}</p>}</details>
 {loading&&<RemoteProgress label="正在读取来源文件和适用扩展"/>}{error&&<p className="structure-error" role="alert">{error}</p>}{!loading&&!extensions.length&&<p className="extension-empty">当前没有已启用且适用于此阶段的扩展。打开扩展管理，导入并启用所需工具。</p>}
 {uncertain&&<div className="action-error" role="alert"><strong>本次分析的创建回执未确认</strong><p>{uncertain.id?'已记录本次分析；再次读取该记录即可恢复。':'先核对分析历史与原任务，当前不会重复创建。'}</p><button className="button" disabled={!!busy} onClick={()=>void verifyReceipt()}>{uncertain.id?'读取原分析记录':'核对分析历史'}</button>{!uncertain.id&&<button className="text-button" disabled={!!busy} onClick={()=>{setUncertain(null);setPreview(null);}}>已核对历史，允许另建分析</button>}</div>}{manifest&&<><details className="extension-help"><summary>扩展说明</summary><p>{manifest.description}</p></details><div className="extension-sources"><h4>1. 确认来源与输入文件</h4>{resolved.map((s,index)=><div key={s.id}><strong>来源 {index+1} · {attempts.find(a=>a.id===s.id)?sourceCaption(attempts.find(a=>a.id===s.id)!):readableSource(s.name)}</strong><span>{s.stage} · {s.alias||'结构草稿'}</span>{s.input_summary?.seed&&<p>通道：{s.input_summary.seed}{s.input_summary.seed.endsWith('.1')?' · 上自旋':s.input_summary.seed.endsWith('.2')?' · 下自旋':''} · num_wann：{s.input_summary.num_wann??'未记录'} · DFT 父来源：{readableSource(s.input_summary.parent_name,s.input_summary.parent_attempt_id?'DFT 来源已记录':'缺少来源')} · 冻结 E_F：{s.input_summary.energy_reference?.fermi_eV==null?'缺少；请在选项中填写手动参考':s.input_summary.energy_reference.fermi_eV+' eV'}</p>}{manifest.inputs.map(role=><label key={role.role}>{role.title}<select aria-label={`${role.title} · 来源 ${index+1}`} value={roleFiles[String(index)]?.[role.role]??role.names.find(n=>s.files.some(f=>f.name===n))??''} onChange={e=>setRoleFiles(old=>({...old,[String(index)]:{...old[String(index)],[role.role]:e.target.value}}))}><option value="">{role.required===false?'不提供（可选；组合由扩展检查）':(s.needs_refresh||s.files_incomplete)?'清单待更新':'缺少输入'}</option>{role.names.filter(n=>s.files.some(f=>f.name===n)).map(n=><option key={n}>{n}</option>)}</select></label>)}</div>)}</div>
 {source.kind==='attempt'&&(manifest.sources.max>1||extra.length>0)&&<div className="extension-extra"><ExtensionSourcePicker key={selected} attempts={attempts} primary={source.attempt_id} extra={extra} maximum={manifest.sources.max} stages={manifest.stages} alias={location==='ssh'?resolved[0]?.alias:undefined} disabled={!!busy||loading} onApply={ids=>{setRoleFiles(old=>retainedSourceFiles(source.attempt_id,extra,ids,old));setExtra(ids);}}/><details open={extra.length>0&&extra.length<8}><summary>附加来源 {extra.filter(Boolean).length} 个 · 查看或逐个调整</summary>{extra.map((value,i)=><label key={i}>来源 {i+2}<select aria-label={`来源 ${i+2}`} value={value} disabled={!!busy||loading} onChange={e=>{setExtra(old=>old.map((s,index)=>index===i?e.target.value:s));setRoleFiles({});}}><option value="">请选择</option>{attempts.filter(a=>a.id!==source.attempt_id&&(!extra.includes(a.id)||a.id===value)&&(!manifest.stages.length||manifest.stages.includes(a.stage))&&(location!=='ssh'||a.alias===resolved[0]?.alias)).map(a=><option value={a.id} key={a.id}>{sourceCaption(a)} · {a.alias} · {a.state}</option>)}</select><button className="text-button" disabled={!!busy||loading} onClick={()=>{setExtra(old=>old.filter((_,index)=>index!==i));setRoleFiles({});}}>移除此来源</button></label>)}{extra.length+1<manifest.sources.max&&<button className="button" disabled={!!busy||loading||extra.some(id=>!id)} onClick={()=>setExtra(old=>[...old,''])}>添加来源</button>}</details></div>}

 <h4>2. 填写必要选项</h4>{blockers.length>0&&<div className="handoff-warning">{blockers.map((reason,i)=><p key={i}>{reason}</p>)}</div>}{artifact.visible&&artifactRule&&<div className="extension-field">
  <label>{artifactRule.title}<select value={artifact.selected} disabled={!!busy||artifactManual} onChange={e=>artifact.select(e.target.value)}>
   <option value="">不使用项目报告</option>
   {artifact.candidates.map(item=><option value={item.id} key={item.id}>{item.sources.map(source=>readableSource(source.name||'来源')).join(' / ')} · {new Date(item.created_at).toLocaleString()}</option>)}
  </select></label>
  <button type="button" className="text-button" disabled={!!busy} onClick={artifact.refresh}>刷新项目报告</button>
  <p role="status">{artifactManual?'正在使用下方手动导入的文件。':artifact.error|| (artifact.pending||artifact.loading?'正在读取项目产物…':artifact.auto?'已自动匹配这组计算来源。':artifact.value?'已选入报告，将随本次分析核对。':artifact.candidates.length?'请选用一份项目报告，也可在下方导入文件。':'暂无匹配的项目报告，可在下方手动导入。')}</p>
 </div>}<div className="extension-options"><Suspense fallback={<p>正在载入参数表单…</p>}><ExtensionForm manifest={manifest} schema={sourceOptionsSchema(manifest,resolved)} sourceSpinors={resolved.some(s=>s.input_summary?.spinors===true||s.input_summary?.spinors==='true')} value={options} onChange={value=>{setOptions(value);setCheck(null);setPreview(null);}} onSubmit={()=>void perform(location==='local'?'run':'preview')} onCheck={()=>void perform('check')} checkResult={check} running={records.some(r=>r.extension_id===selected&&runningAnalysis(r.state))} busy={!!busy} blocked={Boolean(uncertain)||loading||artifact.pending||Boolean(artifact.selected&&artifact.error&&!artifactManual)||blockers.length>0} label={location==='local'?'检查并运行分析':'预览远端运行脚本'}/></Suspense></div>
 {preview&&<div className="extension-dispatch-review"><h4>4. 审阅后运行</h4><p>{preview.reason}</p>{Array.isArray(manifest?.ui_schema['ui:preview'])&&<div className="extension-preview-options"><strong>本次预览将冻结的参数</strong>{manifest.ui_schema['ui:preview'].map((key:string)=><p key={key}>{manifest.options_schema.properties[key]?.title||key}：<b>{JSON.stringify((preview.options||options)[key]??'未设置')}</b></p>)}</div>}{preview.script&&<pre className="input-text">{preview.script}</pre>}{preview.ready&&<button className="button primary" disabled={!!busy||Boolean(uncertain)} onClick={()=>void perform('run')}>按此来源与脚本运行</button>}</div>}{busy&&<RemoteProgress label={busy==='environment'?'正在检查所选 Python 环境':busy==='receipt'?'正在核对原分析记录':busy==='run'?'正在创建独立分析记录':busy==='preview'?'正在生成服务器运行脚本':'正在检查来源与选项'}/>}</>}
 <div className="extension-history"><div className="extension-heading"><h3>分析历史</h3><button className="text-button" onClick={()=>void history().catch(e=>setError(errorMessage(e)))}>刷新记录</button></div><div className="filter-bar"><label>搜索分析<input value={historySearch} onChange={e=>setHistorySearch(e.target.value)} placeholder="名称、作业号"/></label><label>分析状态<select value={historyState} onChange={e=>setHistoryState(e.target.value)}><option value="all">全部状态</option>{[...new Set(records.map(r=>r.state))].map(state=><option key={state} value={state}>{analysisLabel(state)}</option>)}</select></label><span>{shownRecords.length} / {records.length} 条</span></div>{records.length===0?<p>暂无分析记录。</p>:<HistoryList rows={shownRecords} context={identity+projectId+historySearch+historyState} render={(record,index)=><div className="history-row" key={record.id}><DeleteRecord kind="analyses" id={record.id} state={record.state} label={record.manifest.name} onChanged={history}/><details open={index===0} className="analysis-history-record"><summary>{record.manifest.name} · {analysisLabel(record.state)} · {new Date(record.created_at).toLocaleString()}</summary>{record.state==='PAUSED'&&<button className="button" disabled={!!busy} onClick={()=>void api('/analyses/'+record.id+'/resume',{}).then(history).catch(e=>setError(errorMessage(e)))}>继续此暂停分析</button>}<ExtensionResult key={record.id} record={record} onChanged={history} onRefresh={async()=>{const updated=await api('/analyses/'+record.id+'/refresh',{});await history();return updated;}}/></details></div>}/>}<RecycleBin key={projectId||identity} kind="analyses" projectId={projectId||'geometry'} title="分析回收区" onChanged={history}/></div></section>;
}
