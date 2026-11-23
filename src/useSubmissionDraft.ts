import {createContext,useContext,useCallback,useRef,useState} from 'react';
import type {Dispatch,SetStateAction} from 'react';
import {submissionDrafts as drafts,settingsDrafts,RunPreferences} from './submissionDrafts';

export const RunPreferenceContext=createContext<RunPreferences|null>(null);

export function useSubmissionDraft<T>(key:string|null,initial:T,cache=drafts):[T,Dispatch<SetStateAction<T>>]{
 const preferences=useContext(RunPreferenceContext);
 const read=()=>key===null?initial:cache.get(key,cache===drafts&&preferences?preferences.get(key,initial):initial);
 const previous=useRef({key,preferences});
 const [value,setValue]=useState<T>(read);
 if(previous.current.key!==key||previous.current.preferences!==preferences){
  previous.current={key,preferences};
  setValue(read());
 }
 const update=useCallback<Dispatch<SetStateAction<T>>>(change=>setValue(old=>{
  const next=typeof change==='function'?(change as (value:T)=>T)(old):change;
  if(key!==null){
   cache.set(key,next);
   if(cache===drafts&&preferences){
    if(key.endsWith(':config')&&next&&typeof next==='object'&&'alias' in next){
     const prefix=key.slice(0,-6);
     preferences.set(prefix+'alias',next.alias);
     if('program' in next)preferences.set(prefix+'program',next.program);
     if('runtime_source' in next)preferences.set(prefix+'mode',next.runtime_source);
    }else preferences.set(key,next);
   }
  }
  return next;
 }),[key,cache,preferences]);
 return [value,update];
}
export function useSettingsDraft<T>(key:string,initial:T){return useSubmissionDraft(key,initial,settingsDrafts);}
