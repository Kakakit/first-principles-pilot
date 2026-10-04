import {useEffect,useState} from 'react';

export default function RemoteProgress({label}:{label:string}) {
  const [seconds,setSeconds]=useState(0);
  useEffect(()=>{const started=Date.now();const timer=window.setInterval(()=>setSeconds(Math.floor((Date.now()-started)/1000)),1000);return()=>window.clearInterval(timer);},[label]);
  return <p className="remote-progress" role="status" aria-live="polite"><span className="remote-progress-dot" aria-hidden="true"/>{label} · 已等待 {seconds} s。完成前不会重复触发或提交任务。</p>;
}
