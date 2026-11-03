export type ScanCase={id?:string;index:number;state:string;attempt_id?:string;parameters:{key:string;value:string}[]};
export const CASE_PAGE_SIZE=10;
export function scanBatchProgress(batch:{state:string;waiting_reason?:string;last_worker_error?:string;cases:{state:string}[]}){
 const finished=batch.cases.filter(c=>['COMPLETED','FAILED','CANCELLED'].includes(c.state)).length;
 const inflight=batch.cases.filter(c=>['SUBMITTED','QUEUED','RUNNING','DISPATCHING','HELD','EXITING'].includes(c.state)).length;
 const hint=batch.state==='ACTIVE'?(batch.last_worker_error||batch.waiting_reason||''):'';
 const phase=batch.state!=='ACTIVE'?statusLabel(batch.state):batch.last_worker_error?'派发待核查':inflight?`在途 ${inflight}`:hint?'等待派发':statusLabel(batch.state);
 return {label:`${finished}/${batch.cases.length} · ${phase}`,hint};
}
export function casePage(cases:ScanCase[],query:string,state:string,page:number){
 const q=query.trim().toLocaleLowerCase(),filtered=cases.filter(c=>(!state||c.state===state)&&(!q||[`case ${c.index+1}`,...c.parameters.map(p=>`${p.key}=${p.value}`)].join(' ').toLocaleLowerCase().includes(q)));
 const pages=Math.max(1,Math.ceil(filtered.length/CASE_PAGE_SIZE)),index=Math.max(0,Math.min(page,pages-1));
 return {items:filtered.slice(index*CASE_PAGE_SIZE,(index+1)*CASE_PAGE_SIZE),total:filtered.length,pages,page:index};
}
import {statusLabel} from './statusLabels';
