import {scfSourceId,type LineageAttempt} from './wannierLineage';
import {useSubmissionDraft} from './useSubmissionDraft';
import {sameCapacityTarget} from './executionUi';
import {DeleteRecord} from './RecordCleanup';
import HistoryList from './HistoryList';
import ServerPanel from './ServerPanel';
import {runtimeFor} from './submissionDefaults';
import SubmitConfirmation from './SubmitConfirmation';
import {errorMessage,requestJson} from './apiClient';
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import type {Project} from './model';
import type {RegisteredProfile, ServerConfig, ServerPreview, ServerProbe} from './executionUi';
import InteractivePlot from './InteractivePlot';
import RemoteProgress from './RemoteProgress';

type Attempt=LineageAttempt & {parent_attempt_id?:string;profile?:{ppn?:number;ntasks?:number};id:string;state:string;node_id:string;job_id?:string;config:{alias:string};remote_path_absolute?:string;kind?:string;stage_slug?:string;evidence?:Record<string,unknown>;source_file_plan?:Record<string,{target:string;mode:string;size_bytes:number}>;dependency_snapshot?:{upstream_attempt_id?:string}};
type Source={energy_reference?:EnergyReference;source_path:string;win_source_name:string;source_job_id?:string;files:Record<string,{sha256?:string;size_bytes:number;mtime?:string;verification_mode?:string}>;win:string;spinors?:string;parameters:Record<string,string>};
type Script={alias:string;name:string;sha256:string;evidence:string};
type WinParameter={tag:string;label:string;type:string;unit:string;choices?:string[]};
type BandPath={project_id:string;band_attempt_id:string;parent_attempt_id:string;scf_attempt_id:string;alias:string;kpoints_sha256:string;path_sha256:string;path_lines:string[];bands_num_points:number;source_path:string;generator:string};
type ChannelDraft={sourceHash:string;win:string;parameters:Record<string,string>;applied:Record<string,string>;inputReason:string;bandPath?:BandPath|null;bandId?:string;bandEnabled?:boolean;bandLines?:string;bandPoints?:string};
type EnergyReference={fermi_eV:number;vbm_eV:number|null;cbm_eV:number|null;fermi_source:string;outcar_sha256:string;remote_path:string};
type Advice={mode:'eig'|'pre';seed:string;source_attempt_id:string;source_hashes:Record<string,string>;fermi_eV:number;relative_range_eV:number[];pyw90_version:string;advice:{outer?:Array<Record<string,number>>;frozen?:Array<Record<string,number>>;projections?:string[];minimum_outer_max?:number;eigenval?:{channel:string;spinors?:boolean;nkpts:number;nbands:number;suggested_num_wann?:number}};note:string};
const windowTags=new Set(['dis_win_min','dis_win_max','dis_froz_min','dis_froz_max']);
function relativeValues(values:Record<string,string>,fermi:number){return Object.fromEntries(Object.entries(values).map(([tag,value])=>[tag,windowTags.has(tag)&&value.trim()?String(Number((Number(value)-fermi).toFixed(10))):value]));}
const api=requestJson;
export default function WannierPanel({project,initialNodeId,embedded=false,view,onReview,onConfigure}:{project:Project;initialNodeId:string|null;embedded?:boolean;view?:'config'|'run';onReview?:()=>void;onConfigure?:()=>void}){
  const nodes=project.nodes.filter(item=>item.data.kind==='wannier90');
  const submissionKey='wannier:'+project.id+':'+initialNodeId;
  const [frozen,setFrozen]=useSubmissionDraft(submissionKey+':frozen',''),[frozenRows,setFrozenRows]=useState<{id:string;seed:string;alias:string;input_reason:string;created_at:string}[]>([]),[confirmOpen,setConfirmOpen]=useState(false);
  const [nodeId,setNodeId]=useState(nodes.some(item=>item.id===initialNodeId)?initialNodeId||'':nodes[0]?.id||'');
  const [config,setConfig]=useSubmissionDraft<ServerConfig|null>(submissionKey+':config',null);
  const [profiles,setProfiles]=useState<RegisteredProfile[]>([]);
  const [attempts,setAttempts]=useState<Attempt[]>([]);
  const [scripts,setScripts]=useState<Script[]>([]);
  const [parameterCatalog,setParameterCatalog]=useState<WinParameter[]>([]);
  const [parameterDraft,setParameterDraft]=useState<Record<string,string>>({});
  const [appliedParameters,setAppliedParameters]=useState<Record<string,string>>({});
  const [templates,setTemplates]=useState<{id:string;name:string;alias:string;program:string}[]>([]);
  const [probe,setProbe]=useSubmissionDraft<ServerProbe|null>(submissionKey+':probe',null);
  const [preview,setPreview]=useState<ServerPreview|null>(null);
  const [source,setSource]=useState<Source|null>(null);
  const [energyReference,setEnergyReference]=useState<EnergyReference|null>(null);
  const [energyError,setEnergyError]=useState('');
  const [energyLoading,setEnergyLoading]=useState(false);
  const [advice,setAdvice]=useState<Record<string,Advice>>({}),[adviceError,setAdviceError]=useState('');
  const [adviceBusy,setAdviceBusy]=useState(false),[adviceMin,setAdviceMin]=useState('-4'),[adviceMax,setAdviceMax]=useState('4'),[dosSourceId,setDosSourceId]=useState('');
  const [parentId,setParentId]=useState('');
  const [bandId,setBandId]=useState(''),[bandPath,setBandPath]=useState<BandPath|null>(null);
  const [bandEnabled,setBandEnabled]=useState(false),[bandLines,setBandLines]=useState(''),[bandPoints,setBandPoints]=useState('');
  const [bandDirty,setBandDirty]=useState(false);
  const [seeds,setSeeds]=useState<string[]>([]);
  const [seed,setSeed]=useState('');
  const [computeNode,setComputeNode]=useSubmissionDraft(submissionKey+':computeNode','');
  const [cores,setCores]=useSubmissionDraft(submissionKey+':cores','');
  const [resourceReason,setResourceReason]=useSubmissionDraft(submissionKey+':resourceReason','');
  const [inputReason,setInputReason]=useState('');
  const [win,setWin]=useState('');
  const [templateId,setTemplateId]=useSubmissionDraft(submissionKey+':templateId','');
  const [prepared,setPrepared]=useState<Attempt&{script:string;bundle_id:string;note:string}|null>(null);
  const [busy,setBusy]=useState(false);
  const [busyStep,setBusyStep]=useState('');
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');
  const node=nodes.find(item=>item.id===nodeId);
  const parents=useMemo(()=>new Set(project.edges.filter(edge=>edge.target===nodeId).map(edge=>edge.source)),[project.edges,nodeId]);
  const candidates=attempts.filter(item=>parents.has(item.node_id)&&item.state==='COMPLETED'&&item.config.alias===config?.alias);
  const computedNodes=(probe?.preflight.hosts.flatMap(host=>host.nodes)||[]).filter(item=>!(config?.alias==='jutai-1'&&item.NODE==='node06'));
  const draftKey=(channel:string)=>`fpilot-w90-draft-v2:${project.id}:${nodeId}:${parentId}:${config?.alias||''}:${channel}`;
  const pendingDraft=useRef<{key:string;value:ChannelDraft}|null>(null);
  const flushDraft=useCallback(()=>{
    const pending=pendingDraft.current;if(!pending)return;
    try{sessionStorage.setItem(pending.key,JSON.stringify(pending.value));pendingDraft.current=null;}catch{/* 当前页面中的参数仍可继续编辑 */}
  },[]);
  useEffect(()=>{
    window.addEventListener('pagehide',flushDraft);
    return()=>{window.removeEventListener('pagehide',flushDraft);flushDraft();};
  },[flushDraft]);
  useEffect(()=>{
    if(!seed||(!source&&!Object.values(parameterDraft).some(value=>value.trim())))return;
    const draft:ChannelDraft={sourceHash:source?.files[source.win_source_name].sha256||'',win,parameters:parameterDraft,applied:appliedParameters,inputReason,bandPath,bandId,bandEnabled,bandLines,bandPoints};
    const key=draftKey(seed);
    if(pendingDraft.current?.key!==key)flushDraft();
    pendingDraft.current={key,value:draft};
    const timer=window.setTimeout(flushDraft,250);
    return()=>window.clearTimeout(timer);
  },[source,seed,win,parameterDraft,appliedParameters,inputReason,bandPath,bandId,bandEnabled,bandLines,bandPoints,project.id,nodeId,parentId,config?.alias,flushDraft]);
  const parametersPending=Object.entries(parameterDraft).some(([tag,value])=>value.trim()&&value.trim()!==(appliedParameters[tag]||'').trim());
  const invalidate=(level:'all'|'source'|'preview'='all')=>{setPrepared(null);if(level==='all'){setProbe(null);setPreview(null);setSource(null);setEnergyReference(null);setEnergyError('');setAdvice({});setParentId('');setSeeds([]);setSeed('');setComputeNode('');}else if(level==='source'){setSource(null);setEnergyReference(null);setEnergyError('');setAdvice({});setPreview(null);}else setPreview(null);};
  const loadEnergy=async()=>{if(!parentId)return;setBusy(true);setEnergyLoading(true);setBusyStep('正在读取 OUTCAR 能量参考');setEnergyError('');try{const reference:EnergyReference=await api(`/api/wannier90/energy-reference?project_id=${encodeURIComponent(project.id)}&attempt_id=${encodeURIComponent(parentId)}`);setEnergyReference(reference);if(source){const relative=relativeValues(source.parameters,reference.fermi_eV);setParameterDraft(old=>({...relative,...Object.fromEntries(Object.entries(old).filter(([,value])=>value.trim()))}));setAppliedParameters(old=>({...relative,...Object.fromEntries(Object.entries(old).filter(([,value])=>value.trim()))}));}}catch(caught){setEnergyReference(null);setEnergyError(errorMessage(caught));}finally{setEnergyLoading(false);setBusy(false);setBusyStep('');}};
  const load=async()=>{
    const results=await Promise.allSettled([api('/api/server'),api(`/api/execution?project_id=${encodeURIComponent(project.id)}`),api('/api/wannier90/scripts'),api('/api/job-templates'),api('/api/wannier90/parameters')]);
    const [server,history,choices,saved,parameters]=results;
    if(server.status==='fulfilled'){setConfig(old=>old||{...server.value.config,name:'wannier90'});setProfiles(server.value.registered_profiles||[]);}
    if(history.status==='fulfilled')setAttempts(history.value);
    if(choices.status==='fulfilled')setScripts(choices.value);
    if(saved.status==='fulfilled')setTemplates(saved.value);
    if(parameters.status==='fulfilled')setParameterCatalog(parameters.value);
    setError(results.some(result=>result.status==='rejected')?'部分资料未能读取；参数目录或服务器恢复后可点击“重新载入”。':'');
  };
  useEffect(()=>{void load();},[project.id]);
  const loadFrozen=async()=>{const rows=await api('/api/wannier90/configurations?project_id='+encodeURIComponent(project.id)+'&node_id='+encodeURIComponent(nodeId));setFrozenRows(rows);setFrozen(value=>rows.some((row:{id:string})=>row.id===value)?value:rows[0]?.id||'');};
  useEffect(()=>{void loadFrozen().catch(e=>setError(errorMessage(e)));const changed=()=>void loadFrozen().catch(e=>setError(errorMessage(e)));window.addEventListener('fpilot:history-changed',changed);return()=>window.removeEventListener('fpilot:history-changed',changed);},[project.id,nodeId]);
  useEffect(()=>{if(!parentId&&candidates.length)setParentId(candidates[0].id);},[parentId,candidates.map(c=>c.id).join(',')]);
  useEffect(()=>{const row=frozenRows.find(r=>r.id===frozen);if(view!=='run'||!row||!config||row.alias===config.alias)return;void api('/api/workbench-settings').then(settings=>{setConfig(runtimeFor(config,profiles,settings,row.alias,'wannier90'));invalidate('preview');setProbe(null);setComputeNode('');});},[frozen,frozenRows.length,view,config?.alias]);
  const freezeConfiguration=async()=>{if(!config)return;setBusy(true);setError('');try{const relative_windows=Object.fromEntries(Object.entries(appliedParameters).filter(([tag,value])=>windowTags.has(tag)&&value.trim()).map(([tag,value])=>[tag,Number(value)]));const frozenPath=bandEnabled&&bandPath?{...bandPath,path_lines:bandLines.split('\n').map(line=>line.trim()).filter(Boolean),bands_num_points:Number(bandPoints)}:null;const value=await api('/api/wannier90/configurations/freeze',{project,node_id:nodeId,parent_id:parentId,alias:config.alias,input_reason:inputReason,win,seed,energy_reference:energyReference,relative_windows,band_path:frozenPath});setFrozen(value.id);setFrozenRows(old=>[value,...old]);onReview?.();}catch(e){setError(errorMessage(e));}finally{setBusy(false);}};

  const run=async(action:'probe'|'discover'|'inspect'|'preview'|'prepare'|'submit'|'refresh',force=false)=>{
    if(!config||!node)return;setBusy(true);setBusyStep(({probe:'正在诊断主机资源',discover:'正在发现接口文件组',inspect:'正在载入 .win 与来源摘要',preview:'正在预览资源与脚本',prepare:'正在核对来源并准备新 attempt',submit:'正在提交并等待准确回执',refresh:'正在读取任务状态'} as const)[action]);setError('');
    try{
      if(action==='probe'){
        const value=await api('/api/wannier90/probe',{config});setProbe(value);setPreview(null);setPrepared(null);setMessage('实时资源诊断完成；请选择计算节点。');
      }else if(action==='discover'){
        const value=await api('/api/wannier90/discover',{project,node_id:nodeId,parent_id:parentId,alias:config.alias});
        setSeeds(value.seeds);setSeed(value.seeds.length===1?value.seeds[0]:'');setSource(null);
        setMessage(value.mode==='two-collinear-channels'?'发现 .1 和 .2 两个共线自旋通道；每次 attempt 显式选择一个通道。':'发现一组完整接口文件。');
      }else if(action==='inspect'){
        const value:Source=await api('/api/wannier90/inspect',{project,node_id:nodeId,parent_id:parentId,alias:config.alias,seed,force});
        setEnergyLoading(true);setSource(value);setEnergyReference(null);setEnergyError('');
        const saved=sessionStorage.getItem(draftKey(seed));
        let draft:ChannelDraft|null=null;
        if(saved){try{const parsed=JSON.parse(saved) as ChannelDraft;if(!parsed.sourceHash||parsed.sourceHash===value.files[value.win_source_name].sha256)draft=parsed;}catch{/* discard invalid session draft */}}
        let reference:EnergyReference|null=null;
        setBusyStep('接口文件已核对，正在读取 OUTCAR 能量参考');
        try{reference=value.energy_reference||null;if(!reference)throw new Error('来源 OUTCAR 尚无可读能量参考');setEnergyReference(reference);}catch(caught){setEnergyError(errorMessage(caught));}finally{setEnergyLoading(false);}
        const relative:Record<string,string>=reference?relativeValues(value.parameters,reference.fermi_eV):Object.fromEntries(Object.entries(value.parameters).map(([tag,v])=>[tag,windowTags.has(tag)?'':v]));
        setWin(draft?.sourceHash?draft.win:value.win);setParameterDraft({...relative,...(draft?.parameters||{})});setAppliedParameters(draft?.sourceHash?draft.applied:relative);setInputReason(draft?.inputReason??'');setBandPath(draft?.bandPath||null);setBandId(draft?.bandId||'');setBandEnabled(Boolean(draft?.bandEnabled));setBandLines(draft?.bandLines||'');setBandPoints(draft?.bandPoints||'');setBandDirty(false);setPrepared(null);
        setMessage(reference?(draft?'已核对远端四文件并恢复该通道的独立参数草稿。':'已核对远端四文件并载入 .win；大型矩阵未回传。'):'接口文件和 .win 已载入；OUTCAR 能量参考暂不可用，请单独重试。');
      }else if(action==='preview'){
        const value=await api('/api/wannier90/preview',{config,probe_id:probe?.id,node:computeNode,cores:cores?Number(cores):null,resource_reason:resourceReason,template_id:templateId||null});
        setPreview(value);setPrepared(null);setMessage('资源与脚本预览已生成；尚未提交。');
      }else if(action==='prepare'){
        if(!frozen)throw new Error('请先在计算配置中冻结 Wannier90 输入');
        
        const value=await api('/api/wannier90/prepare',{project,node_id:nodeId,parent_id:parentId||'frozen',config,probe_id:probe?.id||'',node:computeNode,cores:cores?Number(cores):null,resource_reason:resourceReason,input_reason:inputReason,win,template_id:templateId||null,seed,energy_reference:energyReference,frozen_id:frozen});
        setPrepared(value);setConfirmOpen(true);
      }else if(prepared){
        const value=await api(`/api/execution/${prepared.id}/${action}`,{});
        setPrepared(value);setMessage(action==='submit'?(value.not_dispatched?'实时复核未通过，没有调用调度器；草稿和节点选择保留。':'提交回执已记录；请核对 job ID。'):'状态已刷新。');
        setAttempts(await api(`/api/execution?project_id=${encodeURIComponent(project.id)}`));
      }
    }catch(caught){setError(errorMessage(caught));}finally{setBusy(false);setBusyStep('');setEnergyLoading(false);}
  };
  const selectHost=(alias:string)=>{
    const selected=profiles.find(item=>item.alias===alias);
    if(!selected||!config)return;
    setConfig({...config,alias,scheduler:selected.scheduler.toLowerCase(),queue:selected.queue,mpi_executable:selected.mpi_executable,vasp_executable:selected.vasp_executable,environment_script:selected.environment_script,module:selected.module,launcher:selected.launcher,runtime_environment:selected.runtime_environment,pmi_library:selected.pmi_library,pmi_type:selected.pmi_type});
    invalidate();setTemplateId('');
  };
  const imported=async(file:File)=>{
    if(!config)return;setBusy(true);setError('');
    try{
      if(file.size>65536)throw new Error('脚本超过 64 KiB');
      const text=await file.text();const scheduler=config.scheduler?.toLowerCase().includes('slurm')?'slurm':'pbs';
      const result=await api('/api/job-templates',{script:text,name:file.name,scheduler,alias:config.alias,program:'wannier90',source:file.name});
      setTemplates(await api('/api/job-templates'));
      setTemplateId(result.id);setPreview(null);setPrepared(null);setMessage('用户脚本已保存并选择；正文由您核对。');
    }catch(caught){setError(errorMessage(caught));}finally{setBusy(false);}
  };
  const applyParameters=async()=>{
    if(!energyReference){setEnergyError('先读取 OUTCAR 能量参考，再应用相对能窗参数。');return;}
    const changes=Object.fromEntries(Object.entries(parameterDraft).filter(([tag,value])=>value.trim()&&value.trim()!==(appliedParameters[tag]||'').trim()));
    if(!Object.keys(changes).length)return;
    setBusy(true);setError('');
    try{
      const result=await api('/api/wannier90/win-edit',{win,changes,fermi_eV:energyReference?.fermi_eV});
      const relative=relativeValues(result.parameters,energyReference?.fermi_eV??0);
      setWin(result.win);setAppliedParameters(relative);setParameterDraft(relative);setPrepared(null);
      setMessage('参数已按 Wannier90 官方 tag 注册表写入下方 .win；尚未冻结。');
    }catch(caught){setError(errorMessage(caught));}finally{setBusy(false);}
  };
  const runAdvice=async(mode:'eig'|'pre')=>{
    if(!config||!source||!energyReference)return;
    setAdviceBusy(true);setAdviceError('');
    try{const result:Advice=await api('/api/wannier90/pyw90-advice',{project,node_id:nodeId,parent_id:parentId,alias:config.alias,seed,mode,emin:Number(adviceMin),emax:Number(adviceMax),energy_reference:energyReference,dos_attempt_id:mode==='pre'&&dosSourceId?dosSourceId:null});setAdvice(old=>({...old,[seed+':'+mode]:result}));}
    catch(caught){setAdviceError(errorMessage(caught));}
    finally{setAdviceBusy(false);}
  };
  const adoptAdvice=(values:Record<string,number>)=>{const tags=Object.fromEntries(Object.entries(values).filter(([tag,value])=>windowTags.has(tag)&&Number.isFinite(value)).map(([tag,value])=>[tag,String(value)]));setParameterDraft(old=>({...old,...tags}));setPrepared(null);setMessage('已将所选相对能窗候选填入表单；请核对并点击“应用参数到 .win”。');};
  const parentAttempt=attempts.find(item=>item.id===parentId);
  const bandChoices=attempts.filter(item=>item.state==='COMPLETED'&&item.config?.alias===config?.alias&&project.nodes.find(node=>node.id===item.node_id)?.data.kind==='bands'&&Boolean(scfSourceId(parentAttempt))&&scfSourceId(item)===scfSourceId(parentAttempt));
  useEffect(()=>{if(bandId&&!bandChoices.some(item=>item.id===bandId)){setBandId('');setBandPath(null);setBandEnabled(false);setPrepared(null);}},[parentId,config?.alias,bandChoices.map(item=>item.id).join(',')]);
  const prepareBandPath=async()=>{if(!config||!bandId||!source)return;setBusy(true);setBusyStep('远端 PyW90 正在读取同源 DFT 能带路径');setError('');try{const proposal:BandPath=await api('/api/wannier90/band-path',{project,node_id:nodeId,parent_id:parentId,alias:config.alias,band_attempt_id:bandId});setBusyStep('路径已生成，正在写入本次 .win 草稿');const result=await api('/api/wannier90/band-path/apply',{win,proposal,path_lines:proposal.path_lines,bands_num_points:proposal.bands_num_points,enabled:true});setBandPath(proposal);setBandLines(proposal.path_lines.join('\n'));setBandPoints(String(proposal.bands_num_points));setBandEnabled(true);setBandDirty(false);setWin(result.win);setPrepared(null);setMessage('PyW90 已提供路径；bands_plot 已默认打开。请核对路径与最终 .win。');}catch(caught){setError(errorMessage(caught));}finally{setBusy(false);setBusyStep('');}};
  const applyBandPath=async(enabled=bandEnabled)=>{if(!bandPath)return;setBusy(true);setError('');try{const result=await api('/api/wannier90/band-path/apply',{win,proposal:bandPath,path_lines:bandLines.split('\n').map(line=>line.trim()).filter(Boolean),bands_num_points:Number(bandPoints),enabled});setWin(result.win);setBandEnabled(enabled);setBandDirty(false);setPrepared(null);setMessage(enabled?'能带路径已写入 .win；尚未冻结。':'本次已关闭 Wannier 能带输出。');}catch(caught){setError(errorMessage(caught));}finally{setBusy(false);}};
  const dosChoices=attempts.filter(item=>item.state==='COMPLETED'&&item.config?.alias===config?.alias&&project.nodes.find(node=>node.id===item.node_id)?.data.kind==='dos'&&Boolean(scfSourceId(parentAttempt))&&scfSourceId(item)===scfSourceId(parentAttempt));
  const eigAdvice=advice[seed+':eig'],preAdvice=advice[seed+':pre'];
  const adoption=node?.data.windowAdoption as import('./windowScan').WindowAdoption|undefined;
  const loadAdoption=async()=>{if(!adoption||!config)return;setBusy(true);setBusyStep('正在核对并载入扫描窗口草稿');setError('');try{
    const settings=await api('/api/workbench-settings');const runtime={...runtimeFor(config,profiles,settings,adoption.alias,'wannier90'),name:'wannier90'};
    const checked:Source=await api('/api/wannier90/inspect',{project,node_id:nodeId,parent_id:adoption.parent_id,alias:adoption.alias,seed:adoption.seed});
    const reference:EnergyReference=await api(`/api/wannier90/energy-reference?project_id=${encodeURIComponent(project.id)}&attempt_id=${encodeURIComponent(adoption.parent_id)}`);
    if(reference.outcar_sha256!==adoption.energy_reference.outcar_sha256||reference.fermi_eV!==adoption.energy_reference.fermi_eV)throw new Error('扫描来源费米参考已变化，请重新审查');
    const relative=relativeValues(adoption.parameters,reference.fermi_eV);setConfig(runtime);setParentId(adoption.parent_id);setSeeds([adoption.seed]);setSeed(adoption.seed);setSource(checked);setEnergyReference(reference);setWin(adoption.win);setParameterDraft(relative);setAppliedParameters(relative);setInputReason(adoption.input_reason);
    const path=adoption.band_path as BandPath|null;setBandPath(path);setBandId(path?.band_attempt_id||'');setBandEnabled(!!path);setBandLines(path?.path_lines.join('\n')||'');setBandPoints(path?String(path.bands_num_points):'');setBandDirty(false);setPrepared(null);setMessage('扫描窗口已载入新草稿；请核对并重新冻结，历史输入未修改。');
  }catch(e){setError(errorMessage(e));}finally{setBusy(false);setBusyStep('');}};
  return <section className="input-workspace"><h2>Wannier90 局域化 · {project.name}</h2><p>直接使用已完成 VASP-Wannier attempt 的一组 .win/.amn/.mmn/.eig。矩阵留在同一服务器，按来源合同核验。先核对来源和 .win，再进入运行审阅。</p>
    {view!=='run'&&adoption&&<section className="input-submit-card"><h3>已采用的能窗草稿</h3><p>{adoption.alias} · {adoption.seed} · 来源为你选择的迭代结果。</p><button className="button primary" disabled={busy||!config} onClick={()=>void loadAdoption()}>载入扫描窗口并重新审查</button></section>}
    {!parameterCatalog.length&&<p className="handoff-warning">参数目录尚未载入。<button className="button" onClick={()=>void load()}>重新载入参数目录</button></p>}
    {!embedded&&<label className="prominent-node-choice"><strong>Wannier90 节点</strong><select value={nodeId} onChange={e=>{setNodeId(e.target.value);invalidate();}}>{nodes.map(item=><option key={item.id} value={item.id}>{item.data.label}</option>)}</select></label>}
    <div hidden={view==='run'}>
    <div className="parameter-grid"><label className="field">服务器<select value={config?.alias||''} onChange={e=>selectHost(e.target.value)}>{profiles.map(item=><option key={item.alias} value={item.alias} disabled={!item.selectable}>{item.alias} · {item.scheduler}</option>)}</select></label>
    </div><label className="field">上游 VASP-Wannier attempt<select value={parentId} onChange={e=>{setParentId(e.target.value);setSeeds([]);setSeed('');setSource(null);setEnergyReference(null);setAdvice({});setBandPath(null);setBandId('');setBandEnabled(false);setPrepared(null);}}><option value="">请选择同项目、同服务器且已完成的来源</option>{candidates.map(item=><option key={item.id} value={item.id}>{item.job_id||item.id.slice(0,12)} · {item.stage_slug||'vasp-wannier'}</option>)}</select></label>
    <button className="button" disabled={!parentId||busy} onClick={()=>void run('discover')}>查找接口文件组</button>
    {seeds.length>0&&<label className="prominent-node-choice"><strong>本次 Wannier90 通道 / seed</strong><select value={seed} onChange={e=>{setSeed(e.target.value);setSource(null);setEnergyReference(null);setWin('');setParameterDraft({});setAppliedParameters({});setInputReason('');setBandPath(null);setBandId('');setBandEnabled(false);setPrepared(null);}}><option value="">请选择一组</option>{seeds.map(item=><option key={item} value={item}>{item}{item.endsWith('.1')?' · 共线通道 1':item.endsWith('.2')?' · 共线通道 2':' · 单组 / SOC spinor'}</option>)}</select></label>}
    <button className="button" disabled={!seed||busy} onClick={()=>void run('inspect')}>载入与审查 .win</button>{source&&<button className="button" disabled={busy} onClick={()=>void run('inspect',true)}>刷新远端来源</button>}
    {source&&!energyReference&&!energyLoading&&<p className="handoff-warning">.win 已载入；能量参考尚不可用。{energyError&&<>原因：{energyError}。</>}<button className="button" disabled={busy} onClick={()=>void loadEnergy()}>重试读取 OUTCAR 能量</button></p>}
    {energyReference&&<section className="input-submit-card"><h3>VASP-Wannier 能量参考</h3><p>费米能级 E_F = {energyReference.fermi_eV.toFixed(6)} eV · VBM = {energyReference.vbm_eV==null?'不可确定':energyReference.vbm_eV.toFixed(6)+' eV'} · CBM = {energyReference.cbm_eV==null?'不可确定':energyReference.cbm_eV.toFixed(6)+' eV'}</p><details className="technical-details"><summary>能量参考来源与核验</summary><p>{energyReference.remote_path} / OUTCAR</p><code>SHA-256 {energyReference.outcar_sha256}</code></details></section>}
    <section className="input-submit-card"><h3>Wannier90 参数设置</h3><p>restart 可选 default、wannierise、plot、transport，均需要同一模型的 .chk；本页的 VASP 接口来源没有检查点，设置 restart 时不能冻结新局域化。已有模型请使用结果中的“修改路径并重绘”（restart=plot）。guiding_centres 需要显式 projections；trial_step 与 fixed_step 不能同时设置。</p><p>TB2J 需要 _hr.dat + _centres.xyz，或 _tb.dat。准备新任务时可设置 write_hr=true、write_xyz=true；旧 attempt 缺文件时需另行准备，不会自动重跑。</p>{!source&&<p role="status">现在可先填写参数草稿；选择上游 attempt、查找接口文件组并载入 .win 后，再应用到本次输入。</p>}<p>{seeds.length>1?`当前编辑 ${seed||'未选通道'}；.1 和 .2 各自保存参数草稿，提交时分别生成 ${seed||'所选通道'}.win。`:''}空白表示沿用 .win 原值或程序默认值。四个能窗按 E−E_F 输入，提交时加上当前费米能级；下方 .win 原文为绝对能量。能窗的单位是 eV，收敛阈值的单位是 Å²；不自动猜测数值。</p><div className="parameter-grid">{parameterCatalog.map(item=><label className="field" key={item.tag}>{item.label} · {item.tag}{windowTags.has(item.tag)?'（相对 E_F）':''}{item.unit&&<small> / {item.unit}</small>}{item.choices?<select disabled={busy} value={parameterDraft[item.tag]||''} onChange={e=>{setParameterDraft(old=>({...old,[item.tag]:e.target.value}));setPrepared(null);}}><option value="">沿用</option>{item.choices.map(value=><option key={value} value={value}>{value}</option>)}</select>:item.type==='boolean'?<select disabled={busy} value={parameterDraft[item.tag]||''} onChange={e=>{setParameterDraft(old=>({...old,[item.tag]:e.target.value}));setPrepared(null);}}><option value="">沿用</option><option value=".true.">true</option><option value=".false.">false</option></select>:<input disabled={busy} value={parameterDraft[item.tag]||''} onChange={e=>setParameterDraft(old=>({...old,[item.tag]:e.target.value}))} placeholder="沿用原值 / 程序默认"/>}{windowTags.has(item.tag)&&parameterDraft[item.tag]?.trim()&&energyReference&&Number.isFinite(Number(parameterDraft[item.tag]))&&<small>将写入 .win：{(Number(parameterDraft[item.tag])+energyReference.fermi_eV).toFixed(6)} eV</small>}</label>)}</div><button className="button" disabled={!source||!energyReference||busy} onClick={()=>void applyParameters()}>应用参数到 .win</button></section>
    <section className="input-submit-card"><h3>PyW90 能带路径与 Wannier 插值输出</h3><p>选择与 VASP-Wannier 相同 SCF 来源的 DFT 能带，远端 PyW90 pre kpath 从该任务的 line-mode KPOINTS 提取路径。选定路径后默认启用 bands_plot，可关闭；最终内容请在下方 .win 核对。</p>{!bandChoices.length&&<p role="status" className="handoff-warning">{!parentAttempt?'请先选择上游 VASP-Wannier attempt。':!scfSourceId(parentAttempt)?'当前接口没有可确认的 SCF 来源，或历史绑定有冲突。':'当前接口没有同一 SCF、同服务器的已完成能带。SOC 与 noSOC 来源分别筛选；可切换接口来源核对。'}LORBIT 不参与能带路径来源筛选；它用于轨道投影输出。</p>}<label className="field">DFT 能带来源<select value={bandId} onChange={e=>{setBandId(e.target.value);setBandPath(null);setBandEnabled(false);setPrepared(null);}}><option value="">选择同源能带 attempt</option>{bandChoices.map(item=><option key={item.id} value={item.id}>{project.nodes.find(n=>n.id===item.node_id)?.data.label||'能带'} · {item.job_id||item.id.slice(0,12)}</option>)}</select></label><button className="button" disabled={!source||!bandId||busy} onClick={()=>void prepareBandPath()}>远端 PyW90 pre kpath 并写入 .win</button>{bandPath&&<><details className="technical-details"><summary>路径来源与核验</summary><p>{bandPath.source_path} / KPOINTS</p><p><code>KPOINTS SHA-256 {bandPath.kpoints_sha256}</code></p><p><code>路径 SHA-256 {bandPath.path_sha256}</code></p></details><label className="field">每段采样点 · bands_num_points<input type="number" min="2" value={bandPoints} onChange={e=>{setBandPoints(e.target.value);setBandDirty(true);setPrepared(null);}}/></label><label className="field">可编辑 kpoint_path<textarea rows={8} className="input-text" value={bandLines} onChange={e=>{setBandLines(e.target.value);setBandDirty(true);setPrepared(null);}}/></label><label className="field"><input type="checkbox" checked={bandEnabled} onChange={e=>void applyBandPath(e.target.checked)}/>本次生成 Wannier 插值能带</label><button className="button" disabled={busy||!bandEnabled} onClick={()=>void applyBandPath()}>应用编辑后的路径到 .win</button>{bandDirty&&<p className="handoff-warning">路径编辑尚未应用到 .win；请点击应用。</p>}</>}</section>
    {source&&<><p>来源：{source.source_path} · .win 来源：{source.win_source_name} · spinors = {source.spinors||'未显式设置'}</p><table><thead><tr><th>文件</th><th>大小</th><th>文件核验</th></tr></thead><tbody>{Object.entries(source.files).map(([name,info])=><tr key={name}><td>{name}</td><td>{(info.size_bytes/1048576).toFixed(2)} MiB</td><td>{info.sha256?<details className="technical-details"><summary>完整内容核验</summary><code>{info.sha256}</code></details>:<>轻量：大小与修改时间<br/>{info.mtime}</>}</td></tr>)}</tbody></table><label className="field">Wannier90 .win 高级编辑（只接受官方已登记 tag；VASP 生成的晶胞、原子、k 点和 spinors 保持原值）<textarea rows={18} className="input-text" value={win} onChange={e=>{setWin(e.target.value);setPrepared(null);}}/></label><label className="field">本次输入调整理由<span className="required-mark">必填</span><textarea required onBlur={flushDraft} value={inputReason} onChange={e=>{setInputReason(e.target.value);setPrepared(null);}}/></label></>}
    <details><summary>绘图与 PyW90 数值辅助（按需）</summary><InteractivePlot project={project} compact/>
    <section className="input-submit-card"><h3>PyW90 远端数值建议</h3><p>在来源服务器按需运行 PyW90 1.0.7；只读取远端文件，回传候选数值与来源记录。建议不自动写入 .win。</p><div className="parameter-grid"><label className="field">分析范围下界 / E−E_F (eV)<input type="number" value={adviceMin} onChange={e=>setAdviceMin(e.target.value)}/></label><label className="field">分析范围上界 / E−E_F (eV)<input type="number" value={adviceMax} onChange={e=>setAdviceMax(e.target.value)}/></label><label className="field">pre dos 来源<select value={dosSourceId} onChange={e=>setDosSourceId(e.target.value)}><option value="">当前 VASP-Wannier attempt</option>{dosChoices.map(item=><option value={item.id} key={item.id}>{project.nodes.find(node=>node.id===item.node_id)?.data.label||'DOS'} · {item.job_id||item.id.slice(0,12)}</option>)}</select></label></div><button className="button" disabled={!source||!energyReference||adviceBusy||!(Number(adviceMin)<Number(adviceMax))} onClick={()=>void runAdvice('eig')}>远端 eig：外窗候选</button><button className="button" disabled={!source||!energyReference||adviceBusy||!(Number(adviceMin)<Number(adviceMax))} onClick={()=>void runAdvice('pre')}>远端 pre dos：投影与冻结窗候选</button>{adviceBusy&&<p role="status">远端只读分析中…</p>}{adviceError&&<p role="alert" className="structure-error">{adviceError}</p>}{[eigAdvice,preAdvice].filter((item):item is Advice=>Boolean(item)).map(item=><div key={item.mode}><h4>{item.mode==='eig'?'eig suggest':'pre dos'} · {item.seed}</h4><p>来源 attempt {item.source_attempt_id} · E_F {item.fermi_eV.toFixed(6)} eV · 范围 {item.relative_range_eV.join(' 至 ')} eV · PyW90 {item.pyw90_version}</p><p>{item.note}</p>{item.advice.eigenval&&<p>实际能带：{item.advice.eigenval.spinors?'SOC／非共线 spinor 单通道':item.advice.eigenval.channel==='single'?'单通道':item.advice.eigenval.channel==='down'?'下自旋':'上自旋'} · {item.advice.eigenval.nkpts} 个 k 点 · {item.advice.eigenval.nbands} 条能带{item.advice.eigenval.suggested_num_wann!==undefined&&<> · 本次投影建议 {item.advice.eigenval.suggested_num_wann} 个函数（未更改 .win）</>}</p>}<details><summary>核对过的远端输入哈希</summary>{Object.entries(item.source_hashes).map(([name,hash])=><p key={name}>{name} · {hash}</p>)}</details>{item.advice.projections?.length?<p>投影数值分析：{item.advice.projections.join('；')}</p>:null}{item.advice.minimum_outer_max!==undefined&&<p>当前范围的外窗上界至少为 {item.advice.minimum_outer_max.toFixed(6)} eV（相对 E_F）。</p>}{(['outer','frozen'] as const).map(group=><div key={group}><strong>{group==='outer'?'外窗候选':'冻结窗候选'}</strong>{item.advice[group]?.length?item.advice[group]!.map((row,index)=><p key={index}>{Object.entries(row).map(([tag,value])=>`${tag}=${value.toFixed(6)}`).join(' · ')} <button className="button" onClick={()=>adoptAdvice(row)}>采用这组建议</button></p>):<p>当前范围没有可解析的{group==='outer'?'外窗':'冻结窗'}候选；不自动补值。</p>}</div>)}</div>)}</section>
    </details><details className="run-history"><summary>冻结输入版本 · {frozenRows.length}</summary><HistoryList rows={frozenRows} context={project.id+nodeId} render={row=><div className="history-row" key={row.id}><span>{row.seed} · {new Date(row.created_at).toLocaleString()} · {row.input_reason}</span><DeleteRecord kind="wannier-inputs" id={row.id} label={row.seed} onChanged={async()=>{setPrepared(null);await loadFrozen();}}/></div>}/></details>{onReview&&<div className="configuration-next"><p>当前 .win、来源与通道会带入运行审阅；资源与脚本在下一步确认。</p><button className="button primary" disabled={!source||!energyReference||!inputReason.trim()||parametersPending||bandDirty||busy} onClick={()=>void freezeConfiguration()}>冻结输入并进入运行审阅</button></div>}</div>
    <div hidden={view==='config'} className="simple-submit">
    <details className="run-history"><summary>冻结输入版本 · {frozenRows.length}</summary><HistoryList rows={frozenRows} context={project.id+nodeId} render={row=><div className="history-row" key={row.id}><span>{row.seed} · {new Date(row.created_at).toLocaleString()} · {row.input_reason}</span><DeleteRecord kind="wannier-inputs" id={row.id} label={row.seed} onChanged={async()=>{setPrepared(null);await loadFrozen();}}/></div>}/></details>
    <section className="input-submit-card"><h3>1 · 输入</h3><label>冻结 Wannier90 输入<select value={frozen} onChange={e=>{setFrozen(e.target.value);setPrepared(null);}}><option value="">请选择</option>{frozenRows.map(row=><option key={row.id} value={row.id}>{row.seed} · {new Date(row.created_at).toLocaleString()} · {row.input_reason}</option>)}</select></label><button className="text-button" onClick={onConfigure}>来源与 .win 配置</button></section>
    <ServerPanel project={project} scopeId={nodeId} program="wannier90" onTemplate={setTemplateId} config={config} profiles={profiles} probe={probe} preview={preview} node={computeNode} cores={cores} reason={resourceReason} busy={busy} canProbe={Boolean(frozen)} onConfigChange={next=>{setConfig(next);if(!sameCapacityTarget(config,next)){setProbe(null);setComputeNode('');}setPreview(null);setPrepared(null);setConfirmOpen(false);}} onNodeChange={next=>{setComputeNode(next);invalidate('preview');setConfirmOpen(false);}} onCoresChange={value=>{setCores(value);invalidate('preview');setConfirmOpen(false);}} onReasonChange={value=>{setResourceReason(value);invalidate('preview');setConfirmOpen(false);}} onSaveAndProbe={()=>void run('probe')} onProbe={()=>void run('probe')} onPreview={()=>{}}/>
    <section className="input-submit-card"><h3>4 · 提交</h3><button className="button primary" disabled={!frozen||!computeNode||busy} onClick={()=>void run('prepare')}>准备提交</button></section>
    {confirmOpen&&prepared&&<SubmitConfirmation busy={busy} script={prepared.script} onClose={()=>setConfirmOpen(false)} onConfirm={()=>{setConfirmOpen(false);void run('submit');}}><p>{prepared.config.alias} · {computeNode} · {prepared.profile?.ppn||prepared.profile?.ntasks||cores||'未确认'} 核</p><p>来源已绑定到所选冻结输入</p></SubmitConfirmation>}
    {prepared&&<section className="input-submit-card run-history"><h3>本次 attempt · {prepared.state}</h3><p>服务器：{prepared.config.alias} · 来源：{prepared.parent_attempt_id} · job ID：{prepared.job_id||'尚未取得'}</p><div className="remote-result-path"><code>{prepared.remote_path_absolute}</code><button className="button" onClick={()=>void navigator.clipboard.writeText(prepared.remote_path_absolute||'')}>复制路径</button></div><p>{prepared.note}</p>{prepared.source_file_plan&&<details><summary>接口矩阵存储策略</summary>{Object.entries(prepared.source_file_plan).map(([name,item])=><p key={name}>{name} → {item.target} · 同服务器硬链接 · {item.size_bytes} B</p>)}</details>}<details><summary>最终脚本与技术证据</summary><pre className="input-text">{prepared.script}</pre></details>{prepared.state!=='PREPARED'&&<button className="button" disabled={busy} onClick={()=>void run('refresh')}>刷新状态</button>}{prepared.evidence&&<details><summary>执行证据</summary><pre className="input-text">{JSON.stringify(prepared.evidence,null,2)}</pre></details>}</section>}
    </div>
    {message&&<p role="status">{message}</p>}{busy&&<RemoteProgress label={busyStep||'正在处理本次请求'}/>}{error&&<p role="alert" className="structure-error">{error} <button className="text-button" onClick={()=>void load()}>重试读取页面数据</button></p>}
  </section>;
}
