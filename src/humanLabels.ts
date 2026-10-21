import type {Project} from './model';
import {statusLabel} from './statusLabels';
type LabelRecord={id?:string;node_id?:string;execution_scope?:string;owner_node_id?:string;parameter_summary?:string;case_index?:number;batch_reason?:string;batch_created_at?:string;kind?:string;stage_slug?:string;job_id?:string|null;created_at?:string;seed?:string;input_reason?:string;input_note?:string|null;chain_run_id?:string;config?:{name?:string|null;alias?:string;scheduler?:string};state?:string;display_name?:string};
const kinds:Record<string,string>={wannier90:'Wannier90',bands:'能带',dos:'DOS',scf:'SCF',relax:'结构优化','vasp-wannier':'VASP–Wannier 接口'};
export function timeLabel(value?:string){if(!value)return '时间未记录';const date=new Date(value);return Number.isFinite(date.getTime())?date.toLocaleString():'时间未记录';}
export function channelLabel(seed?:string){return seed?.endsWith('.1')?'通道 1':seed?.endsWith('.2')?'通道 2':seed?'单通道':'';}
export function attemptName(record:LabelRecord){
 const note=record.input_note?.trim();
 return note||(record.chain_run_id&&record.config?.name?.trim())||'未填写备注';
}
export function attemptLabel(record:LabelRecord,project?:Project,full=false){
 const node=project?.nodes.find(n=>n.id===(record.owner_node_id||record.execution_scope||record.node_id));
 if(record.parameter_summary)return [`${node?.data.label||record.batch_reason||'迭代'} · Case ${(record.case_index??0)+1} · ${record.parameter_summary}`,channelLabel(record.seed),record.job_id||timeLabel(record.created_at)].filter(Boolean).join(' · ');
 const name=attemptName(record).replace(/\s+/g,' '),short=Array.from(name);
 return [node?.data.label,full||short.length<=48?name:short.slice(0,48).join('')+'…',channelLabel(record.seed),timeLabel(record.created_at),record.job_id||(record.id?'attempt '+record.id.slice(0,8):'未提交'),record.state?statusLabel(record.state):''].filter(Boolean).join(' · ');
}
export function frozenLabel(record:LabelRecord,project?:Project){const node=project?.nodes.find(n=>n.id===record.node_id);return [record.input_reason||node?.data.label||'冻结输入',channelLabel(record.seed),timeLabel(record.created_at)].filter(Boolean).join(' · ');}
