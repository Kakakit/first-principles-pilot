import {useEffect,useMemo} from 'react';

export class LatestRequests{
 private requests=new Map<string,AbortController>();
 start(key:string){
  this.cancel(key);const controller=new AbortController();this.requests.set(key,controller);
  return {signal:controller.signal,current:()=>!controller.signal.aborted&&this.requests.get(key)===controller};
 }
 cancel(key?:string){
  if(key!==undefined){this.requests.get(key)?.abort();this.requests.delete(key);return;}
  for(const controller of this.requests.values())controller.abort();
  this.requests.clear();
 }
}
/** Each read channel replaces its old receipt; leaving the page aborts all reads. */
export function useRequestScope(identity:string){
 const scope=useMemo(()=>new LatestRequests(),[identity]);
 useEffect(()=>()=>scope.cancel(),[scope]);return scope;
}
