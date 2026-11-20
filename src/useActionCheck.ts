import {useEffect,useRef} from 'react';
import {registerActionCheck,currentIssueField} from './actionIssues';
import type {IssueCheck} from './actionIssues';
export function useActionCheck(key:string,check:IssueCheck){
 const current=useRef(check);current.current=check;
 useEffect(()=>registerActionCheck(key,()=>current.current()),[key]);
}
export function useIssueField(reveal:(field:string)=>void){
 const callback=useRef(reveal);callback.current=reveal;
 useEffect(()=>{
  const changed=(event:Event)=>callback.current((event as CustomEvent<string>).detail);
  window.addEventListener('fpilot:issue-field',changed);callback.current(currentIssueField());
  return ()=>window.removeEventListener('fpilot:issue-field',changed);
 },[]);
}
