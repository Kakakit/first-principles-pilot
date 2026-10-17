const active=new Set(['COMMAND_RUNNING','DISPATCHING','SUBMITTED','QUEUED','RUNNING','HELD','EXITING']);
export function taskSummary(items:{state?:string}[]):string {
 const count=(states:string[])=>items.filter(i=>states.includes(i.state||'')).length;
 const pending=count(['PENDING','PREPARED','RETRY_PENDING','RECOVERY_PENDING']);
 const inflight=items.filter(i=>active.has(i.state||'')).length;
 const done=count(['COMPLETED','DONE']);
 const failed=count(['FAILED','BLOCKED']);
 return `${failed?'有失败或待处理项 · ':''}待提交 ${pending} · 在途 ${inflight} · 已结束 ${done} · 失败/待处理 ${failed} · 待核查 ${count(['UNKNOWN'])} · 已取消 ${count(['CANCELLED'])}`;
}
