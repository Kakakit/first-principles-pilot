import {useCallback,useRef,useState} from 'react';
import type {Dispatch,SetStateAction} from 'react';
import {submissionDrafts as drafts} from './submissionDrafts';
// UI-only session drafts survive the settings page. Confirmations and remote
// authorization are deliberately never stored here.
export function useSubmissionDraft<T>(key:string,initial:T):[T,Dispatch<SetStateAction<T>>]{
 const previous=useRef(key);
 const [value,setValue]=useState<T>(()=>drafts.get(key,initial));
 if(previous.current!==key){previous.current=key;setValue(drafts.get(key,initial));}
 const update=useCallback<Dispatch<SetStateAction<T>>>(change=>setValue(old=>{const next=typeof change==='function'?(change as (value:T)=>T)(old):change;drafts.set(key,next);return next;}),[key]);
 return [value,update];
}
