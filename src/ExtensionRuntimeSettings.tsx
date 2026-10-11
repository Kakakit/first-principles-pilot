import {useEffect,useRef,useState} from 'react';
import {readJson,errorMessage} from './apiClient';
import {preferredLocation,runtimeChoice,environmentVersion} from './extensionModel';
import type {ExtensionManifest,EnvironmentData,EnvironmentDoctor} from './extensionModel';
export default function ExtensionRuntimeSettings({item,environments,onChanged}:{item:ExtensionManifest;environments:EnvironmentData;onChanged:()=>Promise<void>}){
 const [location,setLocation]=useState(()=>preferredLocation(item)||'local'),[alias,setAlias]=useState(Object.keys(environments.runtimes)[0]||''),[mode,setMode]=useState('default'),[python,setPython]=useState(''),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[doctor,setDoctor]=useState<EnvironmentDoctor|null>(null);
 const chosen=runtimeChoice(item.id,location,alias,environments),persistedMode=chosen?.inherited?'default':'custom';
 const signature=JSON.stringify([location,alias,chosen?.python]);const current=useRef(signature);current.current=signature;
 useEffect(()=>{setMode(chosen?.inherited?'default':'custom');setPython(chosen?.python||'');setDoctor(null);setError('');setNotice('');},[signature]);
 const dirty=!!chosen&&(mode!==persistedMode||mode==='custom'&&python!==chosen.python);
 const action=async(check:boolean)=>{
  const before=current.current;setBusy(check?'check':'save');setError('');setNotice('');
  try{
   const response=await readJson(await fetch(`/api/extension-host/packages/${item.id}/${check?'doctor':'runtime'}`,{method:check?'POST':'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(check?{location,...(location!=='local'?{alias}:{})}:{scope:location==='local'?'local':'remote',...(location!=='local'?{alias}:{}),python:mode==='default'?null:python})}));
   if(before!==current.current)return;
   if(check)setDoctor(response.doctor);else {setDoctor(null);await onChanged();setNotice('环境选择已保存；请检查所选环境');}
  }catch(e){if(before===current.current)setError(errorMessage(e));}finally{setBusy('');}
 };
 return <details className="extension-runtime"><summary>Python 环境 · 默认或单独指定</summary><div className="parameter-grid">
  <label>检查与配置位置<select aria-label={`运行位置 · ${item.name}`} value={location} disabled={!!busy} onChange={e=>setLocation(e.target.value)}>{item.execution.locations.filter(mode=>mode==='local'||mode==='ssh').map(l=><option key={l} value={l}>{l==='local'?'本地运行':'服务器运行 · 直接命令行'}</option>)}</select></label>
  {location!=='local'&&<label>服务器<select aria-label={`服务器 · ${item.name}`} value={alias} disabled={!!busy} onChange={e=>setAlias(e.target.value)}><option value="">先登记服务器默认环境</option>{Object.keys(environments.runtimes).map(a=><option key={a}>{a}</option>)}</select></label>}
  {chosen?<><label>Python 选择<select aria-label={`Python 选择 · ${item.name}`} value={mode} disabled={!!busy} onChange={e=>{setMode(e.target.value);setDoctor(null);}}><option value="default">使用默认环境</option><option value="custom">单独指定 Python</option></select></label>
  {mode==='custom'&&<label>扩展 Python 绝对路径<input aria-label={`扩展 Python · ${item.name}`} value={python} disabled={!!busy} placeholder="已有环境中 Python 的绝对路径" onChange={e=>{setPython(e.target.value);setDoctor(null);}}/></label>}
  <p>已保存的解释器：{chosen.python} · {chosen.inherited?'继承默认':'扩展单独指定'}</p>
  <div className="button-row"><button className="button" disabled={!!busy||mode==='custom'&&!python.trim()} onClick={()=>void action(false)}>保存环境选择</button><button className="button" disabled={!!busy||dirty} onClick={()=>void action(true)}>检查所选环境</button></div>{dirty&&<p>修改后请先保存，再检查环境。</p>}</>:<p>先在下方登记该服务器的默认 Python 和工作区。</p>}
 </div>{busy&&<p role="status">{busy==='check'?'正在检查所选解释器，只运行 doctor，不计算或提交任务。':'正在保存环境选择…'}</p>}{error&&<p className="structure-error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{doctor&&<p className={doctor.ready?'extension-notice':'handoff-warning'} role="status">所选环境：{doctor.ready?'可用':'不可用'}{environmentVersion(doctor)&&` · ${environmentVersion(doctor)}`} · {chosen?.python||doctor.python} · {doctor.missing?.join('、')||doctor.reason}</p>}</details>;
}
