import Feedback from './Feedback';
import {useEffect,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {openSettings} from './submissionDefaults';

export function FirstRunNotice(){
 const [show,setShow]=useState(false);
 useEffect(()=>{void requestJson('/api/local-setup').then(v=>setShow(v.first_run&&!sessionStorage.getItem('fpilot-setup-later'))).catch(()=>{});const done=()=>setShow(false);window.addEventListener('fpilot:setup-completed',done);return()=>window.removeEventListener('fpilot:setup-completed',done);},[]);
 if(!show)return null;
 return <div className="first-run-notice" role="status"><div><strong>欢迎使用计算工作台</strong><span>先确认本机路径，再登记服务器与脚本。已有结构可直接导入。</span></div><button className="button primary" onClick={()=>openSettings('appearance')}>开始设置</button><button className="button" onClick={()=>{sessionStorage.setItem('fpilot-setup-later','1');setShow(false);}}>稍后设置</button></div>;
}

export default function LocalSetup(){
 const [info,setInfo]=useState<any>(null),[form,setForm]=useState<any>({}),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{void requestJson('/api/local-setup').then(v=>{setInfo(v);setForm({...v.current,...v.saved,worker_enabled:v.saved.worker_enabled!==false});}).catch(e=>setMessage(errorMessage(e)));},[]);
 const save=async()=>{setBusy(true);try{const result=await requestJson('/api/local-setup',{...form,port:Number(form.port)},'PUT');setMessage(result.note+(result.restart_required?' 需要重新启动。':''));window.dispatchEvent(new Event('fpilot:setup-completed')); }catch(e){setMessage(errorMessage(e));}finally{setBusy(false);}};
 return <section className="input-submit-card local-setup"><h3>本机路径与启动</h3>{info&&<><p>当前 Python：<code>{info.python}</code> · {info.python_version}</p><div className="submit-grid">{[['workspace','本机数据目录'],['potcar_library','POTCAR 库目录（可稍后设置）'],['research_root','研究文件目录（可选）']].map(([key,label])=><label className="field" key={key}>{label}<input value={form[key]||''} onChange={e=>setForm({...form,[key]:e.target.value})} placeholder={info.platform==='win32'?'例如 D:\计算工作台\数据':'填写本机绝对路径'}/></label>)}<label className="field">网页端口<input type="number" min={1024} max={65535} value={form.port||8765} onChange={e=>setForm({...form,port:e.target.value})}/></label></div><label className="check-label"><input type="checkbox" checked={form.worker_enabled!==false} onChange={e=>setForm({...form,worker_enabled:e.target.checked})}/>启动监控与已授权任务的自动推进</label><p>更换数据目录或端口后重新启动；原数据不会自动迁移。修改解释器可在启动前设置 FPILOT_PYTHON。</p><div className="button-row"><button className="button primary" disabled={busy} onClick={()=>void save()}>保存本机设置</button><button className="button" onClick={()=>openSettings('server')}>下一步：服务器与脚本</button></div></>}<Feedback message={message} busy={busy} label="正在保存本机设置…"/></section>;
}
