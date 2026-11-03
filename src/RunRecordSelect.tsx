import type {ResultAttempt} from './resultWorkspace';
import {attemptLabel} from './humanLabels';
import {statusLabel} from './statusLabels';

export function recordLabel(a:ResultAttempt){
 return a.parameter_summary?`${a.batch_reason||'迭代'} · Case ${Number(a.case_index)+1} · ${a.parameter_summary} · ${a.created_at?new Date(a.created_at).toLocaleString():'时间未记录'} · ${a.job_id||'未提交'} · ${statusLabel(a.state)}`:attemptLabel(a);
}
export default function RunRecordSelect({rows,selected,onSelect,label='运行记录',ariaLabel='运行记录'}:{rows:ResultAttempt[];selected:string;onSelect:(id:string)=>void;label?:string;ariaLabel?:string}){
 const current=rows.find(row=>row.id===selected);
 return <label>{label}<select aria-label={ariaLabel} value={selected} title={current?attemptLabel(current,undefined,true):undefined} onChange={e=>onSelect(e.target.value)}><option value="">选择运行记录</option>{rows.map(row=><option key={row.id} value={row.id} title={attemptLabel(row,undefined,true)}>{recordLabel(row)}</option>)}</select></label>;
}
