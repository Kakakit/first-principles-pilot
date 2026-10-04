import {useState} from 'react';
import {checkedWorkspace} from './useDiskWorkspace';
import type {Workspace} from './model';
import {saveBytes} from './structure';

type Disk={ready:boolean;status:string;error:string;path:string;flush:(manual?:boolean)=>Promise<void>;reload:()=>void};
export default function DiskControls({disk,workspace,legacyRaw,onAppend}:{disk:Disk;workspace:Workspace;legacyRaw:string;onAppend:(w:Workspace)=>void}) {
  const [entries,setEntries]=useState<{revision:number;created_at:string;reason:string}[]>([]),[error,setError]=useState('');
  const read=async()=>{try{const r=await fetch('/api/workspace/history');if(!r.ok)throw new Error('历史记录读取失败。');setEntries(await r.json());setError('');}catch(e){setError((e as Error).message);}};
  const append=async(rev:number)=>{try{const r=await fetch(`/api/workspace/history/${rev}`);const b=await r.json();if(!r.ok)throw new Error(b.detail);onAppend(await checkedWorkspace(b.workspace));setError('');}catch(e){setError((e as Error).message);}};
  return <div className="disk-controls"><div className="disk-status"><span title={disk.path}>{disk.status}</span><button className="button" disabled={!disk.ready} onClick={()=>void disk.flush(true)}>立即保存</button><details><summary>工作区与恢复</summary><p className="hash">位置：{disk.path||'尚未连接本机后端'}</p><p>原始文件与历史快照保留在此目录。迁移后旧浏览器存档仍保留；历史恢复会追加项目，不覆盖当前项目。</p>{legacyRaw&&<button className="button" disabled={!disk.ready} onClick={()=>{void (async()=>{try{onAppend(await checkedWorkspace(JSON.parse(legacyRaw)));}catch(e){setError((e as Error).message);}})();}}>追加旧浏览器草稿</button>}<button className="button" onClick={()=>void read()}>读取最近 100 个快照</button><div className="revision-list">{entries.map(e=><div key={e.revision}><span>版本 {e.revision} · {new Date(e.created_at).toLocaleString()} · {e.reason}</span><button className="button" disabled={!disk.ready} onClick={()=>void append(e.revision)}>追加恢复</button></div>)}</div>{error&&<p role="alert">{error}</p>}</details></div>{disk.error&&<div className="structure-error" role="alert">{disk.error}<button className="button" onClick={()=>{saveBytes('fpilot-before-reload.json',JSON.stringify(workspace));disk.reload();}}>导出当前草稿并重新连接</button></div>}</div>;
}
