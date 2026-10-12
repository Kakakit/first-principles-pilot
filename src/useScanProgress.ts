import {useEffect,useRef,useState} from 'react';
import type {Dispatch,SetStateAction} from 'react';
import {errorMessage,requestJson} from './apiClient';
import {latestChain} from './chainDispatch';
import {pollScanBatch} from './scanDispatch';
import type {ScanProgress} from './scanDispatch';

export function useScanProgress<T extends ScanProgress>(projectId:string,nodeId:string,batch:T|null,
 setBatch:Dispatch<SetStateAction<T|null>>,setBatches:Dispatch<SetStateAction<T[]>>){
 const [dispatchId,track]=useState<string|null>(null),[error,setError]=useState('');
 const current=useRef({scope:projectId+':'+nodeId,id:batch?.id});
 const mounted=useRef(true);
 current.current={scope:projectId+':'+nodeId,id:batch?.id};
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const scope=projectId+':'+nodeId;
 const accept=(value:T)=>{
  if(!mounted.current||current.current.scope!==scope||current.current.id!==value.id)return;
  setBatch(old=>latestChain(old,value));
  setBatches(old=>old.map(item=>item.id===value.id?latestChain(item,value)!:item));
 };
 const read=(id:string):Promise<T>=>requestJson(`/api/scans/${encodeURIComponent(id)}?project_id=${encodeURIComponent(projectId)}`);
 useEffect(()=>{
  setError('');
  if(!batch||(batch.state!=='ACTIVE'&&batch.initial_dispatch?.state!=='IN_PROGRESS'&&dispatchId!==batch.id))return;
  return pollScanBatch(()=>read(batch.id),value=>{accept(value);setError('');},e=>setError('自动状态更新失败，当前显示上次记录：'+errorMessage(e)),dispatchId===batch.id);
 },[scope,batch?.id,batch?.state,batch?.initial_dispatch?.state,dispatchId]);
 return {accept,read,track,error};
}
