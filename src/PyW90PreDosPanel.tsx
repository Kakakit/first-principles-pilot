import {useEffect,useRef,useState} from 'react';
import type {Project} from './model';
import {requestJson,errorMessage} from './apiClient';
import {attemptLabel} from './humanLabels';
import PyW90ProjectionDiagnostic from './PyW90ProjectionDiagnostic';
import type {DosSelection} from './PyW90ProjectionDiagnostic';
import './pyw90-advice.css';

type Source={id:string;node_id:string;owner_node_id?:string;execution_scope?:string;parameter_summary?:string;case_index?:number;job_id?:string;created_at:string;config:{alias:string};analysis_kind:string;analysis_channels:string[];scf_attempt_id:string;pyw90_available:boolean;pyw90_reason:string};
type Result={projections:string[];dos_selection:DosSelection;energy_reference:{fermi_eV:number};relative_range_eV:number[];channel:string;source_attempt_id:string;source_hashes:Record<string,string>;runs:{arguments:string[];stdout:string;stderr:string}[];note:string};
export default function PyW90PreDosPanel({project,nodeId,projectionText,onProjections,targetSpinors}:{project:Project;nodeId:string;projectionText:string;onProjections:(value:string)=>void;targetSpinors:boolean}){
 const [sources,setSources]=useState<Source[]>([]),[sourceId,setSourceId]=useState(''),[channel,setChannel]=useState('auto');
 const [emin,setEmin]=useState('-4'),[emax,setEmax]=useState('4'),[busy,setBusy]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState('');
 const [result,setResult]=useState<{data:Result;fingerprint:string}|null>(null),[adoption,setAdoption]=useState<{before:string;after:string}|null>(null);
 const graph=JSON.stringify([project.id,nodeId,project.edges,project.nodes.map(n=>[n.id,n.data.kind,n.data.scanSpec]),project.nodes.find(n=>n.id===nodeId)?.data.sourceDraft]);
 const fingerprint=JSON.stringify([graph,sourceId,channel,emin,emax]);const current=useRef(fingerprint);current.current=fingerprint;
 const alive=useRef(true);useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 const loadingId=useRef(0);
 const load=async()=>{const id=++loadingId.current;setLoading(true);setError('');try{const data=await requestJson('/api/inputs/pyw90-pre-dos-sources',{project,node_id:nodeId});if(!alive.current||id!==loadingId.current)return;setSources(data.sources);const bound=(project.nodes.find(n=>n.id===nodeId)?.data.sourceDraft as {attempt_id?:string}|undefined)?.attempt_id;setSourceId(old=>data.sources.some((r:Source)=>r.id===old)?old:data.sources.find((r:Source)=>r.id===bound&&r.pyw90_available)?.id||data.sources.find((r:Source)=>r.pyw90_available&&r.analysis_kind==='scf')?.id||data.sources.find((r:Source)=>r.pyw90_available)?.id||'');}catch(e){if(alive.current&&id===loadingId.current)setError(errorMessage(e));}finally{if(alive.current&&id===loadingId.current)setLoading(false);}};
 useEffect(()=>{setResult(null);setAdoption(null);setChannel('auto');void load();},[graph]);
 const source=sources.find(r=>r.id===sourceId),fresh=result?.fingerprint===fingerprint;
 const run=async()=>{const started=fingerprint;setBusy(true);setError('');try{const data:Result=await requestJson('/api/inputs/pyw90-pre-dos',{project,node_id:nodeId,source_attempt_id:sourceId,channel,emin:Number(emin),emax:Number(emax)});if(alive.current&&current.current===started)setResult({data,fingerprint:started});}catch(e){if(alive.current)setError(errorMessage(e));}finally{if(alive.current)setBusy(false);}};
 return <section className="pyw90-advice"><h4>PyW90 pre dos · 矩阵生成前的投影诊断</h4><p>从相连 SCF 或同一 SCF 来源的 DOS 的 vasprun.xml 分析轨道贡献。不需要 .win、.eig 或 EIGENVAL；分析来源不改变本次计算的来源绑定。</p>
 <label className="field">SCF／DOS 分析来源<select disabled={busy||loading} value={sourceId} onChange={e=>{setSourceId(e.target.value);setChannel('auto');setAdoption(null);}}><option value="">请选择已完成任务</option>{sources.map(r=><option key={r.id} value={r.id} disabled={!r.pyw90_available}>{attemptLabel(r,project)} · {r.analysis_kind.toUpperCase()}{!r.pyw90_available?' · '+r.pyw90_reason:''}</option>)}</select></label>
 <button className="button" disabled={busy||loading} onClick={()=>void load()}>刷新来源</button>{!loading&&!sources.length&&<p>没有可用的已完成 SCF／DOS 记录；请连接 SCF 并完成含投影 DOS 的计算。不会自动补算。</p>}
 {source&&<label className="field">实际分析通道<select disabled={busy} value={channel} onChange={e=>setChannel(e.target.value)}><option value="auto">{source.analysis_channels[0]==='charge'?'SOC／非共线电荷投影':source.analysis_channels[0]==='single'?'单通道':'默认：自旋上'}</option>{source.analysis_channels.length>1&&source.analysis_channels.map(c=><option key={c} value={c}>{c==='up'?'自旋上':'自旋下'}</option>)}</select></label>}
 <div className="parameter-grid"><label className="field">分析下界 · E−E_F / eV<input type="number" value={emin} disabled={busy} onChange={e=>setEmin(e.target.value)}/></label><label className="field">分析上界 · E−E_F / eV<input type="number" value={emax} disabled={busy} onChange={e=>setEmax(e.target.value)}/></label></div>
 <button className="button" disabled={busy||loading||!source?.pyw90_available||!emin.trim()||!emax.trim()||!Number.isFinite(Number(emin))||!Number.isFinite(Number(emax))||Number(emin)>=Number(emax)} onClick={()=>void run()}>运行 pre dos 投影诊断</button>
 {busy&&<p role="status">正在源服务器分析 DOS；研究输入保持。</p>}{error&&<p className="structure-error" role="alert">{error}</p>}
 {result&&<div className="pyw90-result">{!fresh&&<p className="handoff-warning">来源或分析设置已变化；以下为上次结果，请重新分析后采用。</p>}<p>E_F={result.data.energy_reference.fermi_eV.toFixed(6)} eV · 范围 {result.data.relative_range_eV.join(' 至 ')} eV · {result.data.channel==='charge'?'SOC／非共线电荷':result.data.channel==='single'?'单通道':result.data.channel==='down'?'自旋下':'自旋上'}</p>
 {Boolean(result.data.dos_selection.spinors)!==targetSpinors&&<p className="handoff-warning">分析来源与当前草稿的 SOC／非共线模型不同；请结合实际模型决定投影，参数不会自动改写。</p>}
 <PyW90ProjectionDiagnostic selection={result.data.dos_selection} projections={result.data.projections}/><p>{result.data.note}</p>
 <button className="button" disabled={busy||!fresh||!result.data.projections.length} onClick={()=>{const after=result.data.projections.join('\n');setAdoption({before:projectionText,after});onProjections(after);}}>用建议替换投影</button>
 {adoption&&<button className="button" disabled={projectionText!==adoption.after} onClick={()=>{onProjections(adoption.before);setAdoption(null);}}>撤销采用投影</button>}
 <details><summary>命令、完整输出与来源核验</summary>{result.data.runs.map((r,i)=><pre className="input-text" key={i}>{r.arguments.join(' ')}{'\n'}{r.stdout}{'\n'}{r.stderr}</pre>)}<pre>{JSON.stringify({attempt:result.data.source_attempt_id,files:result.data.source_hashes},null,2)}</pre></details></div>}
 </section>;
}
