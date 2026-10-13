import {calculationKind} from './scanNodes';
import {useEffect,useRef,useState} from 'react';
import type {Project} from './model';
import {requestJson,errorMessage} from './apiClient';
import {openSettings} from './submissionDefaults';
export type SourceDraft={mode:'new'|'self'|'upstream'|'manual';purpose:'charge'|'continue';read_charge?:boolean;files:'charge'|'both';attempt_id?:string;scan_batch_id?:string;scan_case_id?:string;acknowledge_differences?:boolean};
type Candidate={parameter_summary?:string;case_index?:number;batch_created_at?:string;batch_reason?:string;scan_batch_id?:string;scan_case_id?:string;id:string;alias:string;state:string;job_id:string;created_at:string;eligible:boolean;blockers:string[];differences:string[];warnings:string[];verification_status:string;verified_at?:string;structure_file?:string;observed_files:{path:string;size_bytes:number}[]};
type Resolved={parameter_source?:{parameter_summary:string;values:Record<string,string>};draft:SourceDraft;selected_attempt_id?:string;candidates:Candidate[];blockers:string[];required_files:string[];initialization:{ISTART:number;ICHARG:number};source_plan:{structure_file?:string;electronic_usage:string}};
export default function SourcePanel({project,nodeId,onChange,advanced=false,scopeId,allowSelf=true}:{scopeId?:string;allowSelf?:boolean;project:Project;nodeId:string;onChange:(value:SourceDraft)=>void;advanced?:boolean}){
 const node=project.nodes.find(n=>n.id===nodeId)!;
 const parentId=project.edges.find(e=>e.target===nodeId)?.source;
 const parent=project.nodes.find(n=>n.id===parentId);
 const scanParent=parent?.data.kind==='scan';
 const remoteParent=Boolean(parent&&parent.data.kind!=='structure');
 const fixed=['bands','dos','vasp-wannier'].includes(calculationKind(node,project));
 const value=(node.data.sourceDraft||{mode:remoteParent?'upstream':'new',purpose:'charge',files:'charge',read_charge:calculationKind(parent,project)==='scf'}) as SourceDraft;
 const [resolved,setResolved]=useState<Resolved|null>(null),[error,setError]=useState(''),[refreshing,setRefreshing]=useState(false);
 const epoch=useRef(0);
 const signature=JSON.stringify({nodes:project.nodes.map(n=>({id:n.id,data:n.data})),edges:project.edges});
 useEffect(()=>{const token=++epoch.current;setResolved(null);setError('');void requestJson('/api/calculation-sources/resolve',{project,node_id:nodeId,execution_scope:scopeId}).then(result=>{if(token===epoch.current)setResolved(result);}).catch(e=>{if(token===epoch.current)setError(errorMessage(e));});return()=>{epoch.current++;};},[signature,nodeId,scopeId]);
 useEffect(()=>{if(!node.data.sourceDraft)onChange(value);},[nodeId]);
 const change=(next:Partial<SourceDraft>)=>onChange({...value,...next,acknowledge_differences:false});
 const chosen=resolved?.candidates.find(c=>c.id===(value.attempt_id||resolved.selected_attempt_id));
 const hasSource=value.mode!=='new'||remoteParent;
 const refresh=async()=>{if(!chosen)return;const token=epoch.current;setRefreshing(true);setError('');try{const result=await requestJson('/api/calculation-sources/refresh',{project,node_id:nodeId,execution_scope:scopeId,attempt_id:chosen.id});if(token===epoch.current)setResolved(result);}catch(e){if(token===epoch.current)setError(errorMessage(e));}finally{setRefreshing(false);}};
 return <section className="source-config" aria-label="计算来源"><h4>计算来源</h4>
  <label>来源方式<select value={value.mode} onChange={e=>change({mode:e.target.value as SourceDraft['mode'],attempt_id:undefined,purpose:'charge',files:'charge',read_charge:calculationKind(parent,project)==='scf',scan_batch_id:undefined,scan_case_id:undefined})}>
   {!fixed&&<option value="new">新计算</option>}{allowSelf&&['relax','scf'].includes(calculationKind(node,project))&&<option value="self">同节点重启</option>}
   {remoteParent&&<option value="upstream">上游依赖 · {parent?.data.label}</option>}{(advanced||value.mode==='manual')&&<option value="manual">高级手动来源</option>}
  </select></label>
  {(value.mode==='self'||value.mode==='manual')&&<label>续算目的<select value={value.purpose} onChange={e=>change({purpose:e.target.value as SourceDraft['purpose']})}>
   <option value="charge">{value.mode==='self'?'读取电荷重算 · 使用该次运行 POSCAR':'读取电荷重算 · 使用项目配置结构'}</option>{calculationKind(node,project)==='relax'&&<option value="continue">继续未完成优化 · 使用上一轮 CONTCAR</option>}
  </select></label>}
  {value.mode==='upstream'&&!fixed&&<label>电子初始化<select value={(value.read_charge??calculationKind(parent,project)==='scf')||value.files==='both'?'charge':'new'} onChange={e=>change({read_charge:e.target.value==='charge',files:'charge'})}>
   <option value="new">仅继承结构 · 重新求电子态</option><option value="charge">CHGCAR 作为自洽初值</option>
  </select></label>}
  {hasSource&&<>
   {remoteParent&&!scanParent&&resolved&&!resolved.candidates.length&&<p>若上游只有迭代结果，请在画布将本节点改接对应迭代节点，再选择具体 case；普通节点只提供自己的普通任务。</p>}<label>历史来源<select value={value.attempt_id||''} onChange={e=>{const candidate=resolved?.candidates.find(c=>c.id===e.target.value);change({attempt_id:e.target.value||undefined,scan_batch_id:candidate?.scan_batch_id,scan_case_id:candidate?.scan_case_id});}}>
    <option value="">{scanParent?'请选择具体 case':'自动选择最新合格结果'}{chosen?' · '+chosen.job_id:''}</option>
    {resolved?.candidates.map(c=><option key={c.id} value={c.id} disabled={!c.eligible&&c.verification_status!=='MISSING'}>{c.parameter_summary?`${c.batch_reason||parent?.data.label||'迭代'} · ${c.batch_created_at?new Date(c.batch_created_at).toLocaleString():''} · Case ${(c.case_index??0)+1} · ${c.parameter_summary} · `:''}{c.alias} · {c.job_id||'无作业号'} · {new Date(c.created_at).toLocaleString()} · {c.eligible?c.state:c.blockers.join('；')}</option>)}
   </select></label>
   {!fixed&&value.mode!=='new'&&(value.mode!=='upstream'||(value.read_charge??calculationKind(parent,project)==='scf')||value.files==='both')&&<label>电子文件<select value={value.files} onChange={e=>change({files:e.target.value as SourceDraft['files']})}><option value="charge">仅 CHGCAR</option><option value="both">WAVECAR + CHGCAR</option></select></label>}
   <p>{value.mode==='new'?'保留相连上游的实际结构，电子态重新初始化。':value.mode==='upstream'?'relax 提供电子、离子均收敛的 CONTCAR；SCF 提供该次实际 POSCAR。':value.purpose==='continue'?'结构使用来源 CONTCAR，电子文件按所选模式读取。':value.mode==='self'?'使用所选历史任务的实际 POSCAR，与电子文件绑定同一次运行。':'使用项目配置结构和所选电子文件。'}冻结后固定具体来源。</p>
   {chosen&&<p role="status">文件核验：{chosen.verification_status==='VERIFIED'?'已核验':chosen.verification_status==='MISSING'?'确认缺文件':'待核验 · 旧清单不能证明终态文件缺失'}{chosen.verified_at?' · '+new Date(chosen.verified_at).toLocaleString():''}</p>}
   <button type="button" className="button" disabled={!chosen||refreshing} onClick={()=>void refresh()}>{refreshing?'正在读取远端文件清单…':'刷新所选来源文件'}</button>
   {chosen?.observed_files?.map(f=><p key={f.path}>{f.path} · {f.size_bytes.toLocaleString()} 字节</p>)}
   {chosen?.warnings?.map((w,i)=><p key={'warning'+i}>{w}</p>)}
   {chosen?.differences.map((d,i)=><p className="handoff-warning" key={i}>{d}</p>)}
   {chosen?.differences.length? <label className="confirmation"><input type="checkbox" checked={Boolean(value.acknowledge_differences)} onChange={e=>onChange({...value,acknowledge_differences:e.target.checked})}/>我已审阅差异，允许冻结时核对并使用此来源</label>:null}
   {resolved?.blockers.map((b,i)=><p role="alert" className="structure-error" key={i}>{b}</p>)}
  </>}
  {resolved?.parameter_source&&<details open><summary>继承 case 参数 · {resolved.parameter_source.parameter_summary}</summary><p>下游类型默认和显式覆盖优先；输入审查列出各参数实际来源。</p></details>}{resolved&&<p>本次实际读取：{resolved.required_files.length?resolved.required_files.join('、'):'配置结构'}；ISTART={resolved.initialization.ISTART}，ICHARG={resolved.initialization.ICHARG}{resolved.source_plan.electronic_usage==='fixed'?' · 固定电荷':resolved.source_plan.electronic_usage==='initial'?' · 自洽初值':' · 新电子态'}。</p>}
  {error&&<p role="alert">{error}</p>}{!advanced&&<button type="button" className="text-button" onClick={()=>openSettings('config')}>高级手动来源与参数模板</button>}
 </section>;
}
