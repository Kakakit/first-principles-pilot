import {useEffect,useRef,useState} from 'react';
import type {Project} from './model';
import {requestJson,errorMessage} from './apiClient';
import DisclosureLabel from './DisclosureLabel';

export type SourceInput={source_attempt_id:string;node_id:string;file:string;name:string};
export type FrozenSource=SourceInput&{node_label:string;bundle_id:string;sha256:string};
type Choice={id:string;created_at:string;kind?:string;expected_outputs?:string[]};
type Props={project:Project;nodeId:string;alias:string;sources:SourceInput[];occupied:string[];
 onChange:(value:SourceInput[])=>void;onCopy:(name:string,text:string)=>void};

export default function ProgramSources({project,nodeId,alias,sources,occupied,onChange,onCopy}:Props){
 const parents=project.nodes.filter(n=>project.edges.some(e=>e.target===nodeId&&e.source===n.id));
 const [open,setOpen]=useState(false),[parent,setParent]=useState(''),[attempt,setAttempt]=useState(''),[file,setFile]=useState('');
 const [rows,setRows]=useState<Choice[]>([]),[names,setNames]=useState<string[]>([]),[error,setError]=useState(''),[copying,setCopying]=useState(false);
 const [loading,setLoading]=useState(false),[reading,setReading]=useState(false);
 const liveCopy=useRef(onCopy);
 liveCopy.current=onCopy;
 const mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return ()=>{mounted.current=false}},[]);
 const owner=parents.some(n=>n.id===parent)?parent:parents[0]?.id||'';
 useEffect(()=>{let active=true;setRows([]);setAttempt('');setError('');setLoading(false);
  if(open&&owner&&alias){setLoading(true);void requestJson('/api/program-tasks/source-runs?'+new URLSearchParams({project_id:project.id,node_id:owner,alias})).then(r=>{if(active)setRows(r)}).catch(e=>{if(active)setError(errorMessage(e))}).finally(()=>{if(active)setLoading(false)});}
  return ()=>{active=false};
 },[open,owner,project.id,alias]);
 useEffect(()=>{let active=true;setNames([]);setFile('');setReading(false);
  const run=rows.find(r=>r.id===attempt);
  if(run?.kind==='program')setNames(run.expected_outputs||[]);
  else if(run){setReading(true);void requestJson('/api/execution/'+run.id+'/artifacts/refresh',{}).then(v=>{
   if(active)setNames(v.files.map((f:{path:string})=>f.path).filter((p:string)=>!p.includes('/')&&!p.startsWith('.')&&p!=='input-manifest.sha256'))
  }).catch(e=>{if(active)setError(errorMessage(e))}).finally(()=>{if(active)setReading(false)});}
  return ()=>{active=false};
 },[attempt,rows]);
 function available(){if(occupied.includes(file)||sources.some(s=>s.name===file)){setError('已有同名文件，请先改名');return false}return true}
 async function copy(){if(!available())return;setCopying(true);setError('');
  try{const v=await requestJson('/api/program-tasks/'+attempt+'/text?'+new URLSearchParams({project_id:project.id,file}));
   if(mounted.current)liveCopy.current(file,v.text);
  }catch(e){setError(errorMessage(e))}finally{setCopying(false)}
 }
 return <details onToggle={e=>setOpen(e.currentTarget.open)}><DisclosureLabel>上游文件{sources.length?' · '+sources.length:''}</DisclosureLabel>
 {open&&<fieldset disabled={copying}>
 {sources.map((s,i)=><div className="action-row" key={i}><span>{project.nodes.find(n=>n.id===s.node_id)?.data.label||'原节点'} · {s.file} · {s.source_attempt_id.slice(0,8)}</span>
 <label>输入文件名<input value={s.name} onChange={e=>onChange(sources.map((r,j)=>i===j?{...r,name:e.target.value}:r))}/></label>
 <button className="text-button" onClick={()=>onChange(sources.filter((_,j)=>i!==j))}>移除引用</button></div>)}
 {!parents.length?<p>在画布上将计算节点的输出端连接到本节点，即可选择来源文件。</p>:!alias?<p>先选择运行服务器。</p>:<>
 <label>来源节点<select value={owner} onChange={e=>setParent(e.target.value)}>{parents.map(n=><option key={n.id} value={n.id}>{n.data.label}</option>)}</select></label>
 <label>已完成运行<select value={attempt} onChange={e=>{setError('');setAttempt(e.target.value)}}><option value="">{loading?'正在读取…':rows.length?'选择运行':'该服务器暂无已完成运行'}</option>{rows.map(r=><option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString()} · {r.id.slice(0,8)}</option>)}</select></label>
 {attempt&&<label>来源文件<select value={file} onChange={e=>{setFile(e.target.value);setError('')}}><option value="">{reading?'正在读取目录…':'选择文件'}</option>{names.map(n=><option key={n}>{n}</option>)}</select></label>}
 {file&&<div className="action-row"><button className="button" onClick={()=>{if(available()){onChange([...sources,{node_id:owner,source_attempt_id:attempt,file,name:file}]);setFile('');setError('')}}}>引用文件</button><button className="button" onClick={()=>void copy()}>复制为独立文本</button></div>}</>}
 {error&&<p role="alert">{error}</p>}</fieldset>}</details>;
}
