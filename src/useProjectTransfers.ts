import {useCallback,useSyncExternalStore} from 'react';
import {projectTransfers} from './projectTransfers';

export function useProjectTransfers(projectId:string){
 const subscribe=useCallback((listener:()=>void)=>projectTransfers.subscribe(projectId,listener),[projectId]);
 const getSnapshot=useCallback(()=>projectTransfers.getSnapshot(projectId),[projectId]);
 const snapshot=useSyncExternalStore(subscribe,getSnapshot,getSnapshot);
 const refresh=useCallback(()=>projectTransfers.refresh(projectId),[projectId]);
 return {...snapshot,refresh};
}
