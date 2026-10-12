import type {InitialDispatch} from './chainDispatch';
import {statusLabel} from './statusLabels';

export type ScanProgress={id:string;revision?:number;state:string;initial_dispatch?:InitialDispatch;last_worker_error?:string};

export function scanCasePhase(state:string|undefined,batch:ScanProgress|null){
 if(!state)return '预览';
 if(state==='PENDING'&&batch?.initial_dispatch?.state==='IN_PROGRESS')return '正在准备';
 return statusLabel(state);
}

// Only local status reads: one request at a time, no remote refresh or dispatch.
export function pollScanBatch<T extends ScanProgress>(read:()=>Promise<T>,receive:(value:T)=>void,
 failed:(error:unknown)=>void,pendingAuthorization=false,fast=2000,normal=15000){
 let cancelled=false,timer:ReturnType<typeof setTimeout>|undefined;
 const poll=async()=>{
  let delay=normal;
  try{
   const value=await read();
   if(cancelled)return;
   receive(value);
   const initial=value.initial_dispatch?.state==='IN_PROGRESS';
   if(!initial&&value.state!=='ACTIVE'&&!(pendingAuthorization&&value.state==='FROZEN'))return;
   if(initial||pendingAuthorization)delay=fast;
  }catch(error){if(cancelled)return;failed(error);}
  if(!cancelled)timer=setTimeout(()=>void poll(),delay);
 };
 void poll();
 return ()=>{cancelled=true;clearTimeout(timer);};
}
