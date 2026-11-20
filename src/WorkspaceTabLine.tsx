import {useLayoutEffect,useRef} from 'react';

export default function WorkspaceTabLine({active}:{active:string}){
 const line=useRef<HTMLSpanElement>(null);
 useLayoutEffect(()=>{
  const element=line.current;
  const list=element?.parentElement;
  const tab=list?.querySelector<HTMLElement>('[aria-selected=true]');
  if(!element||!list)return;
  element.hidden=!tab;
  if(!tab)return;
  const align=()=>{
   element.style.transform='translateX('+tab.offsetLeft+'px)';
   element.style.width=tab.offsetWidth+'px';
  };
  align();
  const observer=new ResizeObserver(align);
  observer.observe(list);
  list.querySelectorAll('button').forEach(button=>observer.observe(button));
  return ()=>observer.disconnect();
 },[active]);
 return <span ref={line} aria-hidden="true" className="workspace-tab-line"/>;
}
