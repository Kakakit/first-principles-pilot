import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {usePageVisible} from './usePageVisible';
import type {ResultSummary} from './resultWorkspace';

export function useSummaryCollection(projectId:string,attemptId:string,enabled:boolean,onSummary:(data:ResultSummary)=>void,readExisting=false){
 const visible=usePageVisible(),identity=JSON.stringify([projectId,attemptId]);
 const [status,setStatus]=useState({identity:'',message:'',failed:false,busy:false});
 const [sequence,setSequence]=useState(0);
 const manual=useRef<{identity:string;checkedAt?:string}|null>(null);
 const received=useRef(onSummary);received.current=onSummary;
 useEffect(()=>{
   if(!enabled||!visible||!attemptId)return;
   const controller=new AbortController(),options={signal:controller.signal};
   let timer:ReturnType<typeof setTimeout>;
   const query='?project_id='+encodeURIComponent(projectId);
   const base='/api/results/'+encodeURIComponent(attemptId);
   const url=base+'/summary-collection'+query;
   const show=(message:string,failed=false,busy=true)=>setStatus({identity,message,failed,busy});
   async function check(first=false){
     try{
       if(first&&readExisting){
         const data:ResultSummary=await requestJson(base+'/summary'+query,undefined,undefined,options);
         if(controller.signal.aborted)return;
         received.current(data);
         if(data.metrics&&!manual.current){show('',false,false);return;}
       }
       const action=first&&manual.current?.identity===identity?manual.current:null;
       if(action)manual.current=null;
       let state=await requestJson(url+(action?'&retry=true'+(action.checkedAt?'&checked_at='+encodeURIComponent(action.checkedAt):''):''),undefined,action?'POST':'GET',options);
       if(controller.signal.aborted)return;
       if(state.state==='missing'){
         state=await requestJson(url,undefined,'POST',options);
         if(controller.signal.aborted)return;
       }
       if(state.state==='ready'){
         const data:ResultSummary=await requestJson(base+'/summary'+query,undefined,undefined,options);
         if(!controller.signal.aborted){received.current(data);show('',false,false);}
       }else if(state.state==='failed'){
         show(state.error||'摘要采集暂未成功',true,false);
       }else{
         const waiting=state.state==='waiting';
         show(waiting?'摘要采集暂缓，下次重试 '+new Date(state.retry_at*1000).toLocaleTimeString():'正在等待后台采集摘要…');
         timer=setTimeout(()=>void check(),waiting?15000:4000);
       }
     }catch(e){if(!controller.signal.aborted)show(errorMessage(e),true,false);}
   }
   show('正在读取本机摘要…');void check(true);
   return()=>{controller.abort();clearTimeout(timer);};
 },[identity,enabled,visible,sequence,readExisting]);
 return {...(status.identity===identity?status:{message:'',failed:false,busy:false}),refresh:(checkedAt?:string)=>{
   manual.current={identity,checkedAt};setSequence(n=>n+1);
 }};
}
