import {useCallback,useRef,useState} from 'react';
import type {Dispatch,SetStateAction} from 'react';
import {submissionDrafts as drafts,settingsDrafts} from './submissionDrafts';
// UI-only session drafts survive the settings page. Confirmations and remote
// authorization are deliberately never stored here.
export function useSubmissionDraft<T>(key:string|null,initial:T,cache=drafts):[T,Dispatch<SetStateAction<T>>]{
 const previous=useRef(key);
 const [value,setValue]=useState<T>(()=>key===null?initial:cache.get(key,initial));
 if(previous.current!==key){previous.current=key;setValue(key===null?initial:cache.get(key,initial));}
 const update=useCallback<Dispatch<SetStateAction<T>>>(change=>setValue(old=>{const next=typeof change==='function'?(change as (value:T)=>T)(old):change;if(key!==null)cache.set(key,next);return next;}),[key,cache]);
 return [value,update];
}
export function useSettingsDraft<T>(key:string,initial:T){return useSubmissionDraft(key,initial,settingsDrafts);}
