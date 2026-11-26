import type {StructureOrigin} from './structureOrigin';
import {useRef,useState} from 'react';
import ExecutionEvidence from './ExecutionEvidence';
import {ArrowRight} from 'lucide-react';
import type {Project,Step} from './model';
import type {ResultAttempt} from './resultWorkspace';
import {formatValue,nextAction} from './resultWorkspace';
import {NodeIcon} from './nodeAppearance';
import {attemptLabel,attemptName} from './humanLabels';
import {statusLabel} from './statusLabels';
import CompletedMetrics from './CompletedMetrics';

import {casePage} from './scanCaseView';
import type {ScanCase} from './scanCaseView';

function ScanSummary({cases,attempts,onCase}:{cases:ScanCase[];attempts:ResultAttempt[];onCase:(id:string)=>void}){
 const [chosen,setChosen]=useState(''),[query,setQuery]=useState(''),[state,setState]=useState(''),[page,setPage]=useState(0);
 const view=casePage(cases,query,state,page),current=view.items.find(c=>String(c.index)===chosen)||view.items[0];
 const attempt=current&&attempts.find(a=>a.id===current.attempt_id||(current.id&&a.scan_case_id===current.id));
 return <><h3>扫描概况</h3><div className="scan-summary-counts">{[...projectTaskCounts(cases),['已取消',cases.filter(c=>c.state==='CANCELLED').length] as [string,number]].map(([label,count])=><span key={label}>{label}<b>{count}</b></span>)}</div>{!cases.length?<p className="summary-muted">尚无已冻结的批次记录</p>:<>
 <p className="summary-muted">{cases.filter(c=>['FAILED','UNKNOWN'].includes(c.state)).slice(0,3).map(c=>'Case '+(c.index+1)+' '+statusLabel(c.state)).join(' · ')}</p><p className="summary-muted">扫描维度：{[...new Set(cases.flatMap(c=>c.parameters.map(p=>p.key)))].join('、')}</p>
 <details><summary>查看扫描明细（{cases.length}）</summary><div className="case-filter"><input aria-label="查找扫描参数" placeholder="查找参数或 Case 编号" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/><select aria-label="筛选 case 状态" value={state} onChange={e=>{setState(e.target.value);setPage(0);}}><option value="">全部状态</option>{[...new Set(cases.map(c=>c.state))].map(s=><option key={s} value={s}>{statusLabel(s)}</option>)}</select></div>
 <div className="summary-case-list" aria-label="扫描 case 列表">{view.items.map(c=><button key={c.id||c.index} aria-pressed={c===current} onClick={()=>setChosen(String(c.index))}><span><strong>Case {c.index+1}</strong><span>{c.parameters.map(p=>p.key+'='+p.value).join('，')}</span></span><span>{statusLabel(c.state)}</span></button>)}{!view.total&&<p className="summary-muted">没有匹配的 case</p>}</div>
 <div className="case-pagination"><span>{view.total} 项 · {view.page+1} / {view.pages}</span><button className="icon-btn" aria-label="上一页 case" disabled={!view.page} onClick={()=>setPage(view.page-1)}>‹</button><button className="icon-btn" aria-label="下一页 case" disabled={view.page+1>=view.pages} onClick={()=>setPage(view.page+1)}>›</button></div>
 {current&&<><h3>Case {current.index+1} · 执行证据</h3><ExecutionEvidence attempt={attempt||{id:'',state:current.state}}/><p className="summary-muted">job ID：{attempt?.job_id||'未取得 job ID'}</p>{attempt&&<button className="button" onClick={()=>onCase(attempt.id)}>查看此 case 的结果与回执<ArrowRight size={16}/></button>}</>}
 </details></>}</>;

}
export function projectTaskCounts(rows:{state:string}[]){return [
 ['待提交',rows.filter(r=>['PENDING','PREPARED','RECOVERY_PENDING'].includes(r.state)).length],
 ['在途',rows.filter(r=>['SUBMITTED','QUEUED','RUNNING','DISPATCHING','HELD','EXITING','COMMAND_RUNNING'].includes(r.state)).length],
 ['已结束',rows.filter(r=>r.state==='COMPLETED').length],['失败',rows.filter(r=>r.state==='FAILED').length],['待核查',rows.filter(r=>r.state==='UNKNOWN').length],
] as [string,number][];}
export function WorkflowTaskStrip({rows,loading}:{rows:ResultAttempt[];loading:boolean}){return <div className="workflow-task-strip" aria-label="项目任务数量"><span>运行记录</span>{projectTaskCounts(rows).map(([label,count])=><span key={label}>{label}<b>{loading?'—':count}</b></span>)}</div>;}
function NodeTitle({label,readOnly,onRename}:{label:string;readOnly:boolean;onRename:(value:string,remember:boolean)=>void}){
 const [draft,setDraft]=useState<string|null>(null);
 const firstChange=useRef(true),composing=useRef(false);
 const accept=(text:string)=>{
   const value=text.trim();
   if(!readOnly&&value&&value!==label){
     onRename(value,firstChange.current);
     firstChange.current=false;
   }
 };
 return <input className="node-title-input" aria-label="节点标题" title={label} readOnly={readOnly}
   maxLength={100} value={draft??label} onFocus={()=>{firstChange.current=true;}}
   onCompositionStart={()=>{composing.current=true;}}
   onCompositionEnd={e=>{composing.current=false;accept(e.currentTarget.value);}}
   onChange={e=>{setDraft(e.target.value);if(!composing.current)accept(e.target.value);}}
   onBlur={e=>{accept(e.currentTarget.value);composing.current=false;setDraft(null);}}
   onKeyDown={e=>{if(e.key==='Enter'&&!composing.current&&!e.nativeEvent.isComposing&&e.nativeEvent.keyCode!==229)e.currentTarget.blur();}}/>;
}
export default function WorkflowSummary({project,node,rows,scan,onOpen,onRefresh,loading,failed,readOnly,onRename,onRecordChange}:{onRecordChange:(id:string)=>void;readOnly:boolean;onRename:(value:string,remember:boolean)=>void;project:Project;node:Step;rows:ResultAttempt[];scan?:ScanCase[];onOpen:(view:string,nodeId?:string,attemptId?:string)=>void;onRefresh:()=>void;loading:boolean;failed:boolean}){
 const attempts=rows.filter(a=>(a.owner_node_id||a.execution_scope||a.node_id)===node.id).sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));
 const [chosen,setChosen]=useState('');
 const current=attempts.find(a=>a.id===chosen)||attempts[0];
 const action=nextAction(current);
 const checked=current?.tracking_checked_at||current?.state_checked_at;
 const parents=project.edges.filter(e=>e.target===node.id).map(e=>project.nodes.find(n=>n.id===e.source)?.data.label).filter(Boolean);
 const structure=node.data.structure,origin=node.data.structureOrigin as StructureOrigin|undefined;
 return <div className="workflow-summary">
  <div className="workflow-node-title"><NodeIcon kind={node.data.kind} size={24}/><NodeTitle label={node.data.label} readOnly={readOnly} onRename={onRename}/></div>
  {node.data.kind==='structure'?<><div className="workflow-structure-facts"><strong>{structure?.formula||node.data.params.material||'尚未导入结构'}</strong>{structure&&<><span>{structure.atoms.length} 个原子</span><span>体积 {formatValue(structure.volume)} Å³</span><span className="summary-file">{structure.source.filename}</span></>}</div><button className="button primary" onClick={()=>onOpen('structure',node.id)}>打开结构详情<ArrowRight size={16}/></button>{origin&&<div className="structure-origin-summary"><details><summary>来源记录</summary><p>{origin.path}<br/>{origin.alias} · {origin.job_id||origin.attempt_id.slice(0,8)} · {statusLabel(origin.state)}</p>{origin.input_note&&<p>{origin.input_note}</p>}</details>{node.data.note&&<p className="structure-warning">{node.data.note}</p>}{structure?.edit&&<span>结构已编辑，原来源记录保留</span>}{origin.project_id===project.id&&project.nodes.some(n=>n.id===origin.node_id)&&<button className="text-button" onClick={()=>onOpen('results',origin.node_id,origin.attempt_id)}>查看来源任务</button>}</div>}</>
   :node.data.kind==='scan'?<><ScanSummary cases={scan||[]} attempts={attempts} onCase={id=>onOpen('results',node.id,id)}/><button className="button primary" onClick={()=>onOpen('run',node.id)}>查看扫描批次<ArrowRight size={16}/></button><button className="text-button" onClick={()=>onOpen('config',node.id)}>配置扫描参数</button></>
   :<>
    {attempts.length>1&&<label className="summary-record-select">运行历史<select aria-label="节点摘要运行记录" value={current?.id} onChange={e=>{setChosen(e.target.value);onRecordChange(e.target.value);}}>{attempts.map(a=><option key={a.id} value={a.id}>{attemptLabel(a)}</option>)}</select></label>}
    {current?<>{attempts.length===1&&current.input_note&&<p className="summary-muted">{attemptName(current)}</p>}
      {current.state==='COMPLETED'?<>
        <ExecutionEvidence attempt={current}/>
        {current.kind!=='wannier90'&&current.evidence?.vasp_timing===true&&<CompletedMetrics key={current.id} projectId={project.id} attemptId={current.id}/>}
      </>:<>
        <strong>{statusLabel(current.state)}</strong>
        <dl className="workflow-facts">
          {current.config?.alias&&<div><dt>主机</dt><dd>{current.config.alias}</dd></div>}
          {current.job_id&&<div><dt>作业号</dt><dd>{current.job_id}</dd></div>}
          {typeof checked==='string'&&<div><dt>最近核查</dt><dd>{new Date(checked).toLocaleString()}</dd></div>}
        </dl>
        {['FAILED','UNKNOWN'].includes(current.state)&&<p className="summary-warning">{String(current.tracking_error||action.reason)}</p>}
      </>}
    </>:<p className="summary-muted">{loading?'正在读取本机记录…':failed?'本机运行记录暂不可读':'尚无运行记录'}</p>}
    <button className="button primary" onClick={()=>onOpen(action.view,node.id,current?.id)}>{action.label}<ArrowRight size={16}/></button>
    {current&&<button className="text-button" onClick={()=>onOpen('config',node.id)}>打开计算配置</button>}
   </>}
  {failed&&<button className="text-button" onClick={onRefresh}>重新读取本机记录</button>}
  {(parents.length>0||origin)&&<div className="workflow-upstream"><span>{origin?'结构模式':'直接上游'}</span><strong>{origin?'独立结构副本':parents.join('、')||'独立输入结构'}</strong></div>}
 </div>;
}
