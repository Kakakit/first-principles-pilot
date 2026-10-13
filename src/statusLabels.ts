const labels:Record<string,string>={
  FROZEN:'已冻结',PENDING:'待派发',ACTIVE:'执行中',PAUSED:'已暂停',BLOCKED:'待核查',
  UNKNOWN:'状态待核查',COMPLETED:'已完成',FAILED:'失败',CANCELLED:'已取消',PREPARED:'已准备',
  DISPATCHING:'提交中',RUNNING:'运行中',EXITING:'收尾中',
  SUBMITTED:'已提交',QUEUED:'排队中',HELD:'调度暂停',COMPLETE:'批次完成',
  INSUFFICIENT:'证据不足',
  NOT_PERFORMED:'未审核',NOT_REVIEWED:'未审核',PASS:'已通过',REVIEWED:'已审核',
};
export function statusLabel(value?:string|null):string {return value ? labels[value]||value : '未记录';}
