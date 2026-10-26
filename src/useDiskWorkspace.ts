import {useCallback,useEffect,useRef,useState} from 'react';
import type {Dispatch,SetStateAction} from 'react';
import {cleanWorkspace,parseWorkspace} from './model';
import type {Workspace} from './model';
import {verifySource} from './structure';
import {WorkspacePersistence,WorkspaceRequestError,workspaceRequest,workspaceSerializer} from './workspacePersistence';

export const MAX_BACKUP_BYTES=32*1024*1024;
export async function checkedWorkspace(value:unknown) {
  const w=parseWorkspace(JSON.stringify(value));
  await Promise.all(w.projects.flatMap(p=>p.nodes.flatMap(n=>n.data.structure?[verifySource(n.data.structure)]:[])));
  return w;
}
export function useDiskWorkspace(workspace:Workspace,setWorkspace:Dispatch<SetStateAction<Workspace>>,legacyError:string) {
  const [ready,setReady]=useState(false),[status,setStatus]=useState('正在读取本机工作区…');
  const [error,setError]=useState(''),[path,setPath]=useState(''),[retry,setRetry]=useState(0);
  const latest=useRef(workspace),persistence=useRef(new WorkspacePersistence()),serialize=useRef(workspaceSerializer()),running=useRef<Promise<void>|null>(null),paused=useRef(false);
  const mounted=useRef(true),failures=useRef(0);
  const timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined),drain=useRef(false),flushLatest=useRef<()=>Promise<void>>(()=>Promise.resolve());
  latest.current=workspace;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;clearTimeout(timer.current);};},[]);
  useEffect(()=>{
    const controller=new AbortController();let active=true;setReady(false);setError('');setStatus('正在读取本机工作区…');
    void (async()=>{
      const result=await workspaceRequest({signal:controller.signal});
      if(!active)return;
      let w:Workspace;
      if(result.workspace){w=await checkedWorkspace(result.workspace);}
      else {
        if(legacyError)throw new Error('旧浏览器存档不可读。请先导出原存档并恢复，暂不迁移演示内容。');
        w=cleanWorkspace(latest.current);
        await checkedWorkspace(w);
        const migrated=await workspaceRequest({method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace:w,expected_revision:0,reason:'migration'}),signal:controller.signal});
        result.revision=migrated.revision;
      }
      if(!active)return;
      persistence.current.initialize(serialize.current(w),result.revision);paused.current=false;failures.current=0;
      setWorkspace(w);setPath(result.path);setReady(true);setStatus('已保存到本机工作区');
    })().catch(e=>{if(active){const detail=e instanceof DOMException||e instanceof TypeError?'无法连接本机工作台；请确认工作台启动窗口仍在运行。':(e as Error).message;setError(detail);setStatus('工作区未连接');}});
    return()=>{active=false;controller.abort();};
  },[retry,legacyError,setWorkspace]);
  const flush=useCallback((manual=false):Promise<void>=>{
    if(!ready)return Promise.resolve();
    clearTimeout(timer.current);
    if(manual){paused.current=false;drain.current=true;setError('');}
    if(paused.current)return Promise.resolve();
    if(running.current)return running.current;
    const save=async()=>{
      try{
        while(true){
          const body=serialize.current(latest.current);
          if(body===persistence.current.saved&&persistence.current.pendingBody===null)break;
          if(new TextEncoder().encode(body).length>MAX_BACKUP_BYTES)throw new Error('工作区超过 32 MiB，请先导出并拆分项目。');
          setStatus('正在保存到本机…');
          await persistence.current.save(body);
          if(!mounted.current)return;
          failures.current=0;setError('');
          if(!drain.current&&serialize.current(latest.current)!==body){
            setStatus('有修改待保存');timer.current=setTimeout(()=>void flushLatest.current(),1000);return;
          }
        }
        failures.current=0;setError('');setStatus('已保存到本机工作区');
      }catch(e){
        if(!mounted.current)return;
        const recoverable=e instanceof WorkspaceRequestError&&e.retryable;
        paused.current=!recoverable;
        setStatus(recoverable?'连接恢复后将自动保存':'尚未保存，编辑内容已保留');setError((e as Error).message);
        if(recoverable)timer.current=setTimeout(()=>void flushLatest.current(),Math.min(30_000,2000*2**Math.min(failures.current++,4)));
      }
      finally{running.current=null;drain.current=false;}
    };
    // Defer entry so running is assigned even if there is nothing to save.
    running.current=Promise.resolve().then(save);return running.current;
  },[ready]);
  flushLatest.current=flush;
  useEffect(()=>{if(!ready)return;setStatus('有修改待保存');timer.current=setTimeout(()=>void flush(),1000);return()=>clearTimeout(timer.current);},[workspace,ready,flush]);
  const finishEditing=useCallback(()=>{drain.current=true;void flush();},[flush]);
  useEffect(()=>{const hidden=()=>{if(document.hidden)finishEditing();};document.addEventListener('visibilitychange',hidden);return()=>document.removeEventListener('visibilitychange',hidden);},[finishEditing]);
  useEffect(()=>{const warn=(e:BeforeUnloadEvent)=>{if(ready&&(serialize.current(latest.current)!==persistence.current.saved||persistence.current.pendingBody!==null)){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[ready]);
  const exclusive=async<T,>(action:()=>Promise<T>):Promise<T>=>{
    await flush(true);
    if(paused.current||persistence.current.pendingBody!==null||serialize.current(latest.current)!==persistence.current.saved)throw new Error('草稿尚未保存，连接恢复后再继续。');
    paused.current=true;setReady(false);
    try{return await action();}
    finally{setRetry(n=>n+1);}
  };
  return {ready,status,error,path,flush,finishEditing,exclusive,reload:()=>setRetry(n=>n+1)};
}
