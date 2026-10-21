import type{ResultAttempt}from'./resultWorkspace';
import{attemptLabel}from'./humanLabels';
import ExecutionEvidence from'./ExecutionEvidence';
export default function RunRecordOverview({rows,selected,onSelect}:{rows:ResultAttempt[];selected:string;onSelect:(id:string)=>void}){
 const records=[...rows].sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||''))),current=records.find(r=>r.id===selected)||records[0];
 return <section className="run-record-overview" aria-label="当前运行记录"><div className="run-record-heading"><h2>运行记录</h2>{current&&<label>当前记录<select aria-label="监控运行记录" value={current.id} title={attemptLabel(current,undefined,true)} onChange={e=>onSelect(e.target.value)}>{records.map(r=><option key={r.id} value={r.id}>{attemptLabel(r)}</option>)}</select></label>}</div>{current?<><ExecutionEvidence attempt={current}/><div className="run-record-meta"><span>主机 <strong>{current.config?.alias||'未记录'}</strong></span><span>job ID <code>{current.job_id||'未取得 job ID'}</code></span><button className="text-button" onClick={()=>onSelect(current.id)}>查看记录与恢复操作</button></div></>:<p className="summary-muted">当前节点暂无运行记录，可在下方准备任务。</p>}</section>;
}
