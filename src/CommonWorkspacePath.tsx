import {useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {useSettingsDraft} from './useSubmissionDraft';
export default function CommonWorkspacePath({initialRoot,onSaved}:{initialRoot?:string;onSaved:(root:string)=>void}){
 const savedRoot=initialRoot||'$HOME/fpilot';
 const [draft,setDraft]=useSettingsDraft<string|null>('new-project-root',null),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const root=draft??savedRoot;
 async function save(){setBusy(true);setMessage('');try{const saved=await requestJson('/api/workbench-settings',{workspace_root:root},'PUT');onSaved(saved.workspace_root);setDraft(null);window.dispatchEvent(new Event('fpilot:settings-changed'));setMessage('已保存。创建新项目时会复制此默认值。');}catch(e){setMessage(errorMessage(e));}finally{setBusy(false);}}
 return <section className="input-submit-card"><h3>新项目默认远端目录</h3><label>新项目的工作区根目录<input disabled={busy} value={root} onChange={e=>setDraft(e.target.value===savedRoot?null:e.target.value)} placeholder="/scratch/my-projects"/></label><p>创建新项目时复制此值。使用英文绝对路径或 $HOME 下的英文目录。</p><div className="action-row"><button className="button" disabled={busy||root===savedRoot} onClick={()=>void save()}>保存默认目录</button>{root!==savedRoot&&<><button className="button" disabled={busy} onClick={()=>setDraft(null)}>放弃修改</button><span role="status">有未保存修改</span></>}</div>{message&&<p role="status">{message}</p>}</section>;
}
