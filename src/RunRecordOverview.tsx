import {statusLabel} from './statusLabels';
import DisclosureLabel from './DisclosureLabel';
import RecordPlaceholder from './RecordPlaceholder';
import type {ResultAttempt} from './resultWorkspace';
import ExecutionEvidence from './ExecutionEvidence';
import RunRecordSelect from './RunRecordSelect';
import {commandMode,directProcessLabel} from './executionUi';

type Props={
 rows:ResultAttempt[];selected:string;onSelect:(id:string)=>void;nodeLabel:string;
 onRefresh:()=>void;loading:boolean;failed:boolean;previewOnly:boolean;detail?:ResultAttempt|null;
};

export default function RunRecordOverview({rows,selected,onSelect,nodeLabel,onRefresh,loading,failed,previewOnly,detail}:Props){
 const records=[...rows].sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));
 const current=detail?.id===selected?detail:records.find(r=>r.id===selected);
 const direct=commandMode(current?.config)==='direct';
 const completed=current?.state==='COMPLETED';
 const attention=current?.state==='UNKNOWN'||current?.state==='FAILED'||current?.evidence?.ediff_reached===false;
 const checked=typeof current?.state_checked_at==='string'?current.state_checked_at:'';
 const events=[
  {time:current?.created_at,text:'创建运行记录'},
  {time:current?.dispatch_started_at,text:'开始派发'},
  {time:checked,text:'核查运行状态'},
 ].filter((event):event is {time:string;text:string}=>typeof event.time==='string'&&Number.isFinite(Date.parse(event.time)));
 return <section data-action-field="run.history" className="run-record-overview" aria-label="当前运行记录">
  <div className="run-record-heading">
   <div><small>运行记录</small><h2>{nodeLabel}</h2></div>
   <RunRecordSelect disabled={previewOnly} rows={records} selected={selected} onSelect={onSelect} label="当前记录" ariaLabel="监控运行记录"/>
   <button className="button" disabled={loading} onClick={onRefresh}>{loading?(current?'更新中…':'正在读取…'):'刷新运行记录'}</button>
  </div>
  {current?<><div className="run-state" data-state={current.state}><span aria-hidden="true">●</span> {completed?(direct?'执行结束':'调度结束'):statusLabel(current.state)}</div>
   <dl className="monitor-facts">
    <div><dt>{direct?'直接进程':'作业号'}</dt><dd>{direct?directProcessLabel(current.state,current.direct_runtime):current.job_id||'尚未取得'}</dd></div>
    <div><dt>服务器</dt><dd>{current.config?.alias||'未记录'}</dd></div>
    <div><dt>计算节点</dt><dd>{String(current.node||'未记录')}</dd></div>
    <div><dt>最近核查</dt><dd>{checked?new Date(checked).toLocaleString():'尚无记录'}</dd></div>
   </dl>
   {events.length>0&&<ol className="monitor-events">{events.map(event=><li key={event.text}><time dateTime={event.time}>{new Date(event.time).toLocaleString()}</time>{event.text}</li>)}</ol>}
   <details key={current.id} className="workbench-disclosure" open={attention||undefined}><DisclosureLabel title="执行与收敛证据"/><ExecutionEvidence attempt={current}/></details>
  </>:failed?null:loading?<RecordPlaceholder label="正在读取运行记录"/>:<p className="summary-muted">{records.length?'请选择要查看的运行记录。':'当前节点暂无运行记录，可在下方准备任务。'}</p>}
 </section>;
}
