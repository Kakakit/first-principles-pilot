import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import HistoryList from './HistoryList';
import LifecycleDialog,{assetState,lifecycleKinds} from './LifecycleDialog';
import type {RemoteAsset} from './LifecycleDialog';
import type {Project,Step} from './model';
import {canRecycle} from './historyModel';
type Changed=()=>void|Promise<void>;
type Recycled={kind:string;key:string;label?:string;at:string;project_id?:string;node?:Step;edges?:Project['edges']};
const notify=(kind:string,id:string,action:string)=>window.dispatchEvent(new CustomEvent('fpilot:history-changed',{detail:{kind,id,action}}));
function CleanupAction({kind,id,label,purge=false,onChanged,onNotice}:{kind:string;id:string;label:string;purge?:boolean;onChanged:Changed;onNotice?:(message:string)=>void}) {
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[references,setReferences]=useState<string[]>([]);
 const dialog=useRef<HTMLDialogElement>(null),cancel=useRef<HTMLButtonElement>(null);
 useEffect(()=>{if(open){dialog.current?.showModal();cancel.current?.focus();}else dialog.current?.close();},[open]);
 const remove=async()=>{
  setBusy(true);setError('');
  try {
   const receipt=await requestJson(`/api/recycle/${kind}/${id}${purge?'/purge':''}`,purge?{confirm_local:true}:{});
   setOpen(false);
   notify(kind,id,purge?'purge':'recycle');
   if(receipt.cleanup_pending){const message='记录已清理；部分文件尚未释放，请检查 '+receipt.cleanup_directory;if(onNotice)onNotice(message);else setError(message);}
   try{await onChanged();}catch(e){setError('操作已完成，列表刷新失败：'+errorMessage(e));}
  }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 };
 return <><button type="button" className={purge?'text-button cleanup-permanent':kind==='job-templates'?'text-button cleanup-permanent':'record-remove'} aria-label={`${purge?'彻底清理本地数据':kind==='job-templates'?'删除脚本':'删除记录'} · ${label}`} title={purge?'彻底清理本地数据':kind==='job-templates'?'删除脚本 · 移入回收站':'删除记录 · 移入回收区'} onClick={e=>{e.stopPropagation();setError('');setReferences([]);setOpen(true);if(purge)void requestJson(`/api/recycle/${kind}/${id}/impact`).then(value=>setReferences(value.references||[])).catch(e=>setError(errorMessage(e)));}}>{purge?'彻底清理本地数据':kind==='job-templates'?'删除脚本':'×'}</button><dialog ref={dialog} className="history-confirm" aria-label={purge?'确认彻底清理本地数据':'确认删除记录'} onCancel={e=>{e.preventDefault();if(!busy)setOpen(false);}}><h3>{purge?'彻底清理本地数据？':'删除这条记录？'}</h3><p className="cleanup-record-label">{label}</p><details><summary>记录身份核验</summary><code>{id}</code></details><p>{kind==='job-templates'&&!purge?'将脚本移入回收站，可恢复。历史输入和任务保留；使用此脚本的未派发计划会暂停。删除当前默认脚本后，请重新选择并保存设置。':kind==='analysis-datasets'?(purge?'将永久删除这份登记快照和本机展示设置；原分析与计算任务保留。':'将登记快照移入回收区，可随时恢复；原分析与计算任务保留。'):purge?'将永久删除这条记录及其本地文件、日志和缓存，无法恢复。引用这些文件的计划会暂停，之后需要重新选择来源。服务器任务继续运行，服务器文件不会删除。':'将从各页面的历史列表移除，保留在回收区，可随时恢复。后续自动派发会暂停；已提交的服务器任务继续运行。可在统一回收站恢复或清理本地文件。'}</p>{purge&&references.length>0&&<p>受影响的引用：{references.slice(0,5).join('、')}{references.length>5?' 等 '+references.length+' 项':''}。仍可按你的决定清理。</p>}{error&&<p role="alert" className="structure-error">{error}</p>}<div className="action-row"><button ref={cancel} className="button" autoFocus disabled={busy} onClick={()=>setOpen(false)}>取消</button><button className="button danger" disabled={busy} onClick={()=>void remove()}>{busy?'正在清理…':purge?'确认彻底清理':'确认删除'}</button></div></dialog>{error&&!open&&<p className="structure-error" role="alert">{error}</p>}</>;
}
export function DeleteRecord({kind,id,state,label,onChanged}:{kind:string;id:string;state?:string;label:string;onChanged:Changed}) {
 if(!canRecycle(state,kind))return null;
 if(lifecycleKinds.has(kind))return <ManagedRecord key={id} kind={kind} id={id} label={label} onChanged={onChanged}/>;
 return <CleanupAction key={id} kind={kind} id={id} label={label} onChanged={onChanged}/>;
}

function ManagedRecord({kind,id,label,onChanged}:{kind:string;id:string;label:string;onChanged:Changed}){
 const [open,setOpen]=useState(false);
 return <><button type="button" className="record-remove" aria-label={'删除记录 · '+label} onClick={e=>{e.stopPropagation();setOpen(true);}}>×</button>
  {open&&<LifecycleDialog target={{kind,key:id}} label={label} onClose={()=>setOpen(false)} onDone={()=>{notify(kind,id,'recycle');void Promise.resolve(onChanged()).catch(()=>{});}}/>}
 </>;
}
function RemoteCleanupList(){
 const [rows,setRows]=useState<RemoteAsset[]>([]),[error,setError]=useState(''),[selected,setSelected]=useState<RemoteAsset|null>(null),[busy,setBusy]=useState(false);
 const [preview,setPreview]=useState<{token:string;alias:string;path:string}|null>(null);
 const load=()=>requestJson('/api/cleanup').then(setRows).catch(e=>setError(errorMessage(e)));
 useEffect(()=>{void load();const timer=window.setInterval(()=>void load(),5000);return()=>clearInterval(timer);},[]);
 const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{if(selected)dialog.current?.showModal();else dialog.current?.close();},[selected]);
 async function readRecovery(row:RemoteAsset){
  setBusy(true);setError('');setPreview(null);
  try{setPreview(await requestJson('/api/cleanup/assets/'+row.id+'/recovery-preview',{},'POST',{timeoutMs:40_000}));}
  catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 }
 function select(row:RemoteAsset){
  setSelected(row);setPreview(null);setError('');
  if(row.recovery_required)void readRecovery(row);
 }
 async function clean(checkOriginal=false){
  if(!selected)return;
  setBusy(true);setError('');
  try{
   if(selected.recovery_required&&!checkOriginal){
    if(!preview)return;
    await requestJson('/api/cleanup/assets/'+selected.id+'/recover',{token:preview.token});
   }else await requestJson('/api/cleanup/assets',{asset_ids:[selected.id],...(checkOriginal?{check_operation:selected.operation}:{})});
   setSelected(null);await load();
  }
  catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 }
 return <section className="remote-cleanup-list" aria-label="远端文件">
  <p>这里保留服务器和原目录。清空本地回收站后仍可核对，目录大小未统计。</p>
  <div className="action-row"><button className="button" onClick={()=>void load()}>刷新远端清理状态</button>
   {rows.some(row=>['pending','running','waiting','unknown'].includes(row.state))&&<button className="button" disabled={busy} onClick={()=>{setBusy(true);setError('');void requestJson('/api/cleanup/check',{}).then(load).catch(e=>setError(errorMessage(e))).finally(()=>setBusy(false));}}>接续已确认的清理</button>}
  </div>
  <div className="recycle-scroll"><HistoryList rows={rows} context="remote-files" render={row=><article className="recycle-row" key={row.id}>
   <div><strong>{row.alias} · {row.label}</strong><span>{assetState[row.state]||row.state}{row.owner_state?' · '+row.owner_state:''}{row.project_id?' · 项目 '+row.project_id:''}</span><code>{row.path}</code>{row.retained_at&&<time>{new Date(row.retained_at).toLocaleString()}</time>}{row.error&&<small>{row.error}</small>}</div>
   {['retained','blocked','failed'].includes(row.state)&&<button className="button" disabled={busy} onClick={()=>select(row)}>{row.recovery_required?'重新预览当前目录':row.state==='retained'?'清理此目录':'重新核对'}</button>}
  </article>}/>{!rows.length&&<p>尚无登记的保留目录或清理回执。</p>}</div>
  {error&&!selected&&<p role="alert">{error}</p>}
  <dialog ref={dialog} className="history-confirm" aria-label="清理保留的远端目录" onCancel={e=>{e.preventDefault();if(!busy)setSelected(null);}}>
   <h3>{selected?.recovery_required?'重新确认清理当前目录？':'清理保留的远端目录？'}</h3>
   <p>{preview?.alias??selected?.alias}</p><code>{preview?.path??selected?.path}</code>
   <p>{selected?.recovery_required?'确认后会发起一次新的清理，旧回执保留。远端删除不可恢复。':'仅处理此目录。远端删除不可恢复。'}</p>
   {selected?.recovery_required&&busy&&!preview&&<p role="status">正在只读核对当前目录…</p>}
   {error&&<p role="alert">{error}</p>}
   <div className="action-row"><button className="button" autoFocus disabled={busy} onClick={()=>setSelected(null)}>取消</button>
    {selected?.recovery_required&&error&&<>{selected.operation&&<button className="text-button" disabled={busy} onClick={()=>void clean(true)}>核对原操作</button>}
     <button className="button" disabled={busy} onClick={()=>void readRecovery(selected)}>重试预览</button></>}
    <button className="button danger" disabled={busy||Boolean(selected?.recovery_required&&(!preview||error))} onClick={()=>void clean()}>{selected?.recovery_required?'确认清理当前目录':'清理此目录'}</button>
   </div>
  </dialog>
 </section>;
}

export const openRecycleBin=()=>window.dispatchEvent(new Event('fpilot:open-recycle'));
export function RecycleBin({kind,onChanged,title='统一回收站'}:{kind:string;projectId?:string;onChanged:Changed;title?:string}) {
 useEffect(()=>{const changed=(event:Event)=>{const detail=(event as CustomEvent).detail;if(detail.kind===kind&&(detail.action==='restore'||detail.action==='purge'))void Promise.resolve(onChanged()).catch(()=>{});};window.addEventListener('fpilot:history-changed',changed);return()=>window.removeEventListener('fpilot:history-changed',changed);},[kind,onChanged]);
 return <button className="text-button recycle-link" onClick={openRecycleBin}>{title} · 打开统一回收站</button>;
}
const kindLabels:Record<string,string>={'projects':'计算项目','nodes':'流程节点','inputs':'VASP 冻结输入','wannier-inputs':'Wannier90 冻结输入','attempts':'计算任务','chains':'计算链','scans':'迭代批次','job-templates':'任务脚本','analyses':'扩展分析','plots':'绘图','local-analyses':'本地分析','saved-plots':'保存的图表','comparisons':'比较记录','analysis-datasets':'后处理数据集'};
export function UnifiedRecycleBin({onRestoreProject,onRestoreNode}:{onRestoreProject:(id:string)=>Promise<void>;onRestoreNode:(row:Recycled)=>Promise<void>}) {
 const [open,setOpen]=useState(false),[rows,setRows]=useState<Recycled[]>([]),[kind,setKind]=useState('all'),[search,setSearch]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[clearing,setClearing]=useState<Recycled[]|null>(null);
 const [remote,setRemote]=useState(false);
 const dialog=useRef<HTMLDialogElement>(null),confirm=useRef<HTMLDialogElement>(null);
 const load=async()=>setRows(await requestJson('/api/recycle'));
 useEffect(()=>{const show=()=>{setOpen(true);setError('');setNotice('');void load().catch(e=>setError(errorMessage(e)));};window.addEventListener('fpilot:open-recycle',show);return()=>window.removeEventListener('fpilot:open-recycle',show);},[]);
 useEffect(()=>{if(open)dialog.current?.showModal();else dialog.current?.close();},[open]);
 useEffect(()=>{if(clearing)confirm.current?.showModal();else confirm.current?.close();},[clearing]);
 useEffect(()=>{if(!open)return;const changed=()=>void load().catch(e=>setError(errorMessage(e)));window.addEventListener('fpilot:history-changed',changed);return()=>window.removeEventListener('fpilot:history-changed',changed);},[open]);
 const restore=async(row:Recycled)=>{setBusy(true);setError('');try{let result;if(row.kind==='projects')await onRestoreProject(row.key);else if(row.kind==='nodes')await onRestoreNode(row);else result=await requestJson(`/api/recycle/${row.kind}/${row.key}`,undefined,'DELETE');notify(row.kind,row.key,'restore');await load();setNotice(result?.remote_cleaned?'已恢复本地记录。已清理的远端目录无法恢复。':row.kind==='analysis-datasets'?'已恢复登记快照。':'已恢复本地记录。远端清理状态可在“远端文件”查看，自动计划保持暂停。');}catch(e){setError(errorMessage(e));}finally{setBusy(false);}};
 const clear=async()=>{if(!clearing)return;setBusy(true);setError('');try{const receipt=await requestJson('/api/recycle/empty',{confirm_local:true,items:clearing.map(({kind,key})=>({kind,key}))});for(const row of clearing)notify(row.kind,row.key,'purge');setClearing(null);await load();setNotice(receipt.deleted.some((r:{cleanup_pending?:boolean})=>r.cleanup_pending)?'记录已清理，部分文件尚未释放。':'已清空所选本地回收记录与文件。');}catch(e){setError(errorMessage(e));}finally{setBusy(false);}};
 const visible=rows.filter(r=>(kind==='all'||r.kind===kind)&&`${r.label||''} ${r.key}`.toLowerCase().includes(search.toLowerCase()));
 return <><dialog ref={dialog} className="unified-recycle" aria-label="统一回收站" onCancel={e=>{e.preventDefault();if(!busy)setOpen(false);}}><header><div><h2>回收站</h2><p>项目、任务、输入和报告集中管理。</p></div><button className="button" aria-label="关闭回收站" onClick={()=>setOpen(false)}>×</button></header><p className="recycle-notice">回收停止本机后续自动派发。服务器任务继续运行，清空只删除本地记录、日志、缓存与报告；外部原始文件和 POTCAR 库保留。</p><div className="action-row"><button className="button" aria-pressed={!remote} onClick={()=>setRemote(false)}>本地记录</button><button className="button" aria-pressed={remote} onClick={()=>setRemote(true)}>远端文件</button></div>{remote&&open?<RemoteCleanupList/>:<><div className="filter-bar"><label>记录类型<select value={kind} onChange={e=>setKind(e.target.value)}><option value="all">全部类型</option>{Object.entries(kindLabels).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><label>搜索回收记录<input value={search} onChange={e=>setSearch(e.target.value)} placeholder="名称或任务编号"/></label><button className="button" disabled={busy} onClick={()=>void load().catch(e=>setError(errorMessage(e)))}>刷新</button><button className="button danger" disabled={busy||!rows.length} onClick={()=>setClearing([...rows])}>清空回收站 · {rows.length}</button></div>{error&&<p role="alert" className="structure-error">{error}</p>}{notice&&<p role="status">{notice}</p>}<div className="recycle-scroll"><HistoryList rows={visible.map(r=>({...r,id:r.kind+r.key}))} context={kind+search} render={row=><article className="recycle-row" key={row.id}><div><span className="recycle-kind">{kindLabels[row.kind]}</span><strong>{row.label||kindLabels[row.kind]+' · '+new Date(row.at).toLocaleString()}</strong><time>{new Date(row.at).toLocaleString()}</time></div><div className="action-row"><button className="button" disabled={busy} onClick={()=>void restore(row)}>恢复</button><CleanupAction kind={row.kind} id={row.key} label={row.label||kindLabels[row.kind]+' · '+new Date(row.at).toLocaleString()} purge onChanged={load} onNotice={setNotice}/></div></article>}/>{!visible.length&&<p className="empty-note">{rows.length?'没有符合条件的记录。':'回收站为空。'}</p>}</div></>}</dialog><dialog ref={confirm} className="history-confirm" aria-label="确认清空回收站" onCancel={e=>{e.preventDefault();if(!busy)setClearing(null);}}><h2>清空本地回收站？</h2><p>永久删除 {clearing?.length||0} 条回收记录及其本地文件，无法恢复。已提交的服务器任务与文件不受影响，引用这些来源的计划需要重新配置。</p>{error&&<p role="alert" className="structure-error">{error}</p>}<div className="action-row"><button className="button" autoFocus disabled={busy} onClick={()=>setClearing(null)}>取消</button><button className="button danger" disabled={busy} onClick={()=>void clear()}>{busy?'正在清空…':'确认清空本地回收站'}</button></div></dialog></>;
}
