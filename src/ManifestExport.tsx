import {useEffect,useRef,useState} from 'react';
import {errorMessage,readJson} from './apiClient';

type Props={projectId:string;kind:'batch'|'attempt'|'analysis';id:string};

export default function ManifestExport({projectId,kind,id}:Props){
 const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 const active=useRef<AbortController|null>(null);
 useEffect(()=>()=>{active.current?.abort();},[]);
 const download=async()=>{
  if(active.current)return;
  const controller=new AbortController();active.current=controller;
  let expired=false;
  const timer=setTimeout(()=>{expired=true;controller.abort();},30_000);
  setBusy(true);setMessage('');setError('');
  try{
   const query=new URLSearchParams({project_id:projectId});
   const path=kind==='batch'
    ?`/api/scans/${encodeURIComponent(id)}/manifest?${query}&format=zip`
    :'/api/artifact-refs?'+new URLSearchParams({project_id:projectId,producer_kind:kind,producer_id:id});
   const response=await fetch(path,{signal:controller.signal});
   if(response.status===404)throw new Error('当前后端尚未加载清单接口，请重启工作台后重试。');
   if(!response.ok)await readJson(response);
   const blob=kind==='batch'?await response.blob():new Blob([JSON.stringify(await readJson(response),null,2)+'\n'],{type:'application/json;charset=utf-8'});
   if(controller.signal.aborted)return;
   const url=URL.createObjectURL(blob),link=document.createElement('a');
   link.href=url;link.download=kind==='batch'?`batch-manifest-${id}.zip`:`artifact-refs-${id}.json`;
   document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);
   setMessage(kind==='batch'?'已生成清单包，含 batch.json、cases.csv 和字段说明。':'已生成产物引用清单，可按生产者和文件身份解析。');
  }catch(caught){
   if(!controller.signal.aborted||expired)setError(expired?'清单生成超时，请重试。':errorMessage(caught));
  }finally{
   clearTimeout(timer);active.current=null;
   if(!controller.signal.aborted||expired)setBusy(false);
  }
 };
 return <div className="manifest-export"><button className="button" disabled={busy} onClick={()=>void download()}>{busy?'正在生成清单…':kind==='batch'?'导出数据清单':'导出产物引用'}</button>{message&&<p role="status">{message}</p>}{error&&<p role="alert">{error}</p>}</div>;
}
