import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import type {Project} from './model';
import './project-sync.css';
type Disk={ready:boolean;error:string;flush:(manual?:boolean)=>Promise<void>;exclusive:<T>(action:()=>Promise<T>)=>Promise<T>};
type Host={alias:string;root:string};
type CleanupPreview={candidates:{sha256:string;size_bytes:number}[];server:{host:string;root:string}};
type Preview={preview_id:string;project_id:string;name:string;direction:string;counts:Record<string,number>;files:number;bytes:number;local_changes:boolean;local_counts:Record<string,number>|null;missing:{path:string;reason:string}[];remote_operation:unknown;absolute_directory:string};
export default function ProjectSync({project,disk,onChanged}:{project:Project;disk:Disk;onChanged:()=>void}){
 const [alias,setAlias]=useState(''),[directory,setDirectory]=useState(''),[hosts,setHosts]=useState<Host[]>([]),[state,setState]=useState<any>(null);
 const [catalog,setCatalog]=useState<any[]|null>(null),[target,setTarget]=useState(''),[preview,setPreview]=useState<Preview|null>(null),[versions,setVersions]=useState<any[]>([]),[version,setVersion]=useState('');
 const [cleanup,setCleanup]=useState<CleanupPreview|null>(null),[cleanupSelected,setCleanupSelected]=useState<string[]>([]);
 const [choice,setChoice]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const revision=useRef(0),context=useRef(project.id);context.current=project.id;
 const defaultDirectory=(root:string)=>root.replace(/\/$/,'')+'/.fpilot/project-sync';
 useEffect(()=>{let active=true;revision.current++;setPreview(null);setCleanup(null);setCatalog(null);setTarget('');setError('');setMessage('');
  Promise.all([requestJson('/api/project-sync/hosts'),requestJson(`/api/projects/${project.id}/sync`)]).then(([h,s])=>{if(!active)return;setHosts(h.hosts);setState(s);const a=s.location?.alias||s.local?.alias||s.default_alias||h.hosts[0]?.alias||'';setAlias(a);setDirectory(s.location?.snapshot_directory||s.local?.snapshot_directory||defaultDirectory(s.local?.root||h.hosts.find((v:Host)=>v.alias===a)?.root||'$HOME/fpilot'));}).catch(e=>{if(active)setError(errorMessage(e));});return()=>{active=false;};
 },[project.id]);
 useEffect(()=>{if(!disk.ready)return;let active=true;const timer=setTimeout(()=>{requestJson(`/api/projects/${project.id}/sync`).then(s=>{if(active)setState(s);}).catch(()=>{});},800);return()=>{active=false;clearTimeout(timer);};},[project.id,project.updatedAt,disk.ready]);
 const run=async(action:()=>Promise<void>)=>{if(busy)return;const pid=project.id;setBusy(true);setError('');setMessage('');try{await action();}catch(e){if(context.current===pid)setError(errorMessage(e));}finally{setBusy(false);}};
 const clear=()=>{revision.current++;setPreview(null);setCatalog(null);setTarget('');setVersion('');setVersions([]);setCleanup(null);setCleanupSelected([]);setMessage('');setError('');};
 const remember=()=>requestJson(`/api/projects/${project.id}/sync/location`,{alias,snapshot_directory:directory});
 const query=`alias=${encodeURIComponent(alias)}&snapshot_directory=${encodeURIComponent(directory)}`;
 const prepare=async(direction:string,pid=project.id)=>{const r=revision.current;await disk.flush(true);if(disk.error)throw new Error(disk.error);await remember();const p=await requestJson(`/api/projects/${pid}/sync/preview`,{alias,snapshot_directory:directory,direction,version:direction==='download'?(version||undefined):undefined});if(r!==revision.current)return;setPreview(p);setChoice(false);};
 const notify=()=>{onChanged();window.dispatchEvent(new Event('fpilot:project-sync-changed'));};
 const confirm=async()=>{if(!preview)return;const p=preview;const result=await disk.exclusive(()=>requestJson(`/api/projects/${p.project_id}/sync/transfer`,{preview_id:p.preview_id,choose_server:choice}));setPreview(null);setState(await requestJson(`/api/projects/${project.id}/sync`));setMessage(p.direction==='upload'?'快照已上传，项目仍可编辑；后续自动派发已暂停。':'项目已还原，任务路径与原 ID 保留；自动流程已暂停，请手动继续。');notify();};
 const stopped=state?.local?.mode==='STOPPED'||Boolean(state?.local?.operation),legacy=Boolean(state?.needs_conversion);
 return <details className="project-sync"><summary>跨设备项目快照</summary><p>服务器只保存快照。上传后仍可编辑，另一台设备下载还原历史、冻结输入、分析和备注；计算文件留在原服务器、原目录。</p>
 <p role="status">{state?.local?`${legacy?'旧执行权交接，需切换':state?.local?.restore_required?'还原未完成，请重新下载':stopped?'回执待核查，自动派发保持暂停':'轻量快照传输'} · 最后同步 ${state.local.synced_at?new Date(state.local.synced_at).toLocaleString():'尚未完成'}${state.pending?' · 有修改待上传':''}`:'尚未上传项目快照。'}</p>
 {legacy&&<div className="sync-preview"><p>旧模式可能将本机设为只读。切换会先暂停后续自动派发，保留授权、已提交作业及旧快照；未完成的旧操作须先核查。</p><button className="button" disabled={busy||stopped||!disk.ready} onClick={()=>void run(async()=>{const s=await disk.exclusive(()=>requestJson(`/api/projects/${project.id}/sync/convert`,{}));setState(s);setDirectory(s.local.snapshot_directory);notify();setMessage('已切换，草稿可编辑；自动派发已暂停。');})}>切换为快照传输</button></div>}
 <label>同步服务器<select aria-label="同步服务器" value={alias} disabled={busy} onChange={e=>{const old=hosts.find(h=>h.alias===alias),next=hosts.find(h=>h.alias===e.target.value);if(directory===defaultDirectory(old?.root||'$HOME/fpilot'))setDirectory(defaultDirectory(next?.root||'$HOME/fpilot'));setAlias(e.target.value);clear();}}><option value="">请选择已登记服务器</option>{hosts.map(h=><option key={h.alias} value={h.alias}>{h.alias}</option>)}</select></label>
 <label>快照保存目录<input aria-label="快照保存目录" value={directory} disabled={busy} onChange={e=>{setDirectory(e.target.value);clear();}} placeholder="$HOME/项目快照"/></label><p>这是快照库的位置，独立于计算工作区。更换目录不会移动或删除旧快照；项目包在此目录的项目 ID 子目录中。</p>
 <div className="sync-actions"><button className="button" disabled={busy||!disk.ready||!alias||!directory||stopped||legacy||Boolean(state?.local?.restore_required)} onClick={()=>void run(()=>prepare('upload'))}>上传项目快照</button>
 <button className="button" disabled={busy||!disk.ready||!alias||!directory} onClick={()=>void run(async()=>{const r=revision.current;await remember();const result=await requestJson(`/api/project-sync/catalog?${query}`);if(r!==revision.current)return;setCatalog(result.projects);setTarget(result.projects.some((p:any)=>p.project_id===project.id)?project.id:result.projects[0]?.project_id||'');setPreview(null);setVersions([]);})}>读取服务器项目</button>
 <button className="button" disabled={busy||!disk.ready} onClick={()=>void run(async()=>{const r=await requestJson(`/api/projects/${project.id}/sync/refresh`,{});setState(r.status);setMessage(`已核查 ${r.records.length} 条已知任务；${r.records.filter((v:any)=>v.error).length} 条需核查。`);onChanged();})}>刷新远端记录</button>
 <button className="button" disabled={busy||!state?.local} onClick={()=>void run(async()=>{const r=await requestJson(`/api/projects/${project.id}/sync/recover`,{});setState(r);setMessage(r.message);notify();})}>核查原地址的传输回执</button></div>
 {catalog?.length===0&&<p>这个目录尚无项目快照。检查保存位置；更换地址没有删除原库。</p>}
 {catalog&&catalog.length>0&&<div className="sync-catalog"><label>服务器项目<select aria-label="服务器项目" value={target} disabled={busy} onChange={e=>{setTarget(e.target.value);setPreview(null);setVersion('');setVersions([]);}}>{catalog.map(p=><option key={p.project_id} value={p.project_id}>{p.name||'未命名项目'} · {p.counts?.attempts||0} 次运行 · {p.updated_at?new Date(p.updated_at).toLocaleString():''}</option>)}</select></label><button className="button" disabled={busy||!target||!disk.ready||legacy||stopped} onClick={()=>void run(()=>prepare('download',target))}>下载并还原</button><button className="text-button" disabled={busy||!target} onClick={()=>void run(async()=>{const r=revision.current;const result=await requestJson(`/api/projects/${target}/sync/versions?${query}`);if(r===revision.current)setVersions(result.versions);})}>查看历史版本</button></div>}
 {versions.length>0&&<label>恢复版本<select aria-label="恢复版本" value={version} disabled={busy} onChange={e=>{setVersion(e.target.value);setPreview(null);}}><option value="">最新版本</option>{versions.slice(1).map(v=><option key={v.version} value={v.version}>{new Date(v.updated_at).toLocaleString()} · {v.counts?.attempts||0} 次运行</option>)}</select></label>}
 {preview&&<section className="sync-preview" aria-label="项目快照预览"><h3>{preview.direction==='upload'?'上传快照预览':'下载还原预览'} · {preview.name}</h3><p>实际快照目录：<code>{preview.absolute_directory}</code></p><p>{preview.counts.attempts} 次运行 · {preview.counts.chains} 条链 · {preview.counts.scans} 个扫描 · {preview.counts.analyses} 份分析／数据集 · {preview.files} 个本机附件（{(preview.bytes/1024/1024).toFixed(1)} MiB）</p>
 <p>确认后暂停后续自动派发，原授权保留。已排队、运行的任务继续，不重投 UNKNOWN。</p>{preview.direction==='download'&&<p>恢复原项目身份和实际计算路径，其他项目保持；替换前完整备份本机记录。计算服务器的连接与目录在后续操作时核验。</p>}
 {preview.missing.length>0&&<p>原设备缺失 {preview.missing.length} 个附件，保留不可用说明，不自动重跑分析。</p>}{Boolean(preview.remote_operation)&&<p role="alert">服务器存在未完成的传输操作，须先核查回执。</p>}
 {preview.direction==='download'&&preview.local_changes&&<label><input type="checkbox" checked={choice} onChange={e=>setChoice(e.target.checked)}/>选择服务器版本，先备份本机未同步修改</label>}
 <div className="sync-actions"><button className="button primary" disabled={busy||!disk.ready||Boolean(preview.remote_operation)||(preview.direction==='download'&&preview.local_changes&&!choice)} onClick={()=>void run(confirm)}>{preview.direction==='upload'?'确认上传快照':'确认下载并还原'}</button><button className="button" disabled={busy} onClick={()=>setPreview(null)}>取消</button></div></section>}
 <details><summary>清理未发布的快照附件</summary>
  <p>只列出超过 24 小时且没有任何已发布版本引用的附件。当前及历史版本均保留。</p>
  <button className="button" disabled={busy||!alias||!directory||legacy||stopped} onClick={()=>void run(async()=>{
   const r=revision.current;
   const result:CleanupPreview=await requestJson(`/api/projects/${project.id}/sync/cleanup-objects`,{alias,snapshot_directory:directory});
   if(r===revision.current){setCleanup(result);setCleanupSelected([]);}
  })}>核对未引用附件</button>
  {cleanup&&<section className="sync-preview">
   <p>{cleanup.server.host} · <code>{cleanup.server.root}</code></p>
   {!cleanup.candidates.length&&<p>没有可清理的未发布附件。</p>}
   <div className="lifecycle-assets">{cleanup.candidates.map(file=><label key={file.sha256}><input type="checkbox" checked={cleanupSelected.includes(file.sha256)} disabled={busy}
    onChange={e=>setCleanupSelected(old=>e.target.checked?[...old,file.sha256]:old.filter(sha=>sha!==file.sha256))}/> <code>{file.sha256.slice(0,16)}</code> · {file.size_bytes.toLocaleString()} B</label>)}</div>
   {cleanup.candidates.length>0&&<button className="button danger" disabled={busy||!cleanupSelected.length} onClick={()=>void run(async()=>{
    const result=await requestJson(`/api/projects/${project.id}/sync/cleanup-objects`,{preview:cleanup,selected:cleanupSelected});
    setCleanup(null);setCleanupSelected([]);setMessage(`已删除 ${result.deleted.length} 个选中的未发布附件。`);
   })}>删除选中的未发布附件</button>}
  </section>}
 </details>
 {busy&&<p role="status">正在核验／传输，请保持工作台运行…</p>}{error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
 </details>;
}
