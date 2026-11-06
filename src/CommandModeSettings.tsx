import {useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {commandMode,type RegisteredProfile} from './executionUi';
import {useSettingsDraft} from './useSubmissionDraft';

export default function CommandModeSettings({profile,onSaved}:{profile:RegisteredProfile;onSaved:(profiles:RegisteredProfile[])=>void}){
 const [draft,setDraft]=useSettingsDraft<{mode?:'direct'|'opaque';limit?:string}>('command-mode:'+profile.alias,{});
 const savedMode=commandMode(profile),savedLimit=String(profile.direct_limit??1);
 const mode=draft.mode??savedMode,limit=draft.limit??savedLimit;
 const dirty=mode!==savedMode||limit!==savedLimit;
 function update(field:'mode'|'limit',value:string){
  setDraft(old=>{
   const next={...old};
   if(field==='mode'){if(value===savedMode)delete next.mode;else next.mode=value==='direct'?'direct':'opaque';}
   else if(value===savedLimit)delete next.limit;else next.limit=value;
   return next;
  });
  setMessage('');
 }
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 async function save(){
  setBusy(true);setError('');setMessage('');
  try{
   const result=await requestJson('/api/sites/'+encodeURIComponent(profile.alias)+'/paths',
    {command_mode:mode,...(mode==='direct'?{direct_limit:Number(limit)}:{})},'PUT');
   onSaved(result.registered_profiles);setDraft({});setMessage('已保存。新的任务准备将使用此设置。');
   window.dispatchEvent(new Event('fpilot:settings-changed'));
  }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 }
 return <section className="input-submit-card">
  <h3>无调度器的执行方式</h3>
  <label>此服务器的默认方式<select disabled={busy} value={mode||'opaque'} onChange={e=>update('mode',e.target.value)}>
   <option value="direct">直接运行并跟踪进程</option><option value="opaque">自定义命令及原始回执</option>
  </select></label>
  {mode==='direct'&&<>
   <label>本工作台的直接任务并发上限<input type="number" min={1} max={1000} step={1} value={limit} disabled={busy} onChange={e=>update('limit',e.target.value)}/></label>
   <p>此上限按服务器计算，只计直接任务。任务与监控中的总在途上限仍适用。</p>
   <details><summary>直接运行脚本示例</summary><pre className="input-text">{'source /path/to/environment.sh || exit $?\nexec /path/to/mpirun -np 8 /path/to/vasp_std'}</pre>
   <p>把路径和核数改成服务器实际值。SOC 输入请单独保存使用 vasp_ncl 的脚本。脚本在任务目录执行，应等到本机计算进程结束。</p></details>
  </>}
  <div className="action-row"><button className="button" disabled={!dirty||busy||mode==='direct'&&(!Number.isInteger(Number(limit))||Number(limit)<1||Number(limit)>1000)} onClick={()=>void save()}>保存服务器执行方式</button>
  {dirty&&<><button className="button" disabled={busy} onClick={()=>setDraft({})}>放弃修改</button><span role="status">有未保存修改</span></>}</div>
  {message&&<p role="status">{message}</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
