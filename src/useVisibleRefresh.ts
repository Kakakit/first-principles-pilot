import {useEffect,useRef,useState} from 'react';
import {usePageVisible} from './usePageVisible';

/** Refresh metadata only while this panel is on screen, with one request at a time. */
export function useVisibleRefresh(enabled:boolean, identity:string, refresh:()=>Promise<unknown>, interval=60_000){
 const element=useRef<HTMLElement|null>(null),callback=useRef(refresh),running=useRef(false);
 const [onScreen,setOnScreen]=useState(false),pageVisible=usePageVisible();callback.current=refresh;
 useEffect(()=>{
  if(!element.current)return;
  const observer=new IntersectionObserver(entries=>setOnScreen(entries.some(entry=>entry.isIntersecting)));
  observer.observe(element.current);return()=>observer.disconnect();
 },[enabled,identity]);
 const visible=pageVisible&&onScreen;
 useEffect(()=>{
  if(!enabled||!visible)return;
  const update=async()=>{if(running.current)return;running.current=true;try{await callback.current();}finally{running.current=false;}};
  void update();const timer=window.setInterval(()=>void update(),interval);
  return()=>window.clearInterval(timer);
 },[enabled,identity,visible,interval]);
 return {element,visible};
}
