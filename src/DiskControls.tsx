import {useCallback,useEffect,useRef,useState} from 'react';
import {checkedWorkspace} from './useDiskWorkspace';
import type {Workspace} from './model';
import {saveBytes} from './structure';
import {requestJson,errorMessage} from './apiClient';
import {useDialogFocus} from './useDialogFocus';
import './workspace-recovery.css';

type Disk={ready:boolean;status:string;error:string;path:string;flush:(manual?:boolean)=>Promise<void>;checkpoint:(label?:string)=>Promise<void>;reload:()=>void};
type Props={disk:Disk;workspace:Workspace;legacyRaw:string;onAppend:(w:Workspace)=>void};
type Entry={revision:number;created_at:string;reason:string;label?:string};
const reasonName=(reason:string)=>({autosave:'历史恢复点',migration:'初始工作区',restore:'恢复前记录','project-sync-continuation':'跨设备还原'}[reason]||reason);

export default function DiskControls({disk,workspace,legacyRaw,onAppend}:Props) {
 const [entries,setEntries]=useState<Entry[]>([]),[preview,setPreview]=useState<Workspace|null>(null),[selected,setSelected]=useState('');
 const [label,setLabel]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const controller=useRef<AbortController|null>(null),previewRequest=useRef(0);
 const read=useCallback(async()=>{
  controller.current?.abort();const next=new AbortController();controller.current=next;setLoading(true);
  try{const rows=await requestJson('/api/workspace/history',undefined,undefined,{signal:next.signal,timeoutMs:15000});if(!next.signal.aborted){setEntries(rows);setError('');}}
  catch(e){if(!next.signal.aborted)setError(errorMessage(e));}
  finally{if(!next.signal.aborted)setLoading(false);}
 },[]);
 useEffect(()=>{void read();return()=>{controller.current?.abort();previewRequest.current++;};},[read]);
 const show=async(revision:number)=>{const ticket=++previewRequest.current;setPreview(null);setNotice('');setError('');try{const body=await requestJson(`/api/workspace/history/${revision}`,undefined,undefined,{timeoutMs:15000});const value=await checkedWorkspace(body.workspace);if(ticket===previewRequest.current){setPreview(value);setSelected(value.projects.some(p=>p.id===workspace.activeId)?workspace.activeId:value.activeId);}}catch(e){if(ticket===previewRequest.current)setError(errorMessage(e));}};
 const checkpoint=async()=>{setBusy(true);setNotice('');setError('');try{await disk.checkpoint(label);setLabel('');await read();setNotice('检查点已建立');}catch(e){setError(errorMessage(e));}finally{setBusy(false);}};
 const recover=()=>{const project=preview?.projects.find(p=>p.id===selected);if(!project)return;onAppend({activeId:project.id,projects:[project]});setPreview(null);setNotice('已恢复为新项目，原项目仍保留');};
 return <section className="disk-controls workspace-recovery">
  <div className="workspace-recovery-actions"><span role="status">{disk.status}</span><button className="button" disabled={!disk.ready||busy} onClick={()=>void disk.flush(true)}>立即保存 · ⌘/Ctrl S</button></div>
  <div className="workspace-checkpoint-create"><input aria-label="检查点备注（选填）" placeholder="检查点备注（选填）" value={label} maxLength={200} onChange={event=>setLabel(event.target.value)}/><button className="button" disabled={!disk.ready||busy} onClick={()=>void checkpoint()}>建立检查点</button></div>
  <div className="workspace-history-heading"><h3>恢复检查点</h3><button className="text-button" disabled={loading||busy} onClick={()=>void read()}>{loading?'正在读取…':'刷新'}</button></div>
  <p>自动检查点间隔至少 10 分钟，纯布局变化不生成。保留最近 30 个，合计最多 64 MiB；当前草稿始终保留。</p>
  <div className="workspace-recovery-feedback" aria-live="polite">{error||notice||disk.error}</div>
  {!preview&&<div className="workspace-recovery-list">{entries.map(entry=><button className="workspace-checkpoint-row" key={entry.revision} disabled={busy} onClick={()=>void show(entry.revision)}><strong>{entry.label||reasonName(entry.reason)}</strong><time>{new Date(entry.created_at).toLocaleString()}</time><span>查看与恢复</span></button>)}{!loading&&!entries.length&&<p>尚无检查点，可以先建立一个。</p>}</div>}
  {preview&&<section className="workspace-recovery-preview" aria-label="检查点恢复预览"><h3>恢复预览</h3><label>选择项目<select value={selected} onChange={event=>setSelected(event.target.value)}>{preview.projects.map(project=><option key={project.id} value={project.id}>{project.name} · {project.nodes.length} 个节点</option>)}</select></label><ul>{preview.projects.find(project=>project.id===selected)?.nodes.map(node=><li key={node.id}>{node.data.label}</li>)}</ul><p>恢复为一个新项目，保留当前项目和已有任务。这里只恢复流程草稿，不重投计算。</p><div className="workspace-recovery-actions"><button className="button primary" disabled={!disk.ready||busy} onClick={recover}>恢复为新项目</button><button className="button" onClick={()=>{previewRequest.current++;setPreview(null);}}>返回检查点列表</button></div></section>}
  <details className="workspace-recovery-location"><summary>工作区位置与旧草稿</summary><p className="hash">{disk.path||'尚未连接本机后端'}</p>{legacyRaw&&<button className="button" disabled={!disk.ready} onClick={()=>{void (async()=>{try{onAppend(await checkedWorkspace(JSON.parse(legacyRaw)));}catch(e){setError(errorMessage(e));}})();}}>追加旧浏览器草稿</button>}{disk.error&&<button className="button" onClick={()=>{saveBytes('fpilot-before-reload.json',JSON.stringify(workspace));disk.reload();}}>导出当前草稿并重新连接</button>}</details>
 </section>;
}

export function WorkspaceRecoveryDialog({onClose,...props}:Props&{onClose:()=>void}){
 useDialogFocus(true,'.workspace-recovery-dialog',onClose);
 return <div className="modal-backdrop"><section className="workspace-recovery-dialog" role="dialog" aria-modal="true" aria-labelledby="workspace-recovery-title"><header><h2 id="workspace-recovery-title">保存与恢复</h2><button className="icon-btn" aria-label="关闭保存与恢复" onClick={onClose}>×</button></header><DiskControls {...props}/></section></div>;
}
