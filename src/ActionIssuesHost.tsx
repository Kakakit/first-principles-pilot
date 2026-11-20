import {useEffect,useRef,useState} from 'react';
import {getActionCheck,issueFromError,issueRoute,revealIssueField,readRuntimeIssueField} from './actionIssues';
import type {ActionIssue} from './actionIssues';
import type {TaskDestination,TaskNavigation} from './taskNavigation';
import './action-issues.css';
type Problem={issues:ActionIssue[];checkKey?:string;origin:TaskNavigation};
export default function ActionIssuesHost({navigation,navigate}:{navigation:TaskNavigation;navigate:(target:TaskDestination)=>void}){
 const [problem,setProblem]=useState<Problem|null>(null),[returnTo,setReturnTo]=useState<Problem|null>(null),[checking,setChecking]=useState(false),[notice,setNotice]=useState('');
 const current=useRef(navigation);current.current=navigation;
 const dialog=useRef<HTMLDialogElement>(null),returning=useRef<Problem|null>(null);
 useEffect(()=>{const received=(event:Event)=>{const detail=(event as CustomEvent<{issues:ActionIssue[];checkKey?:string}>).detail;returning.current=null;setChecking(false);setReturnTo(null);setNotice('');setProblem({...detail,origin:{...current.current,focus:undefined}});};window.addEventListener('fpilot:action-issues',received);return()=>window.removeEventListener('fpilot:action-issues',received);},[]);
 useEffect(()=>{if(problem)dialog.current?.showModal();else dialog.current?.close();},[problem]);
 useEffect(()=>{
  const name=navigation.focus?.startsWith('issue:')?navigation.focus.slice(6):'';
  revealIssueField(name);
  if(!name)return;
  let finished=false,timer:ReturnType<typeof setTimeout>;
  const focus=()=>{
   if(finished)return;
   let element=[...document.querySelectorAll<HTMLElement>('[data-action-field]')].find(item=>item.dataset.actionField===name);
   const runtime=readRuntimeIssueField(name);
   if(!element&&runtime)element=[...document.querySelectorAll<HTMLElement>('[data-action-field]')].find(item=>item.dataset.actionField==='extension.runtime.'+runtime.id);
   if(!element&&name.startsWith('parameter.'))element=document.querySelector<HTMLElement>('[aria-label="'+name.slice(10).replace(/[^A-Z0-9_]/g,'')+'"]')||undefined;
   if(!element&&name.startsWith('extension.options.'))element=document.getElementById('root_'+name.slice(18).replace(/\[(\d+)\]/g,'.$1').split('.').join('_'))||undefined;
   if(!element&&name.startsWith('extension.options'))element=document.querySelector<HTMLElement>('[data-action-field="extension.options"]')||undefined;
   if(!element&&name==='extension.sources')element=document.querySelector<HTMLElement>('[data-action-field="extension.source-refresh"]')||undefined;
   if(!element)return;
   if(element instanceof HTMLDetailsElement)element.open=true;
   for(let parent=element.parentElement;parent;parent=parent.parentElement)if(parent instanceof HTMLDetailsElement)parent.open=true;
   const input=element.matches('input,select,textarea,button')?element:element.querySelector<HTMLElement>('input:not([disabled]),select:not([disabled]),textarea:not([disabled]),button:not([disabled])');
   if(input?.matches(':disabled'))return;
   if(!input&&!element.hasAttribute('tabindex'))element.tabIndex=-1;
   element.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});(input||element).focus({preventScroll:true});
   element.classList.add('action-field-highlight');timer=setTimeout(()=>element?.classList.remove('action-field-highlight'),3000);finished=true;
  };
  const observer=new MutationObserver(focus);observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['disabled','open']});
  const frame=requestAnimationFrame(focus),expiry=setTimeout(()=>observer.disconnect(),15_000);
  return()=>{finished=true;observer.disconnect();cancelAnimationFrame(frame);clearTimeout(expiry);clearTimeout(timer);document.querySelectorAll('.action-field-highlight').forEach(element=>element.classList.remove('action-field-highlight'));};
 },[navigation.focus,navigation.page,navigation.nodeId,navigation.projectId]);
 useEffect(()=>{
  const pending=returning.current;
  if(!checking||!pending)return;
  const sameOrigin=(Object.keys(pending.origin) as (keyof TaskNavigation)[])
   .every(key=>navigation[key]===pending.origin[key]);
  if(!sameOrigin){returning.current=null;setChecking(false);return;}
  let stopped=false,frame=0,tries=0;
  const check=async()=>{
   if(stopped)return;
   const action=pending.checkKey&&getActionCheck(pending.checkKey);
   if(!action&&tries++<120){frame=requestAnimationFrame(()=>void check());return;}
   try{
    if(!action){setNotice('已返回原位置，请重新点击原来的检查或下一步。');return;}
    const issues=(await action()).filter(issue=>issue.level!=='warning');
    if(stopped||returning.current!==pending)return;
    if(issues.length){setProblem({...pending,issues});return;}
    setReturnTo(null);
    setNotice('当前检查已通过，可以继续原操作。提交计算仍需点击确认。');
    document.querySelector<HTMLElement>('[data-action-check="'+CSS.escape(pending.checkKey||'')+'"]')?.focus();
   }catch(error){
    if(!stopped&&returning.current===pending)setProblem({...pending,issues:issueFromError(error)});
   }finally{
    if(!stopped&&returning.current===pending){returning.current=null;setChecking(false);}
   }
  };
  frame=requestAnimationFrame(()=>void check());return()=>{stopped=true;cancelAnimationFrame(frame);};
 },[navigation.page,navigation.projectId,navigation.nodeId,navigation.focus,navigation.subpage,navigation.section,navigation.alias,navigation.program,navigation.attemptId,navigation.bundleId,navigation.runScope,checking]);
 const back=()=>{if(!returnTo)return;if(returnTo.issues.some(issue=>issue.code==='request.failed')){navigate({...returnTo.origin,focus:undefined});setReturnTo(null);setNotice('已返回原位置，请再次点击原操作重试；尚未确认原错误已解决。');return;}returning.current=returnTo;setChecking(true);navigate({...returnTo.origin,focus:undefined});};
 return <>{(returnTo||notice)&&<aside className="action-return" aria-label="返回原操作"><span role="status">{notice||'修改后可返回原操作，重新检查当前设置。'}</span>{returnTo&&<button className="button" disabled={checking} onClick={back}>{checking?'正在重新检查…':'返回并重新检查'}</button>}<button className="text-button" onClick={()=>{returning.current=null;setReturnTo(null);setNotice('');setChecking(false);}}>关闭</button></aside>}<dialog ref={dialog} className="action-issues-dialog" aria-label="下一步所需设置" onCancel={event=>{event.preventDefault();setProblem(null);}}><h2>还需处理 {problem?.issues.length||0} 项</h2><ul>{problem?.issues.map((issue,index)=><li key={issue.code+index}><strong>{issue.message}</strong>{issue.target?.field&&<small>{issue.target.page==='settings'?'全局设置':issue.target.page==='run'?'运行与监控':issue.target.page==='results'?'结果与后处理':issue.target.page==='flow'?'项目与流程':'计算配置'}</small>}<button className={index===0?'button primary':'text-button'} onClick={()=>{if(!problem)return;setReturnTo(problem);setProblem(null);if(issue.action==='retry'&&!issue.target){setNotice('已保留当前内容，请在原位置重试。');return;}navigate(issueRoute(issue,problem.origin));}}>{issue.label||(issue.action==='inspect'?'查看上游任务':issue.action==='retry'?'重试连接':'去修改')}</button></li>)}</ul><div className="action-row"><button className="button" onClick={()=>setProblem(null)}>暂不处理</button></div></dialog></>;
}
