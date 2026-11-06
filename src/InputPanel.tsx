import {calculationKind} from './scanNodes';
import HistoryList from './HistoryList';
import {DeleteRecord,RecycleBin} from './RecordCleanup';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {requestJson,errorMessage,requestTimeouts} from './apiClient';
import {useRequestScope} from './useRequestScope';
import RemoteProgress from './RemoteProgress';
import PotcarLibrary from './PotcarLibrary';
import SymmetryAnalysis from './SymmetryAnalysis';
import KpointsSuggestion from './KpointsSuggestion';
import {upstreamStructure,effectiveParameters} from './parameters';
import type {Project} from './model';
type Draft={grid?:string;center?:string;path?:string;line_points?:string;potcar_id?:string;potcar_provenance_id?:string;reason?:string;projections?:string;path_structure_hash?:string;path_source_attempt_id?:string;path_source_file?:string;kmesh_info?:string};
type ScfAttempt={id:string;node_id:string;owner_node_id?:string;execution_scope?:string;state:string;job_id?:string;parameter_summary?:string;config:{alias:string};evidence?:{ediff_reached?:boolean}};
type Report={errors:string[];warnings:string[];dependencies:{node_id:string;file:string;check:string}[];files:Record<string,string>;ready:boolean;incar_tags?:Record<string,{value:string;source:string;validation:string}>;parameter_catalog?:{revision:string;source:string;retrieved:string};species?:string[];potcar?:{sha256:string;records:{element:string;titel:string;enmax?:number}[]};potcar_source?:string};
const post=requestJson;
function PreviewStructureAnalysis({poscar,projectId}:{poscar:string;projectId:string}){
 const [open,setOpen]=useState(false);
 return <details className="preview-structure-analysis" onToggle={e=>setOpen(e.currentTarget.open)}><summary>可选：分析当前预览结构</summary>{open&&<SymmetryAnalysis poscar={poscar} projectId={projectId}/>}</details>;
}
export default function InputPanel({project,initialNodeId,onDraft,onSharedPotcar,embedded=false,onReview,batchBaseline=false}:{batchBaseline?:boolean;embedded?:boolean;onReview?:(bundleId:string)=>void;project:Project;initialNodeId:string|null;onDraft:(nodeId:string,update:(draft:Draft)=>Draft)=>void;onSharedPotcar:(id:string,provenanceId?:string)=>void}){
 const nodes=project.nodes.filter(n=>['relax','scf','bands','dos','vasp-wannier'].includes(calculationKind(n,project)||''));
 const [selected,setSelected]=useState(initialNodeId||nodes[0]?.id||'');const node=nodes.find(n=>n.id===selected)||nodes[0];
 const draft=(node?.data.inputDraft||{}) as Draft;
 const [report,setReport]=useState<Report|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[bundle,setBundle]=useState<{id:string;download:string;reason?:string}|null>(null);
 const [reviewed,setReviewed]=useState(false);
 const [frozenDifferences,setFrozenDifferences]=useState<{field:string;frozen:unknown;draft:unknown}[]>([]);
 const [potcarScope,setPotcarScope]=useState<'shared'|'node'>('shared');
 const isBands=calculationKind(node,project)==='bands';
 const parentIds=project.edges.filter(edge=>edge.target===node?.id).map(edge=>edge.source).sort();
 const sourceKey=JSON.stringify([project.id,node?.id,isBands,parentIds]);
 const requests=useRequestScope(sourceKey);
 const [scfRead,setScfRead]=useState<{key:string;attempts:ScfAttempt[];error?:string}|null>(null);
 const scfAttempts=scfRead?.key===sourceKey?scfRead.attempts:[];
 const scfError=scfRead?.key===sourceKey?scfRead.error:undefined;
 const [pathChoice,setPathChoice]=useState<{key:string;id:string}|null>(null);
 const [pathBusy,setPathBusy]=useState(false);
 const chosenPath=pathChoice?.key===sourceKey?pathChoice.id:draft.path_source_attempt_id;
 const pathAttemptId=scfAttempts.some(attempt=>attempt.id===chosenPath)?chosenPath||'':'';
 const [history,setHistory]=useState<{id:string;download:string;reason:string;status:string;node_id?:string}[]>([]);
 const loadHistory=async()=>setHistory(await post(`/api/inputs/history?project_id=${encodeURIComponent(project.id)}`));
 const inputRemoved=async(id:string)=>{if(bundle?.id===id)setBundle(null);await loadHistory();};
 useEffect(()=>{const changed=(event:Event)=>{const {kind,id,action}=(event as CustomEvent).detail;if(kind!=='inputs')return;if(action!=='restore'&&bundle?.id===id)setBundle(null);void loadHistory().catch(e=>setError(errorMessage(e)));};window.addEventListener('fpilot:history-changed',changed);return()=>window.removeEventListener('fpilot:history-changed',changed);},[project.id,bundle?.id]);
 const epoch=useRef(0);
 useLayoutEffect(()=>()=>{epoch.current++;requests.cancel('band-path');},[project,node?.id,requests]);
 useEffect(()=>{setReport(null);setReviewed(false);setError('');setPathBusy(false);},[project,node?.id,requests]);
 useEffect(()=>{setBundle(null);setFrozenDifferences([]);void loadHistory().catch(e=>setError(errorMessage(e)));},[project.id,node?.id]);
 const comparedBundle=bundle?.id||history.find(h=>h.node_id===node?.id)?.id;
 useEffect(()=>{let active=true;setFrozenDifferences([]);if(comparedBundle&&node)void post('/api/inputs/'+comparedBundle+'/draft-differences',{project,node_id:node.id}).then(r=>{if(active)setFrozenDifferences(r.differences);}).catch(()=>{});return()=>{active=false;};},[comparedBundle,project,node?.id]);
 const loadScfAttempts=async()=>{
  const request=requests.start('scf');
  setScfRead(null);
  try{
   const rows:ScfAttempt[]=parentIds.length?await requestJson(`/api/execution?project_id=${encodeURIComponent(project.id)}`,undefined,undefined,{signal:request.signal}):[];
   const parents=new Set(parentIds);
   if(request.current())setScfRead({key:sourceKey,attempts:rows.filter(attempt=>
    parents.has(attempt.owner_node_id||attempt.execution_scope||attempt.node_id)&&attempt.state==='COMPLETED'&&attempt.evidence?.ediff_reached)});
  }catch(error){if(request.current())setScfRead({key:sourceKey,attempts:[],error:errorMessage(error)});}
 };
 useEffect(()=>{if(isBands)void loadScfAttempts();},[sourceKey]);
 const change=(key:keyof Draft,value:string)=>onDraft(node.id,current=>({...current,...(key==='potcar_id'?{potcar_provenance_id:undefined}:{}),...(['grid','center'].includes(key)?{kmesh_info:undefined}:{}),[key]:value}));
 const run=async(frozen=false)=>{if(!node)return;setBusy(true);setError('');const token=epoch.current;try{const data=await post(frozen?'/api/inputs/freeze':'/api/inputs/preview',{project,node_id:node.id,reason:draft.reason||''});if(token===epoch.current){if(frozen)setBundle(data);else {setReport(data);setReviewed(false);}}}catch(e){if(token===epoch.current)setError(errorMessage(e));}finally{setBusy(false);}};
 const generatePath=async()=>{
  if(!node||!pathAttemptId)return;
  const token=epoch.current;
  const request=requests.start('band-path');
  setPathBusy(true);
  setError('');
  try{
   const data=await post(`/api/execution/${encodeURIComponent(pathAttemptId)}/band-path`,{project_id:project.id},undefined,{signal:request.signal,timeoutMs:requestTimeouts.remote});
   if(!request.current()||token!==epoch.current)return;
   onDraft(node.id,current=>({...current,path:data.path,path_structure_hash:data.structure_hash,
    path_source_attempt_id:pathAttemptId,path_source_file:data.source_file,line_points:current.line_points||'40'}));
  }catch(error){if(request.current()&&token===epoch.current)setError(errorMessage(error));}
  finally{if(request.current())setPathBusy(false);}
 };
 const species:string[]=[];if(node)upstreamStructure(project,node)?.atoms.forEach(a=>{if(species.at(-1)!==a.element)species.push(a.element);});
 const upload=async(files:File[])=>{if(!files.length||!node)return;setBusy(true);setError('');const token=epoch.current;try{if(files.reduce((n,f)=>n+f.size,0)>32*1024*1024)throw new Error('POTCAR 总上限 32 MiB');const encoded=[];for(const file of files){const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(const b of bytes)binary+=String.fromCharCode(b);encoded.push(btoa(binary));}const structure=upstreamStructure(project,node);const species:string[]=[];structure?.atoms.forEach(a=>{if(species.at(-1)!==a.element)species.push(a.element);});const data=files.length===1?await post('/api/potcars',{base64:encoded[0]}):await post('/api/potcars/assemble',{files:encoded,species});if(token===epoch.current){if(potcarScope==='shared')onSharedPotcar(data.id);else change('potcar_id',data.id);}}catch(e){if(token===epoch.current)setError(errorMessage(e));}finally{setBusy(false);}};
 return <section className="input-workspace"><h2>{embedded?"k 点、赝势与输入审查":"生成与冻结输入 · "+project.name}</h2><fieldset disabled={busy||pathBusy||!node}>{!embedded&&<label className="prominent-node-choice"><strong>当前计算节点：{node?.data.label||'未选择'}</strong><select value={node?.id||''} onChange={e=>setSelected(e.target.value)}>{nodes.map(n=><option key={n.id} value={n.id}>{n.data.label}</option>)}</select></label>}{isBands?<><label className="prominent-node-choice">路径来源 · 已完成的上游 SCF attempt<select value={pathAttemptId} disabled={!scfAttempts.length} onChange={e=>setPathChoice({key:sourceKey,id:e.target.value})}><option value="">请选择</option>{scfAttempts.map(a=><option key={a.id} value={a.id}>{a.parameter_summary&&a.parameter_summary+' · '}{a.config.alias} · {a.job_id||'未提交'}</option>)}</select></label>
 {scfRead?.key!==sourceKey?<p role="status">正在读取上游 SCF 记录…</p>:scfError?<div><p role="alert" className="structure-error">上游 SCF 记录读取失败。{scfError}</p><button className="button" type="button" onClick={()=>void loadScfAttempts()}>重试读取 SCF 记录</button></div>:!scfAttempts.length?<p>当前上游没有已完成且电子收敛的 SCF 记录。 <button className="text-button" type="button" onClick={()=>void loadScfAttempts()}>刷新记录</button></p>:null}
 <p>路径结构：{draft.path_source_attempt_id? `SCF 实际 POSCAR · ${scfAttempts.find(a=>a.id===draft.path_source_attempt_id)?.job_id||'来源已记录'}`:'尚未读取 SCF 实际 POSCAR'}</p><button className="button primary" type="button" disabled={!pathAttemptId} onClick={()=>void generatePath()}>{pathBusy?'正在读取 SCF 并生成路径…':'读取 SCF 实际 POSCAR 并生成路径'}</button><p>路径来自所选 SCF 的最终结构，可在下方删改；更换 SCF attempt 后需重新生成和冻结。</p><label>每段点数<input value={draft.line_points||''} onChange={e=>change('line_points',e.target.value)} placeholder="明确设置"/></label><label>路径端点（倒空间分数坐标；每两行一段，可附标签）<textarea rows={6} value={draft.path||''} onChange={e=>change('path',e.target.value)} placeholder="由当前晶胞确定路径，不自动套用 Si 路径"/></label></>:<><label>规则网格（沿三个倒格矢方向）<input value={draft.grid||''} onChange={e=>change('grid',e.target.value)} placeholder="例如 6 6 6；需要收敛测试"/></label><label>网格中心（附加位移固定为 0 0 0）<select value={draft.center||''} onChange={e=>change('center',e.target.value)}><option value="">请选择</option><option>Gamma</option><option value="Monkhorst-Pack">Monkhorst-Pack（MP）</option></select></label>{node&&<KpointsSuggestion key={project.id+node.id} project={project} node={node} onApply={(grid,center,kmesh_info)=>onDraft(node.id,current=>({...current,grid,center,kmesh_info}))}/>}</>}{calculationKind(node,project)==='vasp-wannier'&&<p>Wannier 元素与轨道投影请在本页“物理参数与继承”中设置；当前选择：{draft.projections||'尚未设置'}。</p>}<label>POTCAR 保存范围<select value={potcarScope} onChange={e=>setPotcarScope(e.target.value as 'shared'|'node')}><option value="shared">项目共享默认</option><option value="node">仅当前节点覆盖</option></select></label><p>实际使用：{draft.potcar_id?'节点覆盖':project.sharedPotcar?.id?'项目共享':'未选择'}</p>{project.sharedPotcar&&<button className="button" type="button" onClick={()=>onSharedPotcar('')}>清除项目共享 POTCAR</button>}{node&&<PotcarLibrary key={node.id+JSON.stringify(species)+(effectiveParameters(project,node).XC_FUNCTIONAL?.value||'')} species={species} functional={effectiveParameters(project,node).XC_FUNCTIONAL?.value||null} onApply={(id,receiptId)=>potcarScope==='shared'?onSharedPotcar(id,receiptId):onDraft(node.id,current=>({...current,potcar_id:id,potcar_provenance_id:receiptId}))}/>}<label>备用手动导入 POTCAR（可多选不同元素，自动按结构顺序装配）<input type="file" multiple onChange={e=>{void upload(Array.from(e.target.files||[]));e.target.value='';}}/></label>{draft.potcar_id&&<button className="button" type="button" onClick={()=>onDraft(node.id,current=>({...current,potcar_id:undefined,potcar_provenance_id:undefined}))}>清除节点 POTCAR 覆盖</button>}<label>本次选择 / 修改理由<span className="required-mark">必填</span><textarea required aria-required="true" maxLength={2000} value={draft.reason||''} onChange={e=>change('reason',e.target.value)} placeholder="说明参数、k 点及赝势选择；冻结时必填"/></label><button className="button primary" onClick={()=>void run()}>审查并预览</button></fieldset>{frozenDifferences.length>0&&<div className="handoff-warning" role="status"><strong>当前草稿与冻结计划不同</strong>{frozenDifferences.map(d=><p key={d.field}>{d.field}：{typeof d.frozen==='object'?'旧来源合同':String(d.frozen??'未设置')} → {typeof d.draft==='object'?'当前来源合同':String(d.draft??'未设置')}</p>)}<p>准备运行仍使用已选冻结版本；请审查当前草稿并重新冻结以应用这些修改。</p></div>}{busy&&<RemoteProgress label="正在生成并审查当前节点输入"/>}{error&&<p role="alert" className="structure-error">{error}</p>}{report&&<><h3>{report.errors.length?'需要修正':report.ready?'输入已装配 · 尚需执行前审查':'输入配方 · 等待上游产物'}</h3>{report.errors.map((e,i)=><p className="structure-error" key={i}>{e}</p>)}{report.warnings.map((w,i)=><p key={i}>提示：{w}</p>)}{report.dependencies.map((d,i)=><p key={i}>待接入 {d.file}（{project.nodes.find(n=>n.id===d.node_id)?.data.label}）：{d.check}</p>)}{report.potcar&&<><h3>物种与赝势映射 · {report.potcar_source}</h3><p>POSCAR 物种块顺序：{report.species?.join(' → ')}</p><table><thead><tr><th>物种</th><th>TITEL</th><th>ENMAX / eV</th></tr></thead><tbody>{report.potcar.records.map((r,i)=><tr key={i}><td>{r.element}</td><td>{r.titel}</td><td>{r.enmax??'未知'}</td></tr>)}</tbody></table></>}{report.incar_tags&&<details><summary>INCAR tag 来源与校验级别 · 目录 {report.parameter_catalog?.retrieved}</summary><p><a href={report.parameter_catalog?.source} target="_blank" rel="noreferrer">VASP 官方目录</a> · <details><summary>目录核验</summary>{report.parameter_catalog?.revision}</details></p><table><thead><tr><th>tag</th><th>实际写入值</th><th>来源</th><th>校验级别</th></tr></thead><tbody>{Object.entries(report.incar_tags).map(([tag,row])=><tr key={tag}><td>{tag}</td><td>{row.value}</td><td>{row.source}</td><td>{row.validation}</td></tr>)}</tbody></table></details>}{Object.entries(report.files).map(([name,text])=><details key={name} open><summary>{name}{name==='POSCAR.reference'?'（参考结构，不能替代上游最终结构）':''}</summary><pre className="input-text">{text}</pre>{(name==='POSCAR'||name==='POSCAR.reference')&&<PreviewStructureAnalysis poscar={text} projectId={project.id}/>}</details>)}{batchBaseline&&<p>基础设置无需单独冻结；请在下方预览全部 case 并准备整批测试。</p>}<div className="input-review-actions"><label className="confirmation"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/><span>我已核对以上文件、赝势映射和提示，确认冻结当前节点的输入。</span></label><button className="button primary" disabled={batchBaseline||busy||!!report.errors.length||!draft.reason?.trim()||!reviewed} onClick={()=>void run(true)}>{report.ready?'冻结输入包':'冻结待依赖配方'}</button></div></>}{bundle&&<div className="frozen-next" role="status"><div><strong>当前节点输入已冻结 · {bundle.reason||'当前输入'}</strong><a href={bundle.download}>下载冻结输入包</a></div>{onReview&&<button className="button primary" onClick={()=>onReview(bundle.id)}>用此版本准备运行</button>}</div>}<details onToggle={e=>{if(e.currentTarget.open)void loadHistory().catch(e=>setError(errorMessage(e)));}}><summary>当前节点冻结记录</summary><button className="text-button" onClick={()=>void loadHistory().catch(e=>setError(errorMessage(e)))}>刷新记录</button><HistoryList rows={history.filter(h=>h.node_id===node?.id)} context={project.id+node?.id} render={h=><div className="history-row" key={h.id}><a href={h.download}>{h.reason||'冻结输入'}</a><p>{h.status==='pending-dependencies'?'待上游产物':'待执行前审查'} · {h.reason}</p><DeleteRecord kind="inputs" id={h.id} label={h.reason||'冻结输入'} onChanged={()=>inputRemoved(h.id)}/></div>}/></details><RecycleBin key={project.id} kind="inputs" projectId={project.id} title="冻结输入回收区" onChanged={loadHistory}/></section>;
}
