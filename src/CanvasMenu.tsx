import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {catalog,type Kind,type Step} from './model';
import {VASP_KINDS} from './stagePolicy';
import {attemptLabel} from './humanLabels';
import {requestJson,errorMessage} from './apiClient';
import type {ResultAttempt} from './resultWorkspace';

export type CanvasTarget={x:number;y:number;position:{x:number;y:number};id?:string};
type Props={target:CanvasTarget;projectId:string;node?:Step;count:number;kinds:Kind[];rows:ResultAttempt[];editable:boolean;
 close:()=>void;add:(kind:Kind)=>void;copy:(kind?:Kind)=>void;remove:()=>void;compare:()=>void;dos:(row:ResultAttempt)=>void};
export default function CanvasMenu({target,projectId,node,count,kinds,rows,editable,close,add,copy,remove,compare,dos}:Props){
 const [page,setPage]=useState<'main'|'types'|'dos'>('main');
 const [sources,setSources]=useState<ResultAttempt[]>([]),[loading,setLoading]=useState(false),[error,setError]=useState('');
 const panel=useRef<HTMLDivElement>(null);
 useEffect(()=>{
   const outside=(e:PointerEvent)=>{if(!panel.current?.contains(e.target as Node))close();};
   document.addEventListener('pointerdown',outside,true);
   window.addEventListener('resize',close);
   return()=>{document.removeEventListener('pointerdown',outside,true);window.removeEventListener('resize',close);};
 },[close]);
 useLayoutEffect(()=>{
   const el=panel.current!;
   const place=()=>{el.style.left=Math.max(4,Math.min(target.x,innerWidth-el.offsetWidth-6))+'px';el.style.top=Math.max(4,Math.min(target.y,innerHeight-el.offsetHeight-6))+'px';};
   place();const observer=new ResizeObserver(place);observer.observe(el);
   el.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
   return()=>observer.disconnect();
 },[page,target]);
 const candidates=rows.filter(r=>r.node_id===node?.id&&r.state==='COMPLETED'&&r.path_schema==='fpilot.remote-attempt-path'&&!r.scan_batch_id);
 const ids=candidates.map(r=>r.id).join(',');
 useEffect(()=>{
   if(page!=='dos')return;
   const ctrl=new AbortController();setSources([]);setError('');setLoading(true);
   void (async()=>{for(const row of candidates){
     const summary=await requestJson('/api/results/'+encodeURIComponent(row.id)+'/summary?project_id='+encodeURIComponent(projectId),undefined,undefined,{signal:ctrl.signal});
     if(ctrl.signal.aborted)return;
     if(summary.dos_source_candidate)setSources(old=>[...old,row]);
   }})().catch(e=>{if(!ctrl.signal.aborted)setError(errorMessage(e));}).finally(()=>{if(!ctrl.signal.aborted)setLoading(false);});
   return()=>ctrl.abort();
 },[page,ids,projectId]);
 const act=(fn:()=>void)=>{close();fn();};
 return createPortal(<div className="canvas-action-menu" role="group" aria-label="画布操作" ref={panel} onKeyDown={e=>{
   if(e.key==='Escape'){e.preventDefault();close();}
   if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();const buttons=Array.from(panel.current!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));const i=buttons.indexOf(document.activeElement as HTMLButtonElement);buttons[(i+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();}
 }} onBlur={e=>{if(e.relatedTarget&&!e.currentTarget.contains(e.relatedTarget as Node))close();}}>
 {page!=='main'&&<button onClick={()=>setPage('main')}>‹ 返回</button>}
 {page==='main'?(node?(count>1?<button onClick={()=>act(compare)}>比较所选 {count} 个节点的结果</button>:<>
 <button disabled={!editable} onClick={()=>act(()=>copy())}>复制节点</button>
 {VASP_KINDS.some(k=>k===node.data.kind)&&<button disabled={!editable} onClick={()=>setPage('types')}>复制为其他类型 ›</button>}
 {node.data.kind==='scf'&&candidates.length>0&&<button disabled={!editable} onClick={()=>setPage('dos')}>基于 SCF 结果创建 DOS ›</button>}
 <button disabled={!editable} className="danger" onClick={()=>act(remove)}>删除节点</button></>):<button disabled={!editable} onClick={()=>setPage('types')}>在此添加节点 ›</button>):page==='types'?(node?VASP_KINDS.filter(k=>k!==node.data.kind):kinds).map(k=><button key={k} onClick={()=>act(()=>node?copy(k):add(k))}>{catalog[k].label}</button>):<>
 {sources.map(r=><button key={r.id} onClick={()=>act(()=>dos(r))}>{attemptLabel(r)} · {r.created_at?.slice(0,16).replace('T',' ')}</button>)}
 {loading&&<small>正在核对本机运行记录…</small>}{error&&<small role="alert">{error}</small>}{!sources.length&&!loading&&!error&&<small>尚无可用于 DOS 的已收敛 SCF 结果</small>}</>}
 </div>,document.body);
}
