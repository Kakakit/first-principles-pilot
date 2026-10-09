import {statusLabel} from './statusLabels';

export type InitialDispatch={state:'IN_PROGRESS'|'FINISHED';started_at:string;finished_at?:string};
type StepStatus={state:string;attempt_state?:string;job_id?:string|null;waiting_kind?:string;waiting_reason?:string|null;blocker?:string|null};

export function chainStepPhase(step:StepStatus,initial=false){
 if(step.state==='DONE')return '已完成';
 if(step.attempt_state==='UNKNOWN')return statusLabel('UNKNOWN');
 if(step.state==='BLOCKED')return statusLabel('BLOCKED');
 if(step.attempt_state)return statusLabel(step.attempt_state);
 if(step.waiting_kind==='DEPENDENCY')return '等待上游';
 if(step.waiting_kind==='QUOTA')return '等待额度';
 if(step.waiting_reason)return '等待资源';
 if(initial&&step.state==='PENDING')return '正在准备';
 return statusLabel(step.state);
}

export function latestChain<T extends {id:string;revision?:number}>(current:T|null,incoming:T){
 if(current&&current.id!==incoming.id)return current;
 return current&&(current.revision||0)>(incoming.revision||0)?current:incoming;
}

// Only the initial pass polls, and only the local read endpoint. Keep at most
// one request in flight and discard its reply after a project/run switch.
export function pollInitialChain<T extends {initial_dispatch?:InitialDispatch}>(
 read:()=>Promise<T>,receive:(value:T)=>void,finished:()=>void,interval=2000,
){
 let cancelled=false;
 let timer:ReturnType<typeof setTimeout>|undefined;
 const poll=async()=>{
  try{
   const value=await read();
   if(cancelled)return;
   receive(value);
   if(value.initial_dispatch?.state==='FINISHED'){finished();return;}
  }catch{if(cancelled)return;}
  if(!cancelled)timer=setTimeout(()=>void poll(),interval);
 };
 void poll();
 return ()=>{cancelled=true;clearTimeout(timer);};
}
