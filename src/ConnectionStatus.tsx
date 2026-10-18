import {useEffect,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {usePageVisible} from './usePageVisible';
type Health={state:string;retry_count:number;next_retry_at?:number;last_error?:string;last_success_at?:number};
export default function ConnectionStatus({alias}:{alias:string}){
 const visible=usePageVisible();const [health,setHealth]=useState<Health|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{if(!alias||!visible)return;const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;setHealth(null);
  const load=async()=>{try{const value=await requestJson('/api/server/connection-status?alias='+encodeURIComponent(alias),undefined,undefined,{signal:controller.signal});if(!controller.signal.aborted)setHealth(value.hosts[0]||null);}catch(e){if(!controller.signal.aborted)setError(errorMessage(e));}finally{if(!controller.signal.aborted)timer=setTimeout(()=>void load(),15000);}};
  void load();return()=>{controller.abort();clearTimeout(timer);};
 },[alias,visible]);
 if(!alias||(!health||health.state==='READY')&&!error)return null;
 const recover=async()=>{setBusy(true);setError('');try{const value=await requestJson('/api/server/recover-connection',{alias});setHealth(value.hosts[0]||null);if(value.receipt.exit_code===null||value.receipt.exit_code!==0)setError(value.receipt.stderr||'连接检测未通过');}catch(e){setError(errorMessage(e));}finally{setBusy(false);}};
 return <div className="connection-status" role="status"><p>{alias} · {health?.state==='PAUSED'?'自动连接已暂停':health?.state==='COOLDOWN'?'连接冷却中':'连接监控就绪'}{health&&health.state!=='READY'?` · 已重试 ${health.retry_count}/5 次`:''}{health?.next_retry_at?` · 下次 ${new Date(health.next_retry_at*1000).toLocaleTimeString()}`:''}</p>{health?.state!=='READY'&&health&&<><p>连接失败后每隔 15 分钟重试，连续 5 次重试失败后暂停。原任务与授权保留。</p><button type="button" className="button" disabled={busy} onClick={()=>void recover()}>{busy?'正在检测一次连接…':'检测并恢复连接'}</button>{health.last_error&&<details><summary>连接错误</summary><pre className="input-text">{health.last_error}</pre></details>}</>}{error&&<p role="alert">{error}</p>}</div>;
}
