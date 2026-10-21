import{useEffect}from'react';
export function useDialogFocus(open:boolean,selector:string,onClose:()=>void){
 useEffect(()=>{if(!open)return;const previous=document.activeElement as HTMLElement|null,dialog=document.querySelector<HTMLElement>(selector);if(!dialog)return;
 const controls=()=>Array.from(dialog.querySelectorAll<HTMLElement>('button,input,select,textarea,summary,a[href],[tabindex="0"]')).filter(x=>!x.matches(':disabled')&&x.getClientRects().length>0);
 controls()[0]?.focus();
 const key=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();onClose();}if(event.key!=='Tab')return;const items=controls(),first=items[0],last=items[items.length-1];if(!items.length){event.preventDefault();return;}if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}};
 document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);previous?.focus();};
 },[open,selector]);
}
