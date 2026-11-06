import {useEffect,useState} from 'react';
import type {Project} from './model';
import {requestJson,errorMessage} from './apiClient';
import {useSubmissionDraft} from './useSubmissionDraft';
import {useRequestScope} from './useRequestScope';
import Feedback from './Feedback';

type Directories={remoteWorkspaceRoot:string;remoteSlug:string;remoteDirectories:Record<string,string>};
type Preview={path:string|null;source:'project'|'override'|'server'};
const sourceNames={project:'本项目通用根目录',override:'该服务器的项目例外',server:'旧项目沿用服务器登记值'};
const validRoot=(value:string)=>/^(\/|\$HOME(?:\/|$))/.test(value)&&!/[\x00-\x1f\x7f-\uffff]/.test(value);

export default function ProjectRemoteDirectories({project,canEdit,slugLocked,defaultRoot,isSlugTaken,onSave}:{
 project:Project;canEdit:boolean;slugLocked:boolean;defaultRoot:string;isSlugTaken:(value:string)=>boolean;onSave:(patch:Partial<Project>)=>void;
}){
 const [hosts,setHosts]=useState<{alias:string}[]>([]);
 const [alias,setAlias]=useState(''),[exception,setException]=useState(''),[adding,setAdding]=useState(false);
 const [preview,setPreview]=useState<Preview|null>(null),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 const [draft,setDraft]=useSubmissionDraft<Partial<Directories>>('properties:'+project.id+':directories',{});
 const requests=useRequestScope(project.id);
 const saved={remoteWorkspaceRoot:project.remoteWorkspaceRoot||'',remoteSlug:project.remoteSlug||'',remoteDirectories:project.remoteDirectories||{}};
 const form={...saved,...draft,...(slugLocked?{remoteSlug:saved.remoteSlug}:{})};
 const dirty=JSON.stringify(form)!==JSON.stringify(saved);
 const target=alias||hosts[0]?.alias||'';
 function change(patch:Partial<Directories>){requests.cancel('preview');setPreview(null);setBusy(false);setDraft(old=>({...old,...patch}));setMessage('');setError('');}
 function directory(alias:string,value:string){
  change({remoteDirectories:{...form.remoteDirectories,[alias]:value}});
 }
 async function read(next:Directories,commit=false){
  const request=requests.start('preview');setBusy(true);setError('');if(commit)setMessage('');
  try{
   if(!/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(next.remoteSlug))throw new Error('项目目录名须使用小写字母、数字或短横线');
   if(next.remoteSlug!==saved.remoteSlug&&isSlugTaken(next.remoteSlug))throw new Error('该目录标识已被另一项目使用');
   if(next.remoteWorkspaceRoot!==saved.remoteWorkspaceRoot&&next.remoteWorkspaceRoot&&!validRoot(next.remoteWorkspaceRoot))throw new Error('通用根目录须为英文绝对路径或 $HOME 下的目录');
   if(Object.entries(next.remoteDirectories).some(([host,path])=>path!==saved.remoteDirectories[host]&&(!path.startsWith('/')||!validRoot(path))))throw new Error('服务器例外须为英文绝对路径');
   const value:Preview=await requestJson('/api/projects/'+encodeURIComponent(project.id)+'/directory-preview',
    {project:{id:project.id,remotePathPolicy:project.remotePathPolicy,...next},alias:target},undefined,{signal:request.signal});
   if(!request.current())return;
   setPreview(value);
   if(commit){
    onSave({remoteSlug:next.remoteSlug,remoteWorkspaceRoot:next.remoteWorkspaceRoot||undefined,remoteDirectories:next.remoteDirectories});
    setDraft({});setMessage('已应用到项目草稿，后续冻结和新任务使用此设置');
   }
  }catch(e){if(request.current())setError(errorMessage(e));}
  finally{if(request.current())setBusy(false);}
 }
 useEffect(()=>{
  const request=requests.start('servers');
  void requestJson('/api/server',undefined,undefined,{signal:request.signal}).then(value=>{
   if(request.current())setHosts(value.registered_profiles||[]);
  }).catch(e=>{if(request.current())setError(errorMessage(e));});
 },[requests]);
 useEffect(()=>{void read(dirty?form:saved);},[target,project.remoteWorkspaceRoot,project.remoteSlug,project.remoteDirectories]);
 const available=hosts.filter(host=>!(host.alias in form.remoteDirectories));
 const normalizedForm=()=>({...form,remoteDirectories:Object.fromEntries(Object.entries(form.remoteDirectories).filter(([,value])=>value))});
 return <section className="input-submit-card project-directory-settings">
  <h3>计算目录</h3>
  <p>此项目后续冻结和新任务使用这组设置。已有计算按各自冻结记录读取原目录。</p>
  <label>项目通用根目录<input value={form.remoteWorkspaceRoot} disabled={!canEdit} placeholder="留空时沿用服务器登记值" onChange={e=>change({remoteWorkspaceRoot:e.target.value})}/></label>
  <button className="text-button" disabled={!canEdit} onClick={()=>change({remoteWorkspaceRoot:defaultRoot})}>采用当前新项目默认值</button>
  <div className="project-path-preview">
   <label>预览服务器<select value={target} onChange={e=>setAlias(e.target.value)}>{!hosts.length&&<option value="">尚无服务器登记</option>}{hosts.map(host=><option key={host.alias}>{host.alias}</option>)}</select></label>
   <small>选择仅用于查看目录。</small>
   {preview?.path&&<><p>目录依据 · {sourceNames[preview.source]}</p><code>{preview.path.replace(/\/$/,'')}/flows/main/stages/…/attempts/…</code></>}
   {preview&&!preview.path&&<p>登记服务器后可预览兼容目录。</p>}
   <button className="text-button" disabled={busy} onClick={()=>void read(normalizedForm())}>预览当前填写的目录</button>
  </div>
  <details data-visit-detail="directory-advanced" id="project-directory-advanced">
   <summary>服务器例外与目录标识</summary>
   {Object.entries(form.remoteDirectories).map(([host,value])=><div className="directory-exception" key={host}>
    <label>{host} 的项目绝对目录<input disabled={!canEdit} value={value} onChange={e=>directory(host,e.target.value)}/></label>
    <button className="text-button" disabled={!canEdit} onClick={()=>{const next={...form.remoteDirectories};delete next[host];change({remoteDirectories:next});}}>清除本项目覆盖</button>
   </div>)}
   {available.length>0&&(adding?<div className="action-row"><label>服务器<select value={exception} onChange={e=>setException(e.target.value)}><option value="">请选择</option>{available.map(host=><option key={host.alias}>{host.alias}</option>)}</select></label><button className="button" disabled={!canEdit||!available.some(host=>host.alias===exception)} onClick={()=>{directory(exception,'');setAdding(false);setException('');}}>添加例外</button></div>:<button className="button" disabled={!canEdit} onClick={()=>setAdding(true)}>添加服务器例外</button>)}
   <label>项目目录名<input value={form.remoteSlug} maxLength={64} disabled={!canEdit||slugLocked} onChange={e=>change({remoteSlug:e.target.value})}/></label>
   {slugLocked&&<small>已有运行记录或尚在读取记录，目录名保持当前值。</small>}
  </details>
  <div className="action-row"><button className="button primary" disabled={!canEdit||busy||!dirty} onClick={()=>void read(normalizedForm(),true)}>保存目录修改</button>{dirty&&<><button className="button" disabled={busy} onClick={()=>{setDraft({});void read(saved);}}>放弃目录修改</button><span role="status">有未保存修改</span></>}</div>
  <Feedback message={message} error={error} busy={busy} label="正在核对目录…"/>
 </section>;
}
