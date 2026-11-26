import {useLayoutEffect,useRef,useState} from 'react';

const preference='fpilot.input-preview.';
function savedRatio(stacked:boolean){
 try{
  const value=Number(localStorage.getItem(preference+(stacked?'height':'width')));
  if(value>0&&value<1)return value;
 }catch{/* Local storage is optional. */}
 return stacked ? 0.32 : 0.4;
}

export default function InputPreviewDivider({controls}:{controls:string}){
 const handle=useRef<HTMLDivElement>(null);
 const drag=useRef<{pointer:number;offset:number;stacked:boolean}|null>(null);
 const stopDrag=useRef<()=>void>(()=>{});
 const [metrics,setMetrics]=useState({size:0,min:0,max:0,value:0,stacked:false});
 function resize(ratio?:number){
  const split=handle.current?.parentElement;
  if(!split)return;
  const stacked=getComputedStyle(split).flexDirection==='column';
  const size=stacked?split.clientHeight:split.clientWidth;
  if(!size)return;
  const min=Math.min(stacked?100:240,Math.round(size*.4));
  const max=Math.max(min,size-Math.min(stacked?120:260,Math.round(size*.4))-10);
  const value=Math.max(min,Math.min(max,Math.round(size*(ratio??savedRatio(stacked)))));
  split.style.setProperty('--input-preview-size',value+'px');
  if(ratio!==undefined){
   try{localStorage.setItem(preference+(stacked?'height':'width'),String(value/size));}catch{/* Keep current layout. */}
  }
  setMetrics({size,min,max,value,stacked});
 }
 useLayoutEffect(()=>{
  const split=handle.current!.parentElement!;
  resize();
  const observer=new ResizeObserver(()=>resize());
  observer.observe(split);
  return ()=>{observer.disconnect();stopDrag.current();};
 },[]);
 const {stacked,size,min,max,value}=metrics;
 return <div ref={handle} className="input-preview-divider" role="separator" tabIndex={0}
  aria-label={stacked?'调整输入文件预览高度':'调整输入文件预览宽度'} aria-controls={controls}
  aria-orientation={stacked?'horizontal':'vertical'} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value}
  title="拖动调整预览大小，双击恢复默认" onDoubleClick={()=>resize(stacked ? 0.32 : 0.4)}
  onPointerDown={e=>{
   if(e.button!==0||drag.current)return;
   e.preventDefault();
   const bounds=e.currentTarget.parentElement!.getBoundingClientRect();
   const pointerSize=stacked?e.clientY-bounds.top:bounds.right-e.clientX;
   drag.current={pointer:e.pointerId,offset:value-pointerSize,stacked};
   const move=(event:PointerEvent)=>{
    const active=drag.current;
    if(!active||active.pointer!==event.pointerId)return;
    const split=handle.current?.parentElement;
    if(!split)return;
    const rect=split.getBoundingClientRect();
    const extent=active.stacked?rect.height:rect.width;
    if(extent)resize(((active.stacked?event.clientY-rect.top:rect.right-event.clientX)+active.offset)/extent);
   };
   const stop=()=>{
    window.removeEventListener('pointermove',move,true);
    window.removeEventListener('pointerup',stop,true);
    window.removeEventListener('pointercancel',stop,true);
    window.removeEventListener('blur',stop);
    drag.current=null;
   };
   stopDrag.current=stop;
   window.addEventListener('pointermove',move,true);
   window.addEventListener('pointerup',stop,true);
   window.addEventListener('pointercancel',stop,true);
   window.addEventListener('blur',stop);
  }}
  onKeyDown={e=>{
   const steps:Record<string,number>={Home:min,End:max,...(stacked?{ArrowUp:value-24,ArrowDown:value+24}:{ArrowLeft:value+24,ArrowRight:value-24})};
   if(size&&e.key in steps){e.preventDefault();resize(steps[e.key]/size);}
  }}><span/></div>;
}
