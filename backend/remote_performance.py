"""Bounded local action receipts; excludes command text and scientific inputs."""
import json,threading,time,shutil
from .storage import ROOT
LOCK=threading.RLock()
PATH=ROOT/'remote-performance.json'


def read():
    with LOCK:
        try:return json.loads(PATH.read_text())
        except (OSError,ValueError):return {'actions':[]}


def record(label,seconds,events):
    entry={'action':label,'seconds':seconds,'at':time.time(),'remote_requests':sum(e.get('ssh_requests',0) for e in events),'steps':events}
    with LOCK:
        data=read();data['actions']=(data['actions']+[entry])[-200:]
        PATH.parent.mkdir(parents=True,exist_ok=True)
        temporary=PATH.with_suffix('.part');temporary.write_text(json.dumps(data));temporary.replace(PATH)
    return entry


def clear_cache():
    from . import remote_cache,interactive_plots,wannier_compare
    with interactive_plots.LOCK:
        if any(j['state'] in ('GENERATING','DOWNLOADING','PARSING') for j in interactive_plots.JOBS.values()):
            raise ValueError('绘图数据仍在准备中，请完成后清理缓存')
    with wannier_compare.JOB_LOCK:
        if any(j['state']=='RUNNING' for j in wannier_compare.JOBS.values()):raise ValueError('叠图仍在准备中，请完成后清理缓存')
    result=remote_cache.clear()
    for name in ('compact-plot-data','projection-cache','wannier-comparisons/data'):
        shutil.rmtree(ROOT/name,ignore_errors=True)
    interactive_plots.JOBS.clear()
    return {**result,'message':'已清除可重建来源与绘图缓存；历史报告、冻结输入和研究原文件保留。'}
