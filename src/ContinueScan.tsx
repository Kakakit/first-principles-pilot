import {useEffect,useRef,useState} from 'react';
import {errorMessage,requestJson} from './apiClient';
import type {Project} from './model';
import type {ContinuationCreated} from './scanContinuation';
import './continuation.css';

type Choice={case_id:string;index:number;attempt_id:string;parameters:{key:string;value:string}[];eligible:boolean;reason:string;alias?:string;job_id?:string};
type Checked={case_id:string;attempt_id:string;index?:number;alias?:string;errors:string[];warnings:string[];input_files?:Record<string,string>;input_diff?:Record<string,string>;structure_source?:{volume_A3:number;sha256:string}};
type Preview={preview_id:string;definition_hash:string;ready:boolean;cases:Checked[]};

export default function ContinueScan({project,batchId,initialAttemptId,onCreated}:{project:Project;batchId:string;initialAttemptId?:string;onCreated:(result:ContinuationCreated)=>void}){
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [choices,setChoices]=useState<Choice[]>([]),[selected,setSelected]=useState<string[]>([]),[children,setChildren]=useState<ContinuationCreated[]>([]);
 const [name,setName]=useState('后续 SCF'),[incar,setIncar]=useState(''),[grid,setGrid]=useState(''),[center,setCenter]=useState('Gamma');
 const [files,setFiles]=useState('structure'),[acknowledge,setAcknowledge]=useState(false),[preview,setPreview]=useState<Preview|null>(null);
 const dialog=useRef<HTMLDialogElement>(null),request=useRef<AbortController|null>(null);
 useEffect(()=>{if(open&&!dialog.current?.open)dialog.current?.showModal();},[open]);
 useEffect(()=>()=>{request.current?.abort();},[]);
 const act=async(work:(signal:AbortSignal)=>Promise<void>)=>{
  if(request.current)return;
  const controller=new AbortController();request.current=controller;setBusy(true);setError('');
  try{await work(controller.signal);}catch(caught){if(!controller.signal.aborted){const message=errorMessage(caught);setError(message==='Not Found'?'当前后端尚未加载批量续算接口，请重启工作台后重试。':message);}}
  finally{if(!controller.signal.aborted)setBusy(false);request.current=null;}
 };
 const show=()=>{setOpen(true);setPreview(null);void act(async signal=>{
  const query=new URLSearchParams({project_id:project.id,...(initialAttemptId?{attempt_id:initialAttemptId}:{})});
  const data:{cases:Choice[];continuations:ContinuationCreated[]}=await requestJson(`/api/scans/${encodeURIComponent(batchId)}/continuation-sources?${query}`,undefined,undefined,{signal});
  if(signal.aborted)return;
  setChoices(data.cases);setChildren(data.continuations);setSelected(data.cases.filter(item=>item.eligible&&(!initialAttemptId||item.attempt_id===initialAttemptId)).map(item=>item.case_id));
 });};
 const inspect=()=>void act(async signal=>{
  const data=await requestJson('/api/scan-continuations/preview',{project,source_batch_id:batchId,
   selections:choices.filter(item=>selected.includes(item.case_id)).map(item=>({case_id:item.case_id,attempt_id:item.attempt_id})),
   name,incar,grid,center,files,acknowledge_differences:acknowledge},undefined,{signal});
  if(!signal.aborted)setPreview(data);
 });
 const create=()=>void act(async signal=>{
  if(!preview)return;
  const data:ContinuationCreated=await requestJson(`/api/scan-continuations/${preview.preview_id}/create`,{project_id:project.id,definition_hash:preview.definition_hash},undefined,{signal});
  if(signal.aborted)return;
  setChildren(old=>[data,...old.filter(item=>item.batch.id!==data.batch.id)]);
  onCreated(data);setOpen(false);
  window.dispatchEvent(new CustomEvent('fpilot:history-changed',{detail:{kind:'scans',id:data.batch.id,action:'freeze'}}));
 });
 const change=(update:()=>void)=>{update();setPreview(null);};
 return <>
  <button className="button" onClick={show}>基于所选结果创建 SCF 批次</button>
  {open&&<dialog ref={dialog} className="history-confirm continuation-dialog" aria-label="基于优化结果创建 SCF 批次" onCancel={event=>{if(busy)event.preventDefault();else setOpen(false);}}>
   <h3>基于优化结果创建 SCF 批次</h3><p>每个 case 读取自己所选运行的 CONTCAR，继承适用的物理设置与 k 网格。统一覆盖值在下方填写，创建后进入运行审阅。</p>
   {children.length>0&&<details><summary>已创建的后续批次</summary>{children.map(item=><button className="text-button" disabled={busy} key={item.batch.id} onClick={()=>{try{onCreated(item);setOpen(false);}catch(caught){setError(errorMessage(caught));}}}>{item.node.data.label} · {item.batch.case_count} 项 · 打开运行审阅</button>)}</details>}
   <fieldset disabled={busy} className="continuation-options"><label>批次名称<input value={name} maxLength={200} onChange={event=>change(()=>setName(event.target.value))}/></label>
    <label>统一 INCAR 覆盖（选填）<textarea value={incar} maxLength={20000} placeholder={'例如 ENCUT = 520\nEDIFF = 1E-7'} onChange={event=>change(()=>setIncar(event.target.value))}/></label>
    <div className="parameter-grid"><label>统一 k 网格（选填）<input value={grid} placeholder="留空继承各 case 的网格" onChange={event=>change(()=>setGrid(event.target.value))}/></label>{grid.trim()&&<label>网格中心<select value={center} onChange={event=>change(()=>setCenter(event.target.value))}><option>Gamma</option><option>Monkhorst-Pack</option></select></label>}
     <label>接力文件<select value={files} onChange={event=>change(()=>setFiles(event.target.value))}><option value="structure">仅结构</option><option value="charge">结构与 CHGCAR</option><option value="both">结构、CHGCAR 与 WAVECAR</option></select></label></div>
    {files!=='structure'&&<label className="checkbox-row"><input type="checkbox" checked={acknowledge} onChange={event=>change(()=>setAcknowledge(event.target.checked))}/>已核对电子初值的参数差异</label>}
    <div className="action-row"><button className="text-button" onClick={()=>change(()=>setSelected(choices.filter(item=>item.eligible).map(item=>item.case_id)))}>选择全部可用 case</button><button className="text-button" onClick={()=>change(()=>setSelected([]))}>清空选择</button><span>已选 {selected.length} / {choices.length}</span></div>
    <div className="atoms-scroll"><table><thead><tr><th>选择</th><th>来源 case</th><th>扫描条件</th><th>原运行 / 主机</th><th>可用性</th></tr></thead><tbody>{choices.map(item=><tr key={item.case_id}><td><input type="checkbox" aria-label={`选择 Case ${item.index+1}`} disabled={!item.eligible} checked={selected.includes(item.case_id)} onChange={event=>change(()=>setSelected(old=>event.target.checked?[...old,item.case_id]:old.filter(id=>id!==item.case_id)))}/></td><td>{item.index+1}</td><td>{item.parameters.map(parameter=>`${parameter.key}=${parameter.value}`).join(' · ')}</td><td>{item.job_id||'尚无运行'} · {item.alias||'—'}</td><td>{item.eligible?'完成与收敛证据可用':item.reason}</td></tr>)}</tbody></table></div>
   </fieldset>
   {preview&&<section><h4>逐 case 输入预览</h4>{preview.cases.map(item=><details key={item.case_id}><summary>来源 Case {(choices.find(choice=>choice.case_id===item.case_id)?.index??0)+1} · {item.errors.length?'需处理':`${item.alias} · 输入可用`}</summary>{item.errors.map(message=><p role="alert" key={message}>{message}</p>)}{item.warnings.map(message=><p key={message}>{message}</p>)}{Object.entries(item.input_diff||{}).map(([file,text])=><div key={file}><strong>{file} 变化</strong><pre className="input-text">{text||'与来源相同'}</pre></div>)}{item.structure_source&&<p>来源 CONTCAR 体积 {item.structure_source.volume_A3} Å³</p>}<details><summary>实际 SCF 输入</summary>{Object.entries(item.input_files||{}).map(([file,text])=><div key={file}><strong>{file}</strong><pre className="input-text">{text}</pre></div>)}</details></details>)}
    {!preview.ready&&<button className="button" disabled={busy} onClick={()=>change(()=>setSelected(preview.cases.filter(item=>!item.errors.length).map(item=>item.case_id)))}>仅保留检查通过的 case</button>}
   </section>}
   {busy&&<p role="status">正在核对所选来源与输入…</p>}{error&&<p role="alert">{error}</p>}
   <div className="action-row"><button className="button" disabled={busy} onClick={()=>setOpen(false)}>关闭</button><button className="button" disabled={busy||!selected.length||!name.trim()} onClick={inspect}>预览后续 SCF 输入</button><button className="button primary" disabled={busy||!preview?.ready} onClick={create}>创建冻结批次并审阅运行</button></div>
  </dialog>}
 </>;
}
