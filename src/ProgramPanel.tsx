import {useEffect,useState} from 'react';
import type {Project,Step} from './model';
import {requestJson,errorMessage} from './apiClient';
import {statusLabel} from './statusLabels';
import CommandOutput from './CommandOutput';
import CancelTask from './CancelTask';
import DisclosureLabel from './DisclosureLabel';

import ProgramSources from './ProgramSources';
import type {SourceInput,FrozenSource} from './ProgramSources';

type InputFile={name:string;text:string};
type Batch={queue?:string;node?:string;cores?:number;walltime?:string};
type Draft={sources?:SourceInput[];batch?:Batch;alias:string;command:string;files:InputFile[];outputs:string[]};
type Snapshot={sources?:FrozenSource[];batch_script?:string;command:string;files:Record<string,string>;outputs:string[]};
type Run={id:string;created_at:string;state:string;config:{alias:string;scheduler?:string};job_id?:string;remote_path_absolute:string;script:string;
 expected_outputs?:string[];evidence?:{wrapper_exit?:number;scheduler_state?:string;scheduler_exit_code?:string;outputs?:Record<string,boolean>};note?:string;tracking_error?:string};
type Host={scheduler?:string;alias:string;command_mode?:string};
const blank:Draft={alias:'',command:'',files:[],outputs:[]};

function ProgramFiles({id,names}:{id:string;names:string[]}){
 const [file,setFile]=useState(''),[text,setText]=useState(''),[error,setError]=useState(''),[rev,refresh]=useState(0);
 useEffect(()=>{let active=true;setText('');setError('');
  if(file)void requestJson('/api/execution/'+id+'/artifact-preview',{path:file,mode:'tail',max_bytes:32768}).then(v=>{if(active)setText(v.text)}).catch(e=>{if(active)setError(errorMessage(e))});
  return ()=>{active=false};
 },[id,file,rev]);
 return <section><label>查看输出<select value={file} onChange={e=>setFile(e.target.value)}><option value="">选择文件</option>{names.map(name=><option key={name}>{name}</option>)}</select></label>{file&&<><button className="button" onClick={()=>refresh(n=>n+1)}>刷新末尾 32 KiB</button><pre className="input-text">{text}</pre></>}{error&&<p role="alert">{error}</p>}</section>;
}

function useInputs(id:string,projectId:string){
 const [value,setValue]=useState<Snapshot|null>(null),[error,setError]=useState('');
 useEffect(()=>{let active=true;setValue(null);setError('');
  if(id)void requestJson('/api/program-tasks/'+id+'/inputs?project_id='+encodeURIComponent(projectId)).then(v=>{if(active)setValue(v)}).catch(e=>{if(active)setError(errorMessage(e))});
  return ()=>{active=false};
 },[id,projectId]);
 return {value,error};
}

export function ProgramRuns({projectId,nodeId,initialId='',results=false,onSelect,onResults}:{onSelect?:(id:string)=>void;onResults?:(id:string)=>void;projectId:string;nodeId:string;initialId?:string;results?:boolean}){
 const [rows,setRows]=useState<Run[]>([]),[selected,select]=useState(initialId),[record,setRecord]=useState<Run|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[loaded,setLoaded]=useState(false);
 useEffect(()=>{let active=true;
  void requestJson('/api/execution?view=summary&limit=200&project_id='+encodeURIComponent(projectId)+'&node_id='+encodeURIComponent(nodeId)).then(v=>{
   if(!active)return;
   setRows(v.items);select(id=>id||v.items[0]?.id||'');setLoaded(true);
  }).catch(e=>{if(active)setError(errorMessage(e))});return ()=>{active=false};
 },[projectId,nodeId]);
 const [reload,setReload]=useState(0);
 useEffect(()=>{if(initialId)select(initialId)},[initialId]);
 useEffect(()=>{let active=true;setRecord(null);
  if(selected)void requestJson('/api/execution/'+selected+'?project_id='+encodeURIComponent(projectId)).then(v=>{
   if(!active)return;
   if(v.kind!=='program'||v.node_id!==nodeId){setError('所选运行记录不属于此程序节点');return}
   setRecord(v);
  }).catch(e=>{if(active)setError(errorMessage(e))});return ()=>{active=false};
 },[selected,projectId,nodeId,reload]);
 const frozen=useInputs(selected,projectId);
 const sources=frozen.value?.sources||[];
 async function act(action:'refresh'|'submit'){
  if(busy||!record)return;setBusy(true);setError('');
  try{const next=await requestJson('/api/execution/'+record.id+'/'+action,{});setRecord(next);setRows(old=>old.map(r=>r.id===next.id?next:r))}
  catch(e){setError(errorMessage(e))}finally{setBusy(false)}
 }
 return <section className="input-workspace"><h2>{results?'程序结果':'自定义程序运行'}</h2>
  {rows.length>0&&<label className="workbench-field-line"><span className="field-line-name">运行记录</span><select value={selected} disabled={busy} onChange={e=>{select(e.target.value);onSelect?.(e.target.value)}}>{rows.map(r=><option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString()} · {statusLabel(r.state)}</option>)}</select></label>}
  {loaded&&!selected&&<p>此节点还没有运行记录。可在计算配置中编辑输入并准备运行。</p>}
  {(error||frozen.error)&&<p role="alert">{error||frozen.error}</p>}
  {record&&record.id===selected&&<><h3>{statusLabel(record.state)} · {record.config.alias}</h3>
   {record.job_id&&<p>{record.config.scheduler?.toUpperCase()} 作业号　{record.job_id}</p>}{frozen.value?.batch_script&&<details><DisclosureLabel>冻结的调度脚本</DisclosureLabel><pre className="input-text">{frozen.value.batch_script}</pre></details>}{record.note&&<p role="status">{record.note}</p>}<p>{record.remote_path_absolute}</p><pre className="input-text">{record.script}</pre>
   {frozen.value&&<details><DisclosureLabel>冻结文本输入 · {Object.keys(frozen.value.files).length} 个</DisclosureLabel>{Object.entries(frozen.value.files).map(([name,text])=><details key={name}><summary>{name}</summary><pre className="input-text">{text}</pre></details>)}</details>}
   {sources.length>0&&<details><DisclosureLabel>冻结的上游来源 · {sources.length}</DisclosureLabel>{sources.map(s=><details key={s.name}><summary>{s.name} ← {s.node_label} / {s.file}</summary><p>来源运行　<a href={'#/projects/'+encodeURIComponent(projectId)+'/results?'+new URLSearchParams({node:s.node_id,attempt:s.source_attempt_id})}>{s.source_attempt_id}</a></p><p>上游输入版本　<code>{s.bundle_id}</code></p><p>文件 SHA-256　<code>{s.sha256}</code></p></details>)}</details>}
   {!results&&record.state==='PREPARED'&&<button className="button primary" disabled={busy||!frozen.value} onClick={()=>void act('submit')}>确认并启动本次运行</button>}
   {record.state!=='PREPARED'&&<><button className="button" disabled={busy} onClick={()=>void act('refresh')}>核查当前状态</button>
    {!results&&<CancelTask direct={record.config.scheduler==='custom'} projectId={projectId} attemptId={record.id} state={record.state} onChanged={()=>setReload(n=>n+1)}/>}
   {record.tracking_error&&<p role="status">{record.tracking_error}</p>}
    {record.evidence?.wrapper_exit!=null&&<p>程序退出码　{record.evidence.wrapper_exit}</p>}
    {record.evidence?.scheduler_state&&<p>调度状态　{record.evidence.scheduler_state}{record.evidence.scheduler_exit_code!=null&&'　退出码 '+record.evidence.scheduler_exit_code}</p>}
    {record.config.scheduler==='custom'?<CommandOutput direct attemptId={record.id} alias={record.config.alias} attemptState={record.state} onChanged={()=>act('refresh')}/>:<ProgramFiles key={record.id} id={record.id} names={['_fpilot/program.stdout','_fpilot/program.stderr',...(record.expected_outputs||[])]}/>}
   </>}
   {onResults&&<button className="button" onClick={()=>onResults(record.id)}>查看本次程序结果</button>}
   {Boolean(record.expected_outputs?.length)&&<section><h3>预期输出</h3>{record.expected_outputs!.map(name=><p key={name}><code>{name}</code>　{record.evidence?.outputs?.[name]===true?'已生成':record.evidence?.outputs?.[name]===false?'缺少文件':'待核查'}</p>)}</section>}
  </>}
 </section>;
}

export default function ProgramPanel({project,node,view,initialId,onChange,onPrepared,onSelect,onResults}:{onSelect?:(id:string)=>void;onResults?:(id:string)=>void;project:Project;node:Step;view:'run'|'config';initialId?:string;onChange:(d:Draft)=>void;onPrepared:(key:string)=>void}){
 const draft=(node.data.programDraft as Draft|undefined)||blank;
 const [hosts,setHosts]=useState<Host[]>([]),[idx,setIdx]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [comparison,setComparison]=useState(''),[comparing,setComparing]=useState(false);
 const prior=useInputs(comparing?comparison:'',project.id);
 useEffect(()=>{let active=true;
  if(view==='config')void Promise.all([
   requestJson('/api/server'),
   requestJson('/api/execution?view=summary&limit=1&project_id='+encodeURIComponent(project.id)+'&node_id='+encodeURIComponent(node.id))
  ]).then(([server,recs])=>{if(active){setHosts(server.registered_profiles.filter((h:Host)=>h.command_mode==='direct'||['pbs','pbs/torque','torque','slurm'].includes(h.scheduler?.toLowerCase()||'')));setComparison(recs.items[0]?.id||'')}}).catch(e=>{if(active)setError(errorMessage(e))});
  return ()=>{active=false};
 },[view,project.id,node.id]);
 if(view==='run')return <ProgramRuns key={node.id+String(initialId)} projectId={project.id} nodeId={node.id} initialId={initialId} onSelect={onSelect} onResults={onResults}/>;
 const file=draft.files[idx];
 const change=(patch:Partial<Draft>)=>onChange({...draft,...patch});
 const batch=draft.batch||{};
 const host=hosts.find(h=>h.alias===draft.alias);
 const updateBatch=(patch:Batch)=>change({batch:{...batch,...patch}});
 const updateFile=(patch:Partial<InputFile>)=>change({files:draft.files.map((f,i)=>i===idx?{...f,...patch}:f)});
 function add(){
  if(draft.files.length>=64){setError('输入文件最多 64 个');return}
  let n=draft.files.length+1;while(draft.files.some(f=>f.name==='input-'+n+'.txt'))n++;
  change({files:[...draft.files,{name:'input-'+n+'.txt',text:''}]});setIdx(draft.files.length);
 }
 async function importFiles(items:File[]){
  if(!items.length)return;setBusy(true);setError('');
  try{
   if(draft.files.length+items.length>64)throw Error('输入文件最多 64 个');
   if(items.reduce((n,f)=>n+f.size,0)+draft.files.reduce((n,f)=>n+new Blob([f.text]).size,0)>2*1024*1024)throw Error('文本输入合计最多 2 MiB');
   const names=new Set(draft.files.map(f=>f.name)),files=[...draft.files];
   for(const f of items){if(names.has(f.name))throw Error('已有同名输入：'+f.name);names.add(f.name);
    const text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await f.arrayBuffer());
    if(text.includes('\0'))throw Error(f.name+' 不是文本文件');files.push({name:f.name,text})}
   change({files});setIdx(draft.files.length);
  }catch(e){setError(errorMessage(e))}finally{setBusy(false)}
 }
 async function prepare(){
  setBusy(true);setError('');
  try{const value=await requestJson('/api/program-tasks/prepare',{project,node_id:node.id});onPrepared(value.id)}
  catch(e){setError(errorMessage(e))}finally{setBusy(false)}
 }
 return <section className="input-workspace program-editor"><h2>{node.data.label}</h2><fieldset disabled={busy}>
  <label className="workbench-field-line"><span className="field-line-name">运行服务器</span><select value={draft.alias} onChange={e=>change({alias:e.target.value})}><option value="">选择服务器</option>{hosts.map(h=><option key={h.alias} value={h.alias}>{h.alias} · {h.command_mode==='direct'?'直接运行':h.scheduler?.toUpperCase()}</option>)}</select></label>
  {host&&host.command_mode!=='direct'&&<details><DisclosureLabel>调度资源 · 单节点 {batch.cores||1} 核 · {batch.walltime||'00:10:00'}</DisclosureLabel>
   <label>核数<input type="number" min={1} max={4096} value={batch.cores??1} onChange={e=>updateBatch({cores:Number(e.target.value)})}/></label>
   <label>运行时限<input value={batch.walltime??'00:10:00'} onChange={e=>updateBatch({walltime:e.target.value})}/></label>
   <label>队列 / 分区<input placeholder="站点默认" value={batch.queue||''} onChange={e=>updateBatch({queue:e.target.value})}/></label>
   <label>计算节点<input placeholder="由调度器分配" value={batch.node||''} onChange={e=>updateBatch({node:e.target.value})}/></label></details>}
  {!hosts.length&&<p>可在“全局设置 → 服务器与运行脚本”登记运行服务器。</p>}
  <label className="workbench-field-line"><span className="field-line-name">选择程序</span><select value="" onChange={e=>{const executable=e.target.value;const script=file?.name?" './"+file.name.replaceAll("'","'\\''")+"'":'';change({command:executable+script})}}><option value="">自定义启动命令</option><option value="python3">用 Python 3 运行当前文件</option><option value="bash">用 Bash 运行当前文件</option></select></label>
  <label>启动命令<textarea rows={2} className="input-text" placeholder="例如 python3 analyse.py settings.toml" value={draft.command} onChange={e=>change({command:e.target.value})}/></label>
  <div className="action-row"><h3>输入文件</h3><button className="button" onClick={add}>新建文件</button><label className="button">导入文本<input type="file" multiple onChange={e=>{void importFiles(Array.from(e.target.files||[]));e.target.value=''}}/></label></div>
  {draft.files.length>0&&<div className="program-files"><nav aria-label="输入文件">{draft.files.map((f,i)=><button type="button" className="button" aria-pressed={idx===i} key={i} onClick={()=>setIdx(i)}>{f.name||'未命名'}</button>)}</nav>
  <select className="program-file-picker" aria-label="切换输入文件" value={idx} onChange={e=>setIdx(Number(e.target.value))}>{draft.files.map((f,i)=><option key={i} value={i}>{f.name||'未命名'}</option>)}</select>
  {file&&<div><label>文件名<input value={file.name} onChange={e=>updateFile({name:e.target.value})}/></label>
   <label>文件内容<textarea spellCheck={false} rows={14} className="input-text" value={file.text} onChange={e=>updateFile({text:e.target.value})}/></label>
   <button className="text-button" onClick={()=>{change({files:draft.files.filter((_,i)=>i!==idx)});setIdx(Math.max(0,idx-1))}}>移除此输入</button></div>}</div>}
  <ProgramSources key={node.id+draft.alias} project={project} nodeId={node.id} alias={draft.alias} sources={draft.sources||[]} occupied={[...draft.files.map(f=>f.name),...draft.outputs]} onChange={sources=>change({sources})} onCopy={(name,text)=>{if([...draft.files,...(draft.sources||[])].some(f=>f.name===name)||draft.outputs.includes(name))throw Error('已有同名文件，请先改名');if(draft.files.length+(draft.sources||[]).length>=64||draft.files.reduce((size,f)=>size+new Blob([f.text]).size,new Blob([text]).size)>2*1024*1024)throw Error('文本输入合计最多 2 MiB，输入文件最多 64 个');change({files:[...draft.files,{name,text}]});setIdx(draft.files.length)}}/>
  <details><DisclosureLabel>预期输出</DisclosureLabel><label>每行一个文件名<textarea rows={3} value={draft.outputs.join('\n')} onChange={e=>change({outputs:e.target.value.split('\n')})}/></label></details>
  {comparison&&<details onToggle={e=>setComparing(e.currentTarget.open)}><DisclosureLabel>对照上一次冻结输入</DisclosureLabel>{prior.error&&<p role="alert">{prior.error}</p>}{prior.value&&<><pre>{prior.value.command}</pre>{Object.entries(prior.value.files).map(([name,text])=><details key={name}><summary>{name} · {draft.files.find(f=>f.name===name)?.text===text?'相同':draft.files.some(f=>f.name===name)?'已修改':'已移除'}</summary><pre className="input-text">{text}</pre></details>)}{draft.files.filter(f=>!Object.hasOwn(prior.value!.files,f.name)).map((f,i)=><p key={i}>{f.name} · 新增</p>)}</>}</details>}
  {error&&<p role="alert">{error}</p>}<button className="button primary" disabled={!draft.command.trim()||!hosts.some(h=>h.alias===draft.alias)} onClick={()=>void prepare()}>冻结输入并准备运行</button>
 </fieldset></section>;
}
