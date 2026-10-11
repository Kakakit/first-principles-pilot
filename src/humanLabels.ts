import type {Project} from './model';
type LabelRecord={node_id?:string;execution_scope?:string;kind?:string;stage_slug?:string;job_id?:string|null;created_at?:string;seed?:string;input_reason?:string;display_name?:string};
const kinds:Record<string,string>={wannier90:'Wannier90',bands:'能带',dos:'DOS',scf:'SCF',relax:'结构优化','vasp-wannier':'VASP–Wannier 接口'};
export function timeLabel(value?:string){if(!value)return '时间未记录';const date=new Date(value);return Number.isFinite(date.getTime())?date.toLocaleString():'时间未记录';}
export function channelLabel(seed?:string){return seed?.endsWith('.1')?'通道 1':seed?.endsWith('.2')?'通道 2':seed?'单通道':'';}
export function attemptLabel(record:LabelRecord,project?:Project){const node=project?.nodes.find(n=>n.id===(record.node_id||record.execution_scope));return [node?.data.label||record.display_name||kinds[record.kind||record.stage_slug||'']||'计算记录',channelLabel(record.seed),record.job_id||timeLabel(record.created_at)].filter(Boolean).join(' · ');}
export function frozenLabel(record:LabelRecord,project?:Project){const node=project?.nodes.find(n=>n.id===record.node_id);return [record.input_reason||node?.data.label||'冻结输入',channelLabel(record.seed),timeLabel(record.created_at)].filter(Boolean).join(' · ');}
