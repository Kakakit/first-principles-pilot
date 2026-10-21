"""Small shared executor and per-host analysis slot; monitoring stays independent."""
import contextlib
import hashlib
import json
import os
import threading
import time
import math
from concurrent.futures import ThreadPoolExecutor
from filelock import FileLock, Timeout
from . import remote_health

executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='remote-analysis')
changed=threading.Condition()
closing=False
held=threading.local()


def environment(cores=None):
    return 'export '+' '.join(name+'='+value for name,value in thread_limits(cores).items())+'\n'


def thread_limits(cores=None):
    return dict.fromkeys(('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'),str(max(1,int(cores or 1))))


def subprocess_environment(cores=None):
    return {**os.environ,**thread_limits(cores),'PYTHONDONTWRITEBYTECODE':'1'}


def interpreter(alias):
    """Use the explicitly configured analysis Python; SSH control needs only stdlib."""
    from .extension_remote import runtimes
    from . import remote_transport
    configured=runtimes().get(alias,{}).get('python')
    if configured:
        if not isinstance(configured,str) or not configured.startswith('/') or any(c in configured for c in '\x00\r\n'):
            raise ValueError('服务器默认分析 Python 需为绝对路径')
        return configured
    return remote_transport._interpreter(alias)


def start():
    global closing
    closing=False


def stop():
    global closing
    closing=True;notify()


class Busy(ValueError):pass


def notify():
    with changed:changed.notify_all()


def remote_busy(alias, owner=None):
    from . import recycle
    for path in (remote_health.root()/'analyses-v2').glob('*/analysis.json'):
        try:row=json.loads(path.read_text())
        except (OSError,ValueError):continue
        if (row.get('id')!=owner and row.get('location')=='ssh' and
                row.get('runtime',{}).get('alias')==alias and
                row.get('state') in ('DISPATCHING','RUNNING','SUBMITTED','UNKNOWN') and
                not recycle.inactive('analyses',row['id'],row)):
            return True
    return False


@contextlib.contextmanager
def slot(alias, owner=None, wait=True, wait_timeout=300):
    aliases=getattr(held,'aliases',set())
    if alias in aliases:
        yield;return
    if not isinstance(wait_timeout,(int,float)) or not math.isfinite(wait_timeout) or wait_timeout<0:
        raise ValueError('分析通道等待时间须为有限非负秒数')
    deadline=time.monotonic()+wait_timeout
    folder=remote_health.root()/'analysis-slots';folder.mkdir(parents=True,exist_ok=True)
    lock=FileLock(str(folder/(hashlib.sha256(alias.encode()).hexdigest()+'.lock')))
    while True:
        with changed:
            if closing:raise Busy('服务正在关闭；分析未发送')
            acquired=False
            try:lock.acquire(timeout=0);acquired=True
            except Timeout:pass
            if acquired and not remote_busy(alias,owner):break
            if acquired:lock.release()
            if not wait:raise Busy('服务器正在执行其他分析；本次未发送，请稍后重试')
            states=remote_health.status(alias)
            if states and states[0]['state']!='READY':raise Busy('服务器连接已暂停；分析未发送，请恢复后重新生成')
            remaining=deadline-time.monotonic()
            if remaining<=0:raise Busy('等待服务器分析通道超时；本次未发送，已有分析继续执行')
            # Same-process completions wake immediately; cross-process checks are bounded too.
            changed.wait(timeout=min(remaining,30))
    held.aliases=aliases|{alias}
    try:yield
    finally:held.aliases=aliases;lock.release();notify()
