import {statusLabel} from './statusLabels';

export type RecoveryReceipt = {
  state:string; dispatch_state?:string; job_id?:string; error?:string; receipt_note?:string;
  waiting_reason?:string; progress?:{phase:string;at:string};
};

export function recoveryReceiptLabel(item:RecoveryReceipt):string {
  if (item.dispatch_state==='SUPERSEDED') return '已由后续操作接管，本轮未提交';
  if (item.dispatch_state==='FAILED') return '本项提交处理失败，请查看具体原因';
  if (item.dispatch_state==='UNCERTAIN'||item.state==='UNKNOWN'&&item.dispatch_state==='SENT') return '提交回执不明（UNKNOWN），未取得确认作业号；请核查原作业';
  if (item.job_id) return `作业号 ${item.job_id} · ${statusLabel(item.state)}`;
  if (item.dispatch_state==='SENT') return `本项状态：${statusLabel(item.state)}`;
  if (item.waiting_reason) return item.waiting_reason;
  const phases:Record<string,string>={PROBING:'正在核查所选服务器与资源',PREPARING:'正在准备独立 attempt 与输入',SUBMITTING:'正在传输输入并等待调度器回执'};
  return phases[item.progress?.phase||''] || '授权已保存，等待处理此项';
}

export function recoveryMessage(operation:{state:string;items:RecoveryReceipt[]}):string {
  if (operation.state==='PAUSED') return '本次范围已暂停，等待明确继续。';
  if (operation.state!=='FINISHED') return '授权已保存，正在后台处理本次范围；可关闭面板，重新打开读取进度与回执。';
  const unknown=operation.items.filter(i=>i.dispatch_state==='UNCERTAIN'||i.state==='UNKNOWN'&&i.dispatch_state==='SENT').length;
  const failed=operation.items.filter(i=>i.dispatch_state==='FAILED').length;
  if (unknown||failed) return `本轮处理结束：${unknown} 项提交回执不明，${failed} 项处理失败。请查看逐项原因；未自动重投。`;
  return '本轮处理结束，请查看逐项作业号与状态。';
}
