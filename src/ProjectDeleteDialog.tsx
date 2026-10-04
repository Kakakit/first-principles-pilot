import {useEffect,useRef,useState} from 'react';
import type {Project} from './model';
import {errorMessage} from './apiClient';
export default function ProjectDeleteDialog({project,onClose,onDelete}:{project:Project;onClose:()=>void;onDelete:(project:Project)=>Promise<void>}) {
 const dialog=useRef<HTMLDialogElement>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{dialog.current?.showModal();},[]);
 return <dialog ref={dialog} className="history-confirm" aria-label="确认删除计算项目" onCancel={e=>{e.preventDefault();if(!busy)onClose();}}><h2>删除整个计算项目？</h2><p className="cleanup-record-label">{project.name} · {project.nodes.length} 个节点</p><p>项目、完整流程草稿及相关任务、输入和报告将移入统一回收站，可以恢复或清空本地文件。服务器任务与文件不受影响。</p><p>任何状态都可以回收。后续自动派发会暂停；已提交任务继续在服务器运行，恢复项目后需手动确认继续计划。</p>{error&&<p role="alert" className="structure-error">{error}</p>}<div className="action-row"><button autoFocus className="button" disabled={busy} onClick={onClose}>取消</button><button className="button danger" disabled={busy} onClick={()=>{setBusy(true);setError('');void onDelete(project).then(onClose).catch(e=>setError(errorMessage(e))).finally(()=>setBusy(false));}}>{busy?'正在删除…':'确认移入回收区'}</button></div></dialog>;
}
