import {errorMessage,requestJson} from './apiClient';
import {pollSerial} from './pollSerial';

export type TransferJob={id:string;producer_id:string;producer_kind:string;name:string;state:string;bytes_transferred:number;size_bytes:number;download_url?:string;error?:string};
type Snapshot={jobs:TransferJob[];error:string};
type Reader=(projectId:string,signal:AbortSignal)=>Promise<TransferJob[]>;
type Entry={listeners:Set<()=>void>;snapshot:Snapshot;controller:AbortController|null;pending:Promise<void>|null;dirty:boolean;disposed:boolean;stopPolling:(()=>void)|null};
const empty:Snapshot={jobs:[],error:''};
const active=(snapshot:Snapshot)=>snapshot.jobs.some(job=>job.state==='PENDING'||job.state==='RUNNING');

/** One request per project, shared by all mounted results; terminal jobs need no timer. */
export function createProjectTransfers(read:Reader,interval=2000){
 const entries=new Map<string,Entry>();
 const publish=(entry:Entry,snapshot:Snapshot)=>{
  if(entry.disposed)return;
  entry.snapshot=snapshot;
  for(const listener of entry.listeners)listener();
 };
 const refresh=(projectId:string,entry:Entry,fromPoll=false):Promise<void>=>{
  if(entry.disposed)return Promise.resolve();
  if(!fromPoll){entry.stopPolling?.();entry.stopPolling=null;}
  entry.dirty=true;
  if(entry.pending)return entry.pending;
  entry.pending=Promise.resolve().then(async()=>{
   // Actions during a read coalesce into one fresh read after the current one.
   while(entry.dirty&&!entry.disposed){
    entry.dirty=false;
    const controller=new AbortController();entry.controller=controller;
    try{
     const jobs=await read(projectId,controller.signal);
     if(!controller.signal.aborted)publish(entry,{jobs,error:''});
    }catch(cause){
     if(!controller.signal.aborted)publish(entry,{jobs:entry.snapshot.jobs,error:errorMessage(cause)});
    }
   }
  }).finally(()=>{
   entry.pending=null;entry.controller=null;
   if(entry.disposed)return;
   if(!active(entry.snapshot)){entry.stopPolling?.();entry.stopPolling=null;}
   else if(!entry.stopPolling)entry.stopPolling=pollSerial(()=>refresh(projectId,entry,true),()=>{},interval);
  });
  return entry.pending;
 };
 return {
  getSnapshot:(projectId:string)=>entries.get(projectId)?.snapshot||empty,
  subscribe:(projectId:string,listener:()=>void)=>{
   let entry=entries.get(projectId);
   if(!entry){
    entry={listeners:new Set(),snapshot:empty,controller:null,pending:null,dirty:false,disposed:false,stopPolling:null};
    entries.set(projectId,entry);
   }
   const current=entry,first=current.listeners.size===0;
   current.listeners.add(listener);
   if(first)void refresh(projectId,current);
   return ()=>{
    current.listeners.delete(listener);
    if(!current.listeners.size){
     current.disposed=true;current.controller?.abort();current.stopPolling?.();
     if(entries.get(projectId)===current)entries.delete(projectId);
    }
   };
  },
  refresh:(projectId:string)=>{
   const entry=entries.get(projectId);
   return entry?refresh(projectId,entry):Promise.resolve();
  },
 };
}

export const projectTransfers=createProjectTransfers(async(projectId,signal)=>{
 const result=await requestJson('/api/file-transfers?'+new URLSearchParams({project_id:projectId}),undefined,undefined,{signal});
 return result.jobs;
});
