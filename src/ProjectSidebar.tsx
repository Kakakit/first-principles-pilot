import {useEffect,useRef,useState} from 'react';
import type {CSSProperties,ReactNode} from 'react';

const storageKey='fpilot.project-sidebar-width.v1';
const minimumWidth=192,maximumWidth=512,defaultWidth=256;

export default function ProjectSidebar({children,open,inert}:{children:ReactNode;open:boolean;inert:boolean}){
 const [width,setWidth]=useState(()=>{
  try{
   const saved=Number(localStorage.getItem(storageKey));
   return Number.isFinite(saved)&&saved>0?Math.max(minimumWidth,Math.min(maximumWidth,saved)):defaultWidth;
  }catch{return defaultWidth;}
 });
 const drag=useRef<{pointerId:number;x:number;width:number}|null>(null);
 useEffect(()=>{try{localStorage.setItem(storageKey,String(width));}catch{/* Resizing also works when storage is unavailable. */}},[width]);
 const resize=(value:number)=>setWidth(Math.max(minimumWidth,Math.min(maximumWidth,value)));
 return <>
  <aside id="project-sidebar" aria-label="项目导航" inert={inert} className={`sidebar ${open?'mobile-open':''}`} style={{'--project-sidebar-width':`${width}px`} as CSSProperties}>{children}</aside>
  <div className="project-sidebar-resizer" role="separator" aria-label="调整项目边栏宽度" aria-controls="project-sidebar"
   aria-orientation="vertical" aria-valuemin={minimumWidth} aria-valuemax={maximumWidth} aria-valuenow={width}
   tabIndex={0} title="拖动调整项目边栏宽度，双击恢复默认"
   onPointerDown={event=>{
    if(event.button!==0)return;
    event.preventDefault();drag.current={pointerId:event.pointerId,x:event.clientX,width};
    event.currentTarget.setPointerCapture(event.pointerId);
   }}
   onPointerMove={event=>{if(drag.current?.pointerId===event.pointerId)resize(drag.current.width+event.clientX-drag.current.x);}}
   onPointerUp={event=>{drag.current=null;if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);}}
   onPointerCancel={()=>{drag.current=null;}} onLostPointerCapture={()=>{drag.current=null;}}
   onDoubleClick={()=>resize(defaultWidth)}
   onKeyDown={event=>{
    const next:Record<string,number>={ArrowLeft:width-16,ArrowRight:width+16,Home:defaultWidth,End:maximumWidth};
    if(next[event.key]===undefined)return;
    event.preventDefault();resize(next[event.key]);
   }}><span/></div>
 </>;
}
