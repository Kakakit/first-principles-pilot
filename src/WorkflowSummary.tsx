import {useEffect,useRef,useState} from 'react';
import ExecutionEvidence from './ExecutionEvidence';
import {ArrowRight} from 'lucide-react';
import type {Project,Step} from './model';
import type {ResultAttempt,ResultSummary,Scalar} from './resultWorkspace';
import {formatValue} from './resultWorkspace';
import {NodeIcon} from './nodeAppearance';
import {attemptLabel,attemptName} from './humanLabels';
import {statusLabel} from './statusLabels';
import {errorMessage,requestJson} from './apiClient';

import {casePage} from './scanCaseView';
import type {ScanCase} from './scanCaseView';
const summaries=new Map<string,{version:string;value:ResultSummary}>();
function ScanSummary({cases,attempts,onCase}:{cases:ScanCase[];attempts:ResultAttempt[];onCase:(id:string)=>void}){
 const [chosen,setChosen]=useState(''),[query,setQuery]=useState(''),[state,setState]=useState(''),[page,setPage]=useState(0);
 const view=casePage(cases,query,state,page),current=view.items.find(c=>String(c.index)===chosen)||view.items[0];
 const attempt=current&&attempts.find(a=>a.id===current.attempt_id||(current.id&&a.scan_case_id===current.id));
 return <><h3>扫描概况</h3><div className="scan-summary-counts">{[...projectTaskCounts(cases),['已取消',cases.filter(c=>c.state==='CANCELLED').length] as [string,number]].map(([label,count])=><span key={label}>{label}<b>{count}</b></span>)}</div>{!cases.length?<p className="summary-muted">尚无已冻结的批次记录</p>:<>
 <p className="summary-muted">扫描维度：{[...new Set(cases.flatMap(c=>c.parameters.map(p=>p.key)))].join('、')}</p>
 <div className="case-filter"><input aria-label="查找扫描参数" placeholder="查找参数或 Case 编号" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/><select aria-label="筛选 case 状态" value={state} onChange={e=>{setState(e.target.value);setPage(0);}}><option value="">全部状态</option>{[...new Set(cases.map(c=>c.state))].map(s=><option key={s} value={s}>{statusLabel(s)}</option>)}</select></div>
 <div className="summary-case-list" aria-label="扫描 case 列表">{view.items.map(c=><button key={c.id||c.index} aria-pressed={c===current} onClick={()=>setChosen(String(c.index))}><span><strong>Case {c.index+1}</strong><span>{c.parameters.map(p=>p.key+'='+p.value).join('，')}</span></span><span>{statusLabel(c.state)}</span></button>)}{!view.total&&<p className="summary-muted">没有匹配的 case</p>}</div>
 <div className="case-pagination"><span>{view.total} 项 · {view.page+1} / {view.pages}</span><button className="icon-btn" aria-label="上一页 case" disabled={!view.page} onClick={()=>setPage(view.page-1)}>‹</button><button className="icon-btn" aria-label="下一页 case" disabled={view.page+1>=view.pages} onClick={()=>setPage(view.page+1)}>›</button></div>
 {current&&<><h3>Case {current.index+1} · 执行证据</h3><ExecutionEvidence attempt={attempt||{id:'',state:current.state}}/><p className="summary-muted">job ID：{attempt?.job_id||'未取得 job ID'}</p>{attempt&&<button className="button" onClick={()=>onCase(attempt.id)}>查看此 case 的结果与回执<ArrowRight size={16}/></button>}</>}
 </>}</>;

}
export function projectTaskCounts(rows:{state:string}[]){return [
 ['待提交',rows.filter(r=>['PENDING','PREPARED','RECOVERY_PENDING'].includes(r.state)).length],
 ['在途',rows.filter(r=>['SUBMITTED','QUEUED','RUNNING','DISPATCHING','HELD','EXITING','COMMAND_RUNNING'].includes(r.state)).length],
 ['已结束',rows.filter(r=>r.state==='COMPLETED').length],['失败',rows.filter(r=>r.state==='FAILED').length],['待核查',rows.filter(r=>r.state==='UNKNOWN').length],
] as [string,number][];}
export function WorkflowTaskStrip({rows,loading}:{rows:ResultAttempt[];loading:boolean}){return <div className="workflow-task-strip" aria-label="项目任务数量"><span>运行记录</span>{projectTaskCounts(rows).map(([label,count])=><span key={label}>{label}<b>{loading?'—':count}</b></span>)}</div>;}
export default function WorkflowSummary({project,node,rows,scan,onOpen,onRefresh,loading,failed}:{project:Project;node:Step;rows:ResultAttempt[];scan?:ScanCase[];onOpen:(view:string,nodeId?:string,attemptId?:string)=>void;onRefresh:()=>void;loading:boolean;failed:boolean}){
 const attempts=rows.filter(a=>(a.owner_node_id||a.execution_scope||a.node_id)===node.id).sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));
 const [chosen,setChosen]=useState('');
 const current=attempts.find(a=>a.id===chosen)||attempts[0];
 const key=current?`${project.id}:${current.id}`:'';
 const version=JSON.stringify([current?.state,current?.evidence,current?.updated_at,current?.bundle_id]);
 const [result,setResult]=useState<{key:string;version:string;value:ResultSummary}|null>(null),[error,setError]=useState(''),[retry,setRetry]=useState(0);
 const summary=result?.key===key&&result.version===version?result.value:null;
 const selection=useRef(key);selection.current=key;
 useEffect(()=>{
   setError('');if(!current||node.data.kind==='scan'||node.data.kind==='structure')return;
   const cached=summaries.get(key);if(!retry&&cached?.version===version){setResult({key,version,value:cached.value});return;}
   const controller=new AbortController();
   void requestJson(`/api/results/${encodeURIComponent(current.id)}/summary?project_id=${encodeURIComponent(project.id)}`,undefined,undefined,{signal:controller.signal}).then(value=>{
     if(controller.signal.aborted||selection.current!==key)return;
     summaries.delete(key);summaries.set(key,{version,value});if(summaries.size>32)summaries.delete(summaries.keys().next().value!);
     setResult({key,version,value});
   }).catch(caught=>{if(!controller.signal.aborted&&selection.current===key)setError(errorMessage(caught));});
   return()=>controller.abort();
 },[key,version,retry,node.data.kind]);
 const evidence=summary?.evidence||current?.evidence||{};
 const parents=project.edges.filter(e=>e.target===node.id).map(e=>project.nodes.find(n=>n.id===e.source)?.data.label).filter(Boolean);
 const structure=node.data.structure;
 return <div className="workflow-summary">
  <div className="workflow-node-title"><NodeIcon kind={node.data.kind} size={24}/><h2>{node.data.label}</h2></div>
  {node.data.kind==='structure'?<><div className="workflow-structure-facts"><strong>{structure?.formula||node.data.params.material||'尚未导入结构'}</strong>{structure&&<><span>{structure.atoms.length} 个原子</span><span>体积 {formatValue(structure.volume)} Å³</span><span className="summary-file">{structure.source.filename}</span></>}</div><button className="button primary" onClick={()=>onOpen('structure',node.id)}>打开结构详情<ArrowRight size={16}/></button></>
   :node.data.kind==='scan'?<><ScanSummary cases={scan||[]} attempts={attempts} onCase={id=>onOpen('results',node.id,id)}/><button className="button primary" onClick={()=>onOpen('run',node.id)}>查看扫描批次<ArrowRight size={16}/></button></>
   :<><label className="summary-record-select">运行记录<select aria-label="节点摘要运行记录" value={current?.id||''} title={current?attemptLabel(current,undefined,true):''} onChange={e=>{setChosen(e.target.value);setRetry(0);}}>{!current&&<option value="">{loading?'正在读取本机记录':'尚无运行记录'}</option>}{attempts.map(a=><option key={a.id} value={a.id}>{attemptLabel(a)}</option>)}</select></label>
   {current?<><ExecutionEvidence attempt={current} evidence={evidence}/><dl className="workflow-facts"><div><dt>{current.input_note?'冻结备注':current.chain_run_id?'作业名称':'冻结备注'}</dt><dd>{attemptName(current)}</dd></div><div><dt>主机</dt><dd>{current.config?.alias||'未记录'}</dd></div><div><dt>job ID</dt><dd><code>{current.job_id||'未取得 job ID'}</code></dd></div></dl><div className="workflow-metrics">{([['energy','总能','eV/cell'],['magnetization','磁矩','μB/cell'],['elapsed','用时','s']] as const).map(([metric,label,unit])=>{const m=summary?.metrics?.[metric] as Scalar;return <div key={metric}><span>{label}</span><strong>{m?formatValue(m.value):'—'}</strong><small>{m?.unit||unit}</small></div>;})}</div>{!summary?.metrics&&<p className="summary-muted">尚无已缓存指标</p>}{current.state==='UNKNOWN'&&<p className="summary-warning">提交结果待核查，原记录保持。</p>}<button className="button" onClick={()=>onOpen('results',node.id,current.id)}>查看完整结果与回执<ArrowRight size={16}/></button></>:<p className="summary-muted">当前节点尚未准备或提交任务。</p>}
   <button className="button primary" onClick={()=>onOpen('config',node.id)}>打开计算配置<ArrowRight size={16}/></button></>}
  {error&&<div className="summary-warning" role="alert">{error}<button className="text-button" onClick={()=>setRetry(n=>n+1)}>重试本机摘要</button></div>}
  {failed&&<button className="text-button" onClick={onRefresh}>重新读取本机记录</button>}
  <div className="workflow-upstream"><span>直接上游</span><strong>{parents.join('、')||'独立输入结构'}</strong></div>
 </div>;
}
