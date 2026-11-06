import Feedback from './Feedback';
import {useEffect,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {openSettings} from './submissionDefaults';
import {useSettingsDraft} from './useSubmissionDraft';

export function FirstRunNotice(){
 const [show,setShow]=useState(false);
 useEffect(()=>{void requestJson('/api/local-setup').then(v=>setShow(v.first_run&&!sessionStorage.getItem('fpilot-setup-later'))).catch(()=>{});const done=()=>setShow(false);window.addEventListener('fpilot:setup-completed',done);return()=>window.removeEventListener('fpilot:setup-completed',done);},[]);
 if(!show)return null;
 return <div className="first-run-notice" role="status"><div><strong>欢迎使用计算工作台</strong><span>先确认本机路径，再登记服务器与脚本。已有结构可直接导入。</span></div><button className="button primary" onClick={()=>openSettings('data')}>开始设置</button><button className="button" onClick={()=>{sessionStorage.setItem('fpilot-setup-later','1');setShow(false);}}>稍后设置</button></div>;
}

type Values={workspace:string;research_root:string;port:number;worker_enabled:boolean};
type Setup={current:Partial<Values>&{potcar_library?:string};saved:Partial<Values>;python:string;python_version:string};
export default function LocalSetup({group='data'}:{group?:'data'|'worker'}){
 const [info,setInfo]=useState<Setup|null>(null),[message,setMessage]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [draft,setDraft]=useSettingsDraft<Partial<Values>>('local:'+group,{});
 const baseline={worker_enabled:true,...info?.current,...info?.saved};
 const form={...baseline,...draft};
 const changes=(values:Partial<Values>)=>Object.fromEntries(Object.entries(values).filter(([key,value])=>baseline[key as keyof Values]!==value));
 const patch=changes(draft);
 const dirty=Object.keys(patch).length>0;
 useEffect(()=>{let active=true;void requestJson('/api/local-setup').then(v=>{if(active)setInfo(v);}).catch(e=>{if(active)setError(errorMessage(e));});return()=>{active=false;};},[]);
 const update=(patch:Partial<Values>)=>{setDraft(value=>changes({...value,...patch}));setMessage('');setError('');};
 const save=async()=>{
  if(!info)return;
  if(!dirty)return;
  setBusy(true);setError('');setMessage('');
  try{
   const result=await requestJson('/api/local-setup',patch,'PATCH');
   setInfo({...info,saved:result.saved});setDraft({});
   setMessage(result.restart_required?'已保存，重新启动工作台后生效。':'已保存。');
   window.dispatchEvent(new Event('fpilot:setup-completed'));
  }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 };
 return <section className="input-submit-card local-setup">
  <h3>{group==='data'?'本机数据目录':'启动时的后台监控'}</h3>
  {info&&<fieldset disabled={busy}>
   {group==='worker'?<>
    <label className="check-label"><input type="checkbox" checked={form.worker_enabled} onChange={e=>update({worker_enabled:e.target.checked})}/>启动监控与已授权任务的自动推进</label>
    <p>已保存设置 · {baseline.worker_enabled?'启动时开启':'启动时关闭'}。修改在重新启动工作台后生效。</p>
   </>:<>
    <label>本机数据目录<input value={form.workspace||''} onChange={e=>update({workspace:e.target.value})} placeholder="填写本机绝对路径"/></label>
    <p>当前生效 · <code>{info.current.workspace}</code></p>
    {info.saved.workspace&&info.saved.workspace!==info.current.workspace&&<p>已保存，待重启 · <code>{info.saved.workspace}</code></p>}
    <p>原有数据仍在原目录。可通过工作区备份和导入恢复项目。</p>
    <p>POTCAR 库 · <code>{info.current.potcar_library||'尚未设置'}</code> <button className="text-button" onClick={()=>openSettings('config')}>管理 POTCAR 库</button></p>
    <details data-visit-detail="local-runtime" id="local-runtime"><summary>高级运行环境</summary>
     <p>当前 Python <code>{info.python}</code> · {info.python_version}</p>
     <label>网页端口<input type="number" min={1024} max={65535} value={form.port??''} onChange={e=>update({port:Number(e.target.value)})}/></label>
     <p>当前端口 {info.current.port}{info.saved.port&&info.saved.port!==info.current.port?' · 已保存 '+info.saved.port+'，待重启':''}</p>
     <label>研究数据目录<input value={form.research_root||''} onChange={e=>update({research_root:e.target.value})}/></label>
     <p>供数据集工具定位研究文件。当前配置 <code>{info.current.research_root||'使用工作区旁的 research-data'}</code>{info.saved.research_root!==undefined&&info.saved.research_root!==info.current.research_root?' · 已保存新值，待重启':''}</p>
    </details>
   </>}
   <div className="action-row"><button className="button primary" disabled={!dirty} onClick={()=>void save()}>保存{group==='data'?'本机路径':'启动选项'}</button>
   {dirty&&<><button className="button" onClick={()=>setDraft({})}>放弃修改</button><span role="status">有未保存修改</span></>}</div>
  </fieldset>}
  <Feedback message={message} error={error} busy={busy} label="正在保存本机设置…"/>
 </section>;
}
