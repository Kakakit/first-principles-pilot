import {useState} from 'react';
import LifecycleDialog from './LifecycleDialog';

export default function RemoteDeleteAttempt({projectId,attemptId,onDeleted}:{projectId:string;attemptId:string;state:string;onDeleted:()=>void;onOpenAttempt?:(id:string)=>void}){
 const [open,setOpen]=useState(false);
 return <><button className="text-button cleanup-permanent" onClick={()=>setOpen(true)}>回收任务及管理远端文件</button>
  {open&&<LifecycleDialog target={{kind:'attempts',key:attemptId,project_id:projectId}} label="此运行记录" onClose={()=>setOpen(false)} onDone={onDeleted}/>}
 </>;
}
