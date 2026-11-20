import {useLayoutEffect,useRef} from 'react';

export default function RecordValue({children,recordId}:{children:string;recordId:string}){
 const element=useRef<HTMLElement>(null);
 const previous=useRef({recordId,value:children});
 useLayoutEffect(()=>{
  const changed=previous.current.recordId===recordId&&previous.current.value!==children;
  previous.current={recordId,value:children};
  if(changed&&!matchMedia('(prefers-reduced-motion: reduce)').matches){
   const effect=element.current?.animate([{backgroundColor:'var(--color-accent-soft)'},{backgroundColor:'transparent'}],{duration:500});
   return ()=>effect?.cancel();
  }
 },[recordId,children]);
 return <strong ref={element}>{children}</strong>;
}
