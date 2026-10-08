import {useCallback,useRef,useState} from 'react';
import type {Dispatch,SetStateAction} from 'react';
// UI-only session drafts survive the settings page. Confirmations and remote
// authorization are deliberately never stored here.
const drafts=new Map<string,unknown>();
export function useSubmissionDraft<T>(key:string,initial:T):[T,Dispatch<SetStateAction<T>>]{
 const previous=useRef(key);
 const [value,setValue]=useState<T>(()=>drafts.has(key)?drafts.get(key) as T:initial);
 if(previous.current!==key){previous.current=key;setValue(drafts.has(key)?drafts.get(key) as T:initial);}
 const update=useCallback<Dispatch<SetStateAction<T>>>(change=>setValue(old=>{const next=typeof change==='function'?(change as (value:T)=>T)(old):change;drafts.set(key,next);return next;}),[key]);
 return [value,update];
}
