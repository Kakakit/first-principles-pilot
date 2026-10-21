import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import type {Dispatch,SetStateAction} from 'react';
import {cleanWorkspace,parseWorkspace} from './model';
import type {Workspace} from './model';
import {verifySource} from './structure';

export const MAX_BACKUP_BYTES=32*1024*1024;
async function responseJSON(response:Response) {
  let body;
  try{body=await response.json();}catch{throw new Error(response.ok?'本机工作区返回的数据不可读。':'本机服务暂不可用；请检查工作台是否正在运行。');}
  if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'本机工作区请求失败。');
  return body;
}
export async function checkedWorkspace(value:unknown) {
  const w=parseWorkspace(JSON.stringify(value));
  await Promise.all(w.projects.flatMap(p=>p.nodes.flatMap(n=>n.data.structure?[verifySource(n.data.structure)]:[])));
  return w;
}
export function useDiskWorkspace(workspace:Workspace,setWorkspace:Dispatch<SetStateAction<Workspace>>,legacyError:string) {
  const [ready,setReady]=useState(false),[status,setStatus]=useState('正在读取本机工作区…');
  const [error,setError]=useState(''),[path,setPath]=useState(''),[retry,setRetry]=useState(0);
  const latest=useRef(workspace),revision=useRef(0),saved=useRef(''),running=useRef<Promise<void>|null>(null),paused=useRef(false);
  const timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined),drain=useRef(false),flushLatest=useRef<()=>Promise<void>>(()=>Promise.resolve());
  latest.current=workspace;
  useEffect(()=>{
    const controller=new AbortController();let active=true;setReady(false);setError('');setStatus('正在读取本机工作区…');
    void (async()=>{
      const result=await responseJSON(await fetch('/api/workspace',{signal:controller.signal}));
      if(!active)return;
      let w:Workspace;
      if(result.workspace){w=await checkedWorkspace(result.workspace);}
      else {
        if(legacyError)throw new Error('旧浏览器存档不可读。请先导出原存档并恢复，暂不迁移演示内容。');
        w=cleanWorkspace(latest.current);
        await checkedWorkspace(w);
        const migrated=await responseJSON(await fetch('/api/workspace',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace:w,expected_revision:0,reason:'migration'}),signal:controller.signal}));
        result.revision=migrated.revision;
      }
      if(!active)return;
      saved.current=JSON.stringify(cleanWorkspace(w));revision.current=result.revision;paused.current=false;
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
          const body=JSON.stringify(cleanWorkspace(latest.current));
          if(body===saved.current)break;
          if(new TextEncoder().encode(body).length>MAX_BACKUP_BYTES)throw new Error('工作区超过 32 MiB，请先导出并拆分项目。');
          setStatus('正在保存到本机…');
          const result=await responseJSON(await fetch('/api/workspace',{method:'PUT',headers:{'Content-Type':'application/json'},body:'{"workspace":'+body+',"expected_revision":'+revision.current+',"reason":"autosave"}'}));
          revision.current=result.revision;saved.current=body;
          if(!drain.current&&JSON.stringify(cleanWorkspace(latest.current))!==body){
            setStatus('有修改待保存');timer.current=setTimeout(()=>void flushLatest.current(),1000);return;
          }
        }
        setStatus('已保存到本机工作区');
      }catch(e){paused.current=true;setStatus('尚未保存，请导出备份');setError((e as Error).message);}
      finally{running.current=null;drain.current=false;}
    };
    // Defer entry so running is assigned even if there is nothing to save.
    running.current=Promise.resolve().then(save);return running.current;
  },[ready]);
  flushLatest.current=flush;
  const serialized=useMemo(()=>JSON.stringify(cleanWorkspace(workspace)),[workspace]);
  useEffect(()=>{if(!ready)return;if(serialized===saved.current){if(!running.current&&!paused.current)setStatus('已保存到本机工作区');return;}setStatus('有修改待保存');timer.current=setTimeout(()=>void flush(),1000);return()=>clearTimeout(timer.current);},[serialized,ready,flush]);
  const finishEditing=useCallback(()=>{drain.current=true;void flush();},[flush]);
  useEffect(()=>{const hidden=()=>{if(document.hidden)finishEditing();};document.addEventListener('visibilitychange',hidden);return()=>document.removeEventListener('visibilitychange',hidden);},[finishEditing]);
  useEffect(()=>{const warn=(e:BeforeUnloadEvent)=>{if(ready&&JSON.stringify(cleanWorkspace(latest.current))!==saved.current){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[ready]);
  const exclusive=async<T,>(action:()=>Promise<T>):Promise<T>=>{
    await flush(true);
    if(paused.current)throw new Error('草稿尚未保存，不能开始接续。');
    paused.current=true;setReady(false);
    try{return await action();}
    finally{setRetry(n=>n+1);}
  };
  return {ready,status,error,path,flush,finishEditing,exclusive,reload:()=>setRetry(n=>n+1)};
}
