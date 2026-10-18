import {useEffect,useState} from 'react';

export default function RemoteProgress({label}:{label:string}) {
  const [seconds,setSeconds]=useState(0);
  useEffect(()=>{setSeconds(0);const started=Date.now();const timer=window.setInterval(()=>setSeconds(Math.floor((Date.now()-started)/1000)),1000);return()=>window.clearInterval(timer);},[label]);
  return <p className="remote-progress" role="status"><span className="remote-progress-dot" aria-hidden="true"/>{label}<span aria-hidden="true" style={{fontVariantNumeric:'tabular-nums',minWidth:'5ch'}}> · {seconds} s</span></p>;
}
