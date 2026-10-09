import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import HistoryList from './HistoryList';
import {canRecycle} from './historyModel';
type Changed=()=>void|Promise<void>;
type Recycled={kind:string;key:string;label?:string;at:string;project_id?:string};
const notify=(kind:string,id:string,action:string)=>window.dispatchEvent(new CustomEvent('fpilot:history-changed',{detail:{kind,id,action}}));
function CleanupAction({kind,id,label,purge=false,onChanged,onNotice}:{kind:string;id:string;label:string;purge?:boolean;onChanged:Changed;onNotice?:(message:string)=>void}) {
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[references,setReferences]=useState<string[]>([]);
 const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{if(open)dialog.current?.showModal();else dialog.current?.close();},[open]);
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
 return <><button type="button" className={purge?'text-button cleanup-permanent':'record-remove'} aria-label={`${purge?'彻底清理本地数据':'删除记录'} · ${label}`} title={purge?'彻底清理本地数据':'删除记录 · 移入回收区'} onClick={e=>{e.stopPropagation();setError('');setReferences([]);setOpen(true);if(purge)void requestJson(`/api/recycle/${kind}/${id}/impact`).then(value=>setReferences(value.references||[])).catch(e=>setError(errorMessage(e)));}}>{purge?'彻底清理本地数据':'×'}</button><dialog ref={dialog} className="history-confirm" aria-label={purge?'确认彻底清理本地数据':'确认删除记录'} onCancel={e=>{e.preventDefault();if(!busy)setOpen(false);}}><h3>{purge?'彻底清理本地数据？':'删除这条记录？'}</h3><p className="cleanup-record-label">{label} · {id.slice(0,12)}</p><p>{kind==='analysis-datasets'?(purge?'将永久删除这份登记快照和本机展示设置；原分析与计算任务保留。':'将登记快照移入回收区，可随时恢复；原分析与计算任务保留。'):purge?'将永久删除这条记录及其本地文件、日志和缓存，无法恢复。引用这些文件的计划会暂停，之后需要重新选择来源。服务器任务继续运行，服务器文件不会删除。':'将从各页面的历史列表移除，保留在回收区，可随时恢复。后续自动派发会暂停；已提交的服务器任务继续运行。可在统一回收站恢复或清理本地文件。'}</p>{purge&&references.length>0&&<p>受影响的引用：{references.slice(0,5).join('、')}{references.length>5?' 等 '+references.length+' 项':''}。仍可按你的决定清理。</p>}{error&&<p role="alert" className="structure-error">{error}</p>}<div className="action-row"><button className="button" autoFocus disabled={busy} onClick={()=>setOpen(false)}>取消</button><button className="button danger" disabled={busy} onClick={()=>void remove()}>{busy?'正在清理…':purge?'确认彻底清理':'确认删除'}</button></div></dialog>{error&&!open&&<p className="structure-error" role="alert">{error}</p>}</>;
}
export function DeleteRecord({kind,id,state,label,onChanged}:{kind:string;id:string;state?:string;label:string;onChanged:Changed}) {
 if(!canRecycle(state,kind))return null;
 return <CleanupAction key={id} kind={kind} id={id} label={label} onChanged={onChanged}/>;
}
export const openRecycleBin=()=>window.dispatchEvent(new Event('fpilot:open-recycle'));
export function RecycleBin({kind,onChanged,title='统一回收站'}:{kind:string;projectId?:string;onChanged:Changed;title?:string}) {
 useEffect(()=>{const changed=(event:Event)=>{const detail=(event as CustomEvent).detail;if(detail.kind===kind&&(detail.action==='restore'||detail.action==='purge'))void Promise.resolve(onChanged()).catch(()=>{});};window.addEventListener('fpilot:history-changed',changed);return()=>window.removeEventListener('fpilot:history-changed',changed);},[kind,onChanged]);
 return <button className="text-button recycle-link" onClick={openRecycleBin}>{title} · 打开统一回收站</button>;
}
const kindLabels:Record<string,string>={'projects':'计算项目','inputs':'VASP 冻结输入','wannier-inputs':'Wannier90 冻结输入','attempts':'计算任务','chains':'计算链','scans':'迭代批次','job-templates':'任务脚本','analyses':'扩展分析','plots':'绘图','local-analyses':'本地分析','saved-plots':'保存的图表','comparisons':'比较记录','analysis-datasets':'后处理数据集'};
export function UnifiedRecycleBin({onRestoreProject}:{onRestoreProject:(id:string)=>Promise<void>}) {
 const [open,setOpen]=useState(false),[rows,setRows]=useState<Recycled[]>([]),[kind,setKind]=useState('all'),[search,setSearch]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[clearing,setClearing]=useState<Recycled[]|null>(null);
 const dialog=useRef<HTMLDialogElement>(null),confirm=useRef<HTMLDialogElement>(null);
 const load=async()=>setRows(await requestJson('/api/recycle'));
 useEffect(()=>{const show=()=>{setOpen(true);setError('');setNotice('');void load().catch(e=>setError(errorMessage(e)));};window.addEventListener('fpilot:open-recycle',show);return()=>window.removeEventListener('fpilot:open-recycle',show);},[]);
 useEffect(()=>{if(open)dialog.current?.showModal();else dialog.current?.close();},[open]);
 useEffect(()=>{if(clearing)confirm.current?.showModal();else confirm.current?.close();},[clearing]);
 useEffect(()=>{if(!open)return;const changed=()=>void load().catch(e=>setError(errorMessage(e)));window.addEventListener('fpilot:history-changed',changed);return()=>window.removeEventListener('fpilot:history-changed',changed);},[open]);
 const restore=async(row:Recycled)=>{setBusy(true);setError('');try{if(row.kind==='projects')await onRestoreProject(row.key);else await requestJson(`/api/recycle/${row.kind}/${row.key}`,undefined,'DELETE');notify(row.kind,row.key,'restore');await load();setNotice(row.kind==='analysis-datasets'?'已恢复登记快照。':'已恢复；自动计算计划保持暂停，需手动确认继续。');}catch(e){setError(errorMessage(e));}finally{setBusy(false);}};
 const clear=async()=>{if(!clearing)return;setBusy(true);setError('');try{const receipt=await requestJson('/api/recycle/empty',{confirm_local:true,items:clearing.map(({kind,key})=>({kind,key}))});for(const row of clearing)notify(row.kind,row.key,'purge');setClearing(null);await load();setNotice(receipt.deleted.some((r:{cleanup_pending?:boolean})=>r.cleanup_pending)?'记录已清理，部分文件尚未释放。':'已清空所选本地回收记录与文件。');}catch(e){setError(errorMessage(e));}finally{setBusy(false);}};
 const visible=rows.filter(r=>(kind==='all'||r.kind===kind)&&`${r.label||''} ${r.key}`.toLowerCase().includes(search.toLowerCase()));
 return <><dialog ref={dialog} className="unified-recycle" aria-label="统一回收站" onCancel={e=>{e.preventDefault();if(!busy)setOpen(false);}}><header><div><h2>回收站</h2><p>项目、任务、输入和报告集中管理。</p></div><button className="button" aria-label="关闭回收站" onClick={()=>setOpen(false)}>×</button></header><p className="recycle-notice">回收停止本机后续自动派发。服务器任务继续运行，清空只删除本地记录、日志、缓存与报告；外部原始文件和 POTCAR 库保留。</p><div className="filter-bar"><label>记录类型<select value={kind} onChange={e=>setKind(e.target.value)}><option value="all">全部类型</option>{Object.entries(kindLabels).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><label>搜索回收记录<input value={search} onChange={e=>setSearch(e.target.value)} placeholder="名称或任务编号"/></label><button className="button" disabled={busy} onClick={()=>void load().catch(e=>setError(errorMessage(e)))}>刷新</button><button className="button danger" disabled={busy||!rows.length} onClick={()=>setClearing([...rows])}>清空回收站 · {rows.length}</button></div>{error&&<p role="alert" className="structure-error">{error}</p>}{notice&&<p role="status">{notice}</p>}<div className="recycle-scroll"><HistoryList rows={visible.map(r=>({...r,id:r.kind+r.key}))} context={kind+search} render={row=><article className="recycle-row" key={row.id}><div><span className="recycle-kind">{kindLabels[row.kind]}</span><strong>{row.label||row.key.slice(0,12)}</strong><time>{new Date(row.at).toLocaleString()}</time></div><div className="action-row"><button className="button" disabled={busy} onClick={()=>void restore(row)}>恢复</button><CleanupAction kind={row.kind} id={row.key} label={row.label||row.key.slice(0,12)} purge onChanged={load} onNotice={setNotice}/></div></article>}/>{!visible.length&&<p className="empty-note">{rows.length?'没有符合条件的记录。':'回收站为空。'}</p>}</div></dialog><dialog ref={confirm} className="history-confirm" aria-label="确认清空回收站" onCancel={e=>{e.preventDefault();if(!busy)setClearing(null);}}><h2>清空本地回收站？</h2><p>永久删除 {clearing?.length||0} 条回收记录及其本地文件，无法恢复。已提交的服务器任务与文件不受影响，引用这些来源的计划需要重新配置。</p>{error&&<p role="alert" className="structure-error">{error}</p>}<div className="action-row"><button className="button" autoFocus disabled={busy} onClick={()=>setClearing(null)}>取消</button><button className="button danger" disabled={busy} onClick={()=>void clear()}>{busy?'正在清空…':'确认清空本地回收站'}</button></div></dialog></>;
}
