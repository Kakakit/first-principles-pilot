import {useEffect,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
export default function CommonWorkspacePath(){
 const [root,setRoot]=useState('$HOME/fpilot'),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{void requestJson('/api/workbench-settings').then(v=>setRoot(v.workspace_root||'$HOME/fpilot')).catch(e=>setMessage(errorMessage(e)));},[]);
 async function save(){setBusy(true);setMessage('');try{await requestJson('/api/workbench-settings',{workspace_root:root},'PUT');window.dispatchEvent(new Event('fpilot:settings-changed'));setMessage('已保存。所有服务器上的新项目使用此默认值；已有项目和任务保持原目录。');}catch(e){setMessage(errorMessage(e));}finally{setBusy(false);}}
 return <section className="input-submit-card"><h3>通用默认工作区 · 所有服务器</h3><label>新项目的工作区根目录<input value={root} onChange={e=>setRoot(e.target.value)} placeholder="/scratch/my-projects"/></label><p>新建项目时仍可修改，无需先选服务器。使用英文目录名；可填写绝对路径，或用 $HOME 表示各服务器自己的用户主目录。</p><button className="button" disabled={busy} onClick={()=>void save()}>保存通用默认工作区</button>{message&&<p role="status">{message}</p>}</section>;
}
