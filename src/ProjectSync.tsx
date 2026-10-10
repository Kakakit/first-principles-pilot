import {useEffect,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import type {Project} from './model';
import './project-sync.css';

type Disk={ready:boolean;error:string;flush:(manual?:boolean)=>Promise<void>;exclusive:<T>(action:()=>Promise<T>)=>Promise<T>};
type Preview={preview_id:string;project_id:string;name:string;direction:string;counts:Record<string,number>;files:number;bytes:number;local_changes:boolean;local_counts:Record<string,number>|null;missing:{path:string;reason:string}[];version:string|null;remote_owner:string|null;remote_operation:unknown;updated_at?:string;read_only:boolean};
const modes:Record<string,string>={ACTIVE:'本机执行',OBSERVER:'只查看与刷新',STOPPED:'停止派发，待核验',TRANSFERRING:'正在交接',DISPATCHING:'正在派发',RETRY_UPLOAD:'首次上传未发布，已核查；可重新预览上传'};
export default function ProjectSync({project,disk,onChanged}:{project:Project;disk:Disk;onChanged:()=>void}){
  const [alias,setAlias]=useState(''),[hosts,setHosts]=useState<{alias:string;root:string}[]>([]),[state,setState]=useState<any>(null);
  const [catalog,setCatalog]=useState<any[]>([]),[target,setTarget]=useState(''),[preview,setPreview]=useState<Preview|null>(null);
  const [choice,setChoice]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  const [versions,setVersions]=useState<any[]>([]),[version,setVersion]=useState('');
  useEffect(()=>{let active=true;setPreview(null);setCatalog([]);setTarget('');
    Promise.all([requestJson('/api/project-sync/hosts'),requestJson(`/api/projects/${project.id}/sync`)]).then(([h,s])=>{if(!active)return;setHosts(h.hosts);setState(s);setAlias(s.local?.alias||s.default_alias||h.hosts[0]?.alias||'');}).catch(e=>{if(active)setError(errorMessage(e));});
    return()=>{active=false;};
  },[project.id]);
  useEffect(()=>{let active=true;setVersion('');setVersions([]);if(target&&alias)requestJson(`/api/projects/${target}/sync/versions?alias=${encodeURIComponent(alias)}`).then(r=>{if(active)setVersions(r.versions);}).catch(e=>{if(active)setError(errorMessage(e));});return()=>{active=false;};},[target,alias,catalog]);
  useEffect(()=>{if(!disk.ready)return;let active=true;const timer=setTimeout(()=>{requestJson(`/api/projects/${project.id}/sync`).then(s=>{if(active)setState(s);}).catch(()=>{});},800);return()=>{active=false;clearTimeout(timer);};},[project.id,project.updatedAt,disk.ready]);
  const run=async(action:()=>Promise<void>)=>{if(busy)return;setBusy(true);setError('');setMessage('');try{await action();}catch(e){setError(errorMessage(e));}finally{setBusy(false);}};
  const prepare=async(direction:string,pid=project.id)=>{await disk.flush(true);if(disk.error)throw new Error(disk.error);const p=await requestJson(`/api/projects/${pid}/sync/preview`,{alias,direction,version:direction==='download'?(version||undefined):undefined});setPreview(p);setChoice(false);};
  const confirm=async()=>{if(!preview)return;const pid=preview.project_id;
    const result=await disk.exclusive(()=>requestJson(`/api/projects/${pid}/sync/transfer`,{preview_id:preview.preview_id,choose_server:choice}));
    setPreview(null);setMessage(preview.direction==='upload'?'已上传并交接；本机只查看与刷新。':preview.read_only?'历史版本已恢复为只读；未接管执行权。':'已下载并接管；历史及冻结授权保留。');
    setState({local:result.local,pending:false});onChanged();
    window.dispatchEvent(new Event('fpilot:project-sync-changed'));
  };
  const stopped=state?.local?.mode==='STOPPED';
  return <details className="project-sync"><summary>跨设备项目接续</summary><p>工位上传并交接，家里下载并接管。保留项目身份、任务历史、冻结输入、分析和备注；大型计算输出继续留在服务器。</p>
    <p role="status">{state?.local?`${modes[state.local.mode]||state.local.mode} · 最后同步 ${state.local.synced_at?new Date(state.local.synced_at).toLocaleString():'尚未完成'}${state.pending?' · 有修改待同步':''}`:'尚未启用同步；当前项目沿用本机执行。'}{state?.device&&` · 本机 ${state.device.name}（${state.device.id.slice(0,8)}）`}</p>
    <label>同步服务器<select aria-label="同步服务器" value={alias} disabled={busy||Boolean(state?.local)} onChange={e=>{setAlias(e.target.value);setCatalog([]);setPreview(null);}}><option value="">请选择已登记服务器</option>{hosts.map(h=><option key={h.alias} value={h.alias}>{h.alias} · {h.root}</option>)}</select></label>
    <div className="sync-actions"><button className="button" disabled={busy||!disk.ready||!alias||stopped||state?.local?.mode==='OBSERVER'} onClick={()=>void run(()=>prepare('upload'))}>上传并交接</button>
    <button className="button" disabled={busy||!disk.ready||!alias} onClick={()=>void run(async()=>{const result=await requestJson(`/api/project-sync/catalog?alias=${encodeURIComponent(alias)}`);setCatalog(result.projects);setTarget(result.projects.some((p:any)=>p.project_id===project.id)?project.id:result.projects[0]?.project_id||'');setPreview(null);})}>读取服务器项目</button>
    <button className="button" disabled={busy||!disk.ready} onClick={()=>void run(async()=>{const r=await requestJson(`/api/projects/${project.id}/sync/refresh`,{});setState(r.status);setMessage(`已核查 ${r.records.length} 条已知记录；${r.records.filter((v:any)=>v.error).length} 条需核查。`);onChanged();})}>刷新远端记录</button>
    <button className="button" disabled={busy||!state?.local} onClick={()=>void run(async()=>{const r=await requestJson(`/api/projects/${project.id}/sync/recover`,{});setState(r);setMessage(r.message);})}>核查同步状态与回执</button></div>
    {catalog.length>0&&<div className="sync-catalog"><label>服务器项目<select aria-label="服务器项目" value={target} disabled={busy} onChange={e=>{setTarget(e.target.value);setPreview(null);}}>{catalog.map(p=><option key={p.project_id} value={p.project_id}>{p.name||p.project_id} · {p.counts?.attempts||0} 次运行 · {p.updated_at?new Date(p.updated_at).toLocaleString():''}</option>)}</select></label><button className="button" disabled={busy||!target||!disk.ready} onClick={()=>void run(()=>prepare('download',target))}>下载并接管</button></div>}
    {versions.length>1&&<label>恢复版本<select aria-label="恢复版本" value={version} disabled={busy} onChange={e=>{setVersion(e.target.value);setPreview(null);}}><option value="">最新版本（可接管）</option>{versions.slice(1).map(v=><option key={v.version} value={v.version}>{new Date(v.updated_at).toLocaleString()} · {v.counts?.attempts||0} 次运行（仅查看）</option>)}</select></label>}
    {preview&&<section className="sync-preview" aria-label="项目接续预览"><h3>{preview.direction==='upload'?'上传并交接预览':'下载并接管预览'} · {preview.name}</h3><p>项目 ID：<code>{preview.project_id}</code></p><p>{preview.counts.attempts} 次运行 · {preview.counts.chains} 条链 · {preview.counts.scans} 个扫描 · {preview.counts.analyses} 份分析／数据集 · {preview.files} 个本机附件（{(preview.bytes/1024/1024).toFixed(1)} MiB）</p>
    <p>当前执行设备：{preview.remote_owner||'已交接，等待接管'}；服务器最新版本：{preview.updated_at?new Date(preview.updated_at).toLocaleString():'首次上传'}。</p>
    {preview.local_counts&&<p>本机：{preview.local_counts.attempts} 次运行、{preview.local_counts.analyses} 份分析／数据集。{preview.local_changes?'存在本机未同步修改。':'与上次同步一致。'}</p>}
    {preview.direction==='download'&&<p>接续保留原 ID；其他项目保持。替换前保存本机完整备份。{!preview.read_only&&'接管后，原有效授权的活动链可通过现有检查继续推进；暂停、受阻及 UNKNOWN 保持。'}</p>}
    {preview.read_only&&<p>所选为历史版本，只恢复查看；服务器最新版本与当前执行设备保持。</p>}
    {preview.direction==='upload'&&<p>完成后本机停止新派发；已排队和运行的作业继续。Python、SSH 密钥、下载缓存与本机赝势配置不上传。</p>}
    {preview.missing.length>0&&<p>原设备未保存 {preview.missing.length} 个附件，保留缺失说明；不会自动重跑分析。</p>}
    {Boolean(preview.remote_operation)&&<p role="alert">服务器有尚未完成的项目操作，先核查回执。</p>}
    {preview.direction==='download'&&preview.local_changes&&<label><input type="checkbox" checked={choice} onChange={e=>setChoice(e.target.checked)}/>选择服务器版本，先备份本机未同步修改</label>}
    <div className="sync-actions"><button className="button primary" disabled={busy||!disk.ready||Boolean(preview.remote_operation)||(preview.direction==='download'&&preview.local_changes&&!choice)} onClick={()=>void run(confirm)}>{preview.direction==='upload'?'确认上传并交接':preview.read_only?'确认恢复历史版本（只读）':'确认下载并接管'}</button><button className="button" disabled={busy} onClick={()=>setPreview(null)}>取消</button></div></section>}
    {busy&&<p role="status">正在核验／传输，请保持工作台运行…</p>}{error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
  </details>;
}
