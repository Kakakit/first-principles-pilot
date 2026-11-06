import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {locationFromHash,locationHash,maxVisits,nextLocation,visitFromState,visitTrail,type Visit,type VisitView} from './navigationHistory';
import type {TaskDestination,TaskNavigation} from './taskNavigation';

const storageKey='fpilot-page-visits-v1';
const identity=()=>globalThis.crypto?.randomUUID?.()||Date.now().toString(36)+Math.random().toString(36).slice(2);
function storage(){
 try{return JSON.parse(sessionStorage.getItem(storageKey)||'{}')||{};}catch{return {};}
}
function firstVisit(initial:TaskNavigation):Visit{
 const route=locationFromHash(window.location.hash);
 const state=visitFromState(window.history.state);
 return state&&route&&locationHash(state.route)===locationHash(route)?state:
  {fpilot:1,session:identity(),id:identity(),position:0,route:route||initial};
}

export function useWorkbenchNavigation(initial:TaskNavigation){
 const [entry,setEntry]=useState(()=>firstVisit(initial));
 const [explicit]=useState(()=>Boolean(locationFromHash(window.location.hash)));
 const [cached]=useState(()=>{
  const saved=storage();
  const visits=(values:unknown):Visit[]=>Array.isArray(values)?values.map(visitFromState)
   .filter((visit):visit is Visit=>visit?.session===entry.session):[];
  return {trail:visits(saved.trail),recent:visits(saved.recent),
   views:new Map<string,VisitView>(saved.session===entry.session&&Array.isArray(saved.views)?saved.views.filter(Array.isArray):[])};
 });
 const current=useRef(entry);
 const trail=useRef(cached.trail);
 const recent=useRef(cached.recent);
 const views=useRef(cached.views);
 const [traversal,setTraversal]=useState(0);
 function persist(){
  try{sessionStorage.setItem(storageKey,JSON.stringify({session:current.current.session,trail:trail.current,recent:recent.current,views:[...views.current]}));}catch{/* Storage can be disabled. */}
 }
 function apply(next:Visit,action:'push'|'replace'|'pop'){
  trail.current=visitTrail(trail.current,next,action);
  recent.current=[next,...recent.current.filter(item=>item.id!==next.id&&locationHash(item.route)!==locationHash(next.route))].slice(0,11);
  current.current=next;
  setEntry(next);
  persist();
 }
 useLayoutEffect(()=>{
  history.replaceState(current.current,'',locationHash(current.current.route));
  const restoration=history.scrollRestoration;
  history.scrollRestoration='manual';
  apply(current.current,'replace');
  const pop=(event:PopStateEvent)=>{
   const visit=visitFromState(event.state);
   const route=locationFromHash(location.hash);
   if(visit&&route&&locationHash(visit.route)===locationHash(route))apply(visit,'pop');
   else {
    const next={...firstVisit(current.current.route),session:identity(),id:identity(),position:0,origin:undefined};
    history.replaceState(next,'',locationHash(next.route));
    apply(next,'replace');
   }
   setTraversal(value=>value+1);
  };
  window.addEventListener('popstate',pop);
  window.addEventListener('pagehide',persist);
  return()=>{window.removeEventListener('popstate',pop);window.removeEventListener('pagehide',persist);history.scrollRestoration=restoration;};
 },[]);

 function navigate(target:TaskDestination,replace=false){
  const old=current.current;
  const route=nextLocation(old.route,target);
  if(locationHash(old.route)===locationHash(route))return;
  const origin=route.page==='settings'?(old.route.page==='settings'?old.origin:old.id):undefined;
  const next={...old,route,origin,...(replace?{}:{id:identity(),position:old.position+1})};
  if(replace)history.replaceState(next,'',locationHash(route));
  else history.pushState(next,'',locationHash(route));
  apply(next,replace?'replace':'push');
 }
 function remember(view:VisitView,id=entry.id){
  const prior=views.current.get(id);
  views.current.delete(id);
  views.current.set(id,{...prior,...view,
   ...(view.filter!==undefined?{filter:view.filter.slice(0,256)}:{}),
   ...(view.details?{details:{...prior?.details,...view.details}}:{}),
   ...(view.nestedScroll?{nestedScroll:{...prior?.nestedScroll,...view.nestedScroll}}:{})});
  if(views.current.size>maxVisits)views.current.delete(views.current.keys().next().value!);
 }
 const at=(position:number)=>trail.current.find(item=>item.position===position);
 return {entry,explicit,traversal,navigate,remember,view:views.current.get(entry.id),
  isCurrent:(id:string)=>current.current.id===id,
  current:()=>current.current.route,
  back:at(entry.position-1),forward:at(entry.position+1),
  recent:recent.current.filter(item=>item.id!==entry.id).slice(0,10),
  goBack:()=>{if(at(current.current.position-1))history.back();},
  goForward:()=>{if(at(current.current.position+1))history.forward();},
  returnToOrigin:(fallback:TaskDestination)=>{
   const active=current.current;
   const origin=trail.current.find(item=>item.id===active.origin);
   if(origin&&origin.position<active.position)history.go(origin.position-active.position);
   else navigate(fallback);
  }};
}

export function useVisitScroll(root:React.RefObject<HTMLElement|null>,entryId:string,view:VisitView|undefined,remember:(value:VisitView,id:string)=>void){
 useEffect(()=>{
  const element=root.current;
  if(!element)return;
  const inner=()=>element.querySelector<HTMLElement>('[data-navigation-scroll]');
  const target=()=>view?.scrollContainer==='outer'?element:inner()||element;
  const desired=view?.scroll;
  let restoring=desired!==undefined||Boolean(view?.details||view?.nestedScroll);
  const restoredDetails=new WeakSet<HTMLDetailsElement>();
  const stop=()=>{restoring=false;observer.disconnect();resize.disconnect();clearTimeout(timer);};
  const apply=()=>{
   if(!restoring)return;
   const container=target();
   if(desired!==undefined){
    resize.observe(container);
    if(container.firstElementChild)resize.observe(container.firstElementChild);
    container.scrollTop=desired;
   }
   for(const details of element.querySelectorAll<HTMLDetailsElement>('details[data-visit-detail]')){
    const key=details.dataset.visitDetail!;
    if(!restoredDetails.has(details)&&view?.details?.[key]!==undefined){
     restoredDetails.add(details);
     details.open=view.details[key];
    }
   }
   for(const scroller of element.querySelectorAll<HTMLElement>('[data-visit-scroll]')){
    const offset=view?.nestedScroll?.[scroller.dataset.visitScroll!];
    if(offset!==undefined){
     resize.observe(scroller);
     if(scroller.firstElementChild)resize.observe(scroller.firstElementChild);
     scroller.scrollTop=offset;
    }
   }
  };
  const observer=new MutationObserver(apply);
  const resize=new ResizeObserver(apply);
  if(restoring)observer.observe(element,{childList:true,subtree:true});
  const timer=window.setTimeout(stop,2000);
  const record=(event:Event)=>{
   const scroller=event.target;
   if(restoring||!(scroller instanceof HTMLElement))return;
   if(scroller.dataset.visitScroll){
    remember({nestedScroll:{[scroller.dataset.visitScroll]:scroller.scrollTop}},entryId);
   }else if(scroller===element||scroller===inner()){
    remember({scroll:scroller.scrollTop,scrollContainer:scroller===element?'outer':'content'},entryId);
   }
  };
  const toggle=(event:Event)=>{
   const target=event.target;
   if(target instanceof HTMLDetailsElement&&target.dataset.visitDetail){
    remember({details:{[target.dataset.visitDetail]:target.open}},entryId);
   }
  };
  if(desired===undefined)target().scrollTop=0;
  apply();
  element.addEventListener('scroll',record,true);
  element.addEventListener('toggle',toggle,true);
  const manual=['wheel','touchstart','pointerdown','keydown'];
  for(const type of manual)element.addEventListener(type,stop,{passive:true});
  return()=>{
   stop();
   element.removeEventListener('scroll',record,true);
   element.removeEventListener('toggle',toggle,true);
   for(const type of manual)element.removeEventListener(type,stop);
  };
 },[entryId,root]);
}
