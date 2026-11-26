import DisclosureLabel from './DisclosureLabel';
import {useEffect,useState,useRef} from 'react';
import {requestJson,errorMessage} from './apiClient';
import type {ParameterLayer} from './parameters';
type LibraryIndex={path:string;entries:Entry[];skipped:number};
type Entry={id:string;element:string;variant:string;family:string;titel:string;enmax:number};
export default function PotcarLibrary({species,functional,onApply,maintenance=false,parameterLayers}:{maintenance?:boolean;species:string[];functional:string|null;parameterLayers?:{shared:ParameterLayer;node:ParameterLayer};onApply:(id:string,receiptId:string)=>void}){
 const [entries,setEntries]=useState<Entry[]>([]),[path,setPath]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[chosen,setChosen]=useState<Record<string,string>>({}),[status,setStatus]=useState('');
 const [resolved,setResolved]=useState<{key:string;functional:string|null;source:string;error?:string}|null>(null);
 const context=parameterLayers&&(parameterLayers.shared.manualIncarText||parameterLayers.node.manualIncarText||parameterLayers.node.incarMode==='complete')?JSON.stringify(parameterLayers):'';
 useEffect(()=>{
  if(!context){setResolved(null);return;}
  const controller=new AbortController();
  const timer=setTimeout(()=>{void requestJson('/api/potcar-library/functional',JSON.parse(context),'POST',{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setResolved({key:context,...value});}).catch(e=>{if(!controller.signal.aborted)setResolved({key:context,functional:null,source:'未解析',error:errorMessage(e)});});},200);
  return()=>{clearTimeout(timer);controller.abort();};
 },[context]);
 const alive=useRef(true);useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 const [draftPath,setDraftPath]=useState<string|null>(null);
 const initialRead=useRef<AbortController|null>(null);
 const showIndex=(data:LibraryIndex)=>{
  setEntries(data.entries);
  setPath(data.path);
  setError('');
  setStatus(`已索引 ${data.entries.length} 个 PAW 数据集；${data.skipped} 个不支持的数据集未列入。`);
 };
 useEffect(()=>{
  const controller=new AbortController();
  initialRead.current=controller;
  void requestJson('/api/potcar-library',undefined,'GET',{signal:controller.signal,timeoutMs:0})
   .then(data=>{if(!controller.signal.aborted)showIndex(data);})
   .catch(e=>{if(!controller.signal.aborted)setError(errorMessage(e));});
  return ()=>controller.abort();
 },[]);
 const saveDirectory=async()=>{
  initialRead.current?.abort();
  setBusy(true);setError('');
  try{
   const data=await requestJson('/api/potcar-library',{path:draftPath??path},'PUT',{timeoutMs:0});
   if(alive.current){showIndex(data);setDraftPath(null);}
  }catch(e){if(alive.current)setError(errorMessage(e));}
  finally{if(alive.current)setBusy(false);}
 };
 const pending=Boolean(context&&resolved?.key!==context);
 const effectiveFunctional=context?(resolved?.key===context?resolved.functional:null):functional;
 const functionalError=context&&resolved?.key===context?resolved.error:'';
 const family=effectiveFunctional==='LDA'?'LDA':effectiveFunctional==='PBE'||effectiveFunctional==='PBEsol'?'PBE':'';
 const symbols=[...new Set(species)];
 const options=(element:string)=>entries.filter(e=>e.element===element&&e.family===family);
 const selected=(element:string)=>{const list=options(element);if(list.some(e=>e.id===chosen[element]))return chosen[element];const basic=list.filter(e=>e.variant===element);return basic.length===1?basic[0].id:'';};
 const assemble=async()=>{setBusy(true);setError('');try{const r=await fetch('/api/potcar-library/assemble',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({species,functional:effectiveFunctional,selections:Object.fromEntries(symbols.map(e=>[e,selected(e)]))})});const data=await r.json();if(!r.ok)throw new Error(data.detail);if(!alive.current)return;onApply(data.id,data.provenance_id);setStatus('已按结构顺序生成并应用。后续选择变化须再次点击生成。');}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
 return <div><h3>本机赝势库</h3>{maintenance&&<details open><DisclosureLabel>库目录与重新索引</DisclosureLabel><input aria-label="赝势库目录" value={draftPath??path} disabled={busy} onChange={e=>setDraftPath(e.target.value)}/><button type="button" className="button" disabled={busy||!(draftPath??path).trim()} onClick={()=>void saveDirectory()}>{busy?'正在索引…':'保存目录并索引'}</button></details>}<p>{status}</p>{!maintenance&&<><p>泛函：{pending?'正在解析手动 INCAR…':effectiveFunctional||'尚未明确'}{context&&!pending&&resolved?.source?' · 来源：'+resolved.source:''}。元素同名基础版本为候选，不代表已通过材料适用性或精度验证。</p>{!pending&&!family&&!functionalError&&<p>请在物理参数中选择泛函，或在手动 INCAR 中填写 GGA=PE（PBE）／GGA=PS（PBEsol）；未明确泛函时暂不筛选赝势。</p>}{functionalError&&<p role="alert">{functionalError}</p>}{symbols.map(element=><label key={element}>{element}<select disabled={busy||pending||!family} value={selected(element)} onChange={e=>{setChosen({...chosen,[element]:e.target.value});setStatus('选择尚未应用，请点击生成。');}}><option value="">{pending?'正在解析泛函…':!family?'请先明确泛函':options(element).length?'请选择赝势版本':`库中没有 ${element} 的 ${family} 数据集`}</option>{options(element).map(e=><option key={e.id} value={e.id}>{e.variant} · ENMAX {e.enmax} eV · {e.titel}</option>)}</select></label>)}<button type="button" className="button" disabled={busy||pending||!family||!symbols.length||symbols.some(e=>!selected(e))} onClick={()=>void assemble()}>{busy?'正在拼接…':'按所选版本生成并应用 POTCAR'}</button></>}{error&&<p role={error.includes("请配置存在")?undefined:"alert"}>{error.includes("请配置存在")?"尚未配置本机赝势库。可在设置中登记目录，或使用当前已导入的 POTCAR。":error}</p>}</div>;
}
