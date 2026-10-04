import {useEffect,useState} from 'react';
import type {Project} from './model';
import {requestJson,errorMessage} from './apiClient';
import {openSettings} from './submissionDefaults';
export type SourceDraft={mode:'new'|'self'|'upstream'|'manual';purpose:'charge'|'continue';read_charge?:boolean;files:'charge'|'both';attempt_id?:string;acknowledge_differences?:boolean};
type Candidate={id:string;alias:string;state:string;job_id:string;created_at:string;eligible:boolean;blockers:string[];differences:string[]};
type Resolved={draft:SourceDraft;selected_attempt_id?:string;candidates:Candidate[];blockers:string[]};
export default function SourcePanel({project,nodeId,onChange,advanced=false,scopeId,allowSelf=true}:{scopeId?:string;allowSelf?:boolean;project:Project;nodeId:string;onChange:(value:SourceDraft)=>void;advanced?:boolean}){
 const node=project.nodes.find(n=>n.id===nodeId)!;
 const parent=project.edges.find(e=>e.target===nodeId)?.source;
 const remoteParent=project.nodes.find(n=>n.id===parent)?.data.kind!=='structure'&&Boolean(parent);
 const value=(node.data.sourceDraft||{mode:remoteParent?'upstream':'new',purpose:'charge',files:'charge'}) as SourceDraft;
 const [resolved,setResolved]=useState<Resolved|null>(null),[error,setError]=useState('');
 const signature=JSON.stringify({nodes:project.nodes.map(n=>({id:n.id,data:n.data})),edges:project.edges});
 useEffect(()=>{let active=true;setResolved(null);setError('');void requestJson('/api/calculation-sources/resolve',{project,node_id:nodeId,execution_scope:scopeId}).then(result=>{if(active)setResolved(result);}).catch(e=>{if(active)setError(errorMessage(e));});return()=>{active=false;};},[signature,nodeId]);
 useEffect(()=>{if(!node.data.sourceDraft)onChange(value);},[nodeId]);
 const change=(next:Partial<SourceDraft>)=>onChange({...value,...next,acknowledge_differences:false});
 const chosen=resolved?.candidates.find(c=>c.id===(value.attempt_id||resolved.selected_attempt_id));
 return <section className="source-config" aria-label="计算来源"><h4>计算来源</h4><label>来源方式<select value={value.mode} onChange={e=>change({mode:e.target.value as SourceDraft['mode'],attempt_id:undefined})}>{!['bands','dos','vasp-wannier'].includes(node.data.kind)&&<option value="new">新计算</option>}{allowSelf&&['relax','scf'].includes(node.data.kind)&&<option value="self">同节点重启</option>}{remoteParent&&<option value="upstream">上游依赖 · {project.nodes.find(n=>n.id===parent)?.data.label}</option>}{(advanced||value.mode==='manual')&&<option value="manual">高级手动来源</option>}</select></label>
 {(value.mode==='self'||value.mode==='manual')&&<label>续算目的<select value={value.purpose} onChange={e=>change({purpose:e.target.value as SourceDraft['purpose']})}><option value="charge">读取电荷重算 · 保留当前结构</option>{node.data.kind==='relax'&&<option value="continue">继续未完成优化 · 使用上一轮 CONTCAR</option>}</select></label>}
 {value.mode!=='new'&&<><label>历史来源<select value={value.attempt_id||''} onChange={e=>change({attempt_id:e.target.value||undefined})}><option value="">自动选择最新合格结果{chosen?' · '+chosen.job_id:''}</option>{resolved?.candidates.map(c=><option key={c.id} value={c.id} disabled={!c.eligible}>{c.alias} · {c.job_id} · {new Date(c.created_at).toLocaleString()} · {c.eligible?c.state:c.blockers.join('；')}</option>)}</select></label>{!['bands','dos','vasp-wannier'].includes(node.data.kind)&&<label>电子文件<select value={value.files} onChange={e=>change({files:e.target.value as SourceDraft['files']})}><option value="charge">仅 CHGCAR</option><option value="both">WAVECAR + CHGCAR</option></select></label>}<p>{value.mode==='upstream'?'结构和所需文件来自已连接上游。':value.purpose==='continue'?'结构使用来源 CONTCAR，电子文件按所选模式读取。':'结构保留当前配置，仅读取所选电子文件。'}冻结后固定具体来源。</p>{chosen?.differences.map((d,i)=><p className="handoff-warning" key={i}>{d}</p>)}{chosen?.differences.length? <label className="confirmation"><input type="checkbox" checked={Boolean(value.acknowledge_differences)} onChange={e=>onChange({...value,acknowledge_differences:e.target.checked})}/>我已审阅差异，允许冻结时核对并使用此来源</label>:null}{resolved?.blockers.map((b,i)=><p role="alert" className="structure-error" key={i}>{b}</p>)}</>}
 {error&&<p role="alert">{error}</p>}{!advanced&&<button className="text-button" onClick={()=>openSettings('config')}>高级手动来源与参数模板</button>}
 </section>;
}
