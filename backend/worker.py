"""One local worker: refresh attempts and dispatch only explicitly authorized runs."""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from . import execution as ex
from . import execution_control as control
from . import recycle
from .platform_runtime import exclusive_lock, process_alive


def state():
    path=ex.ROOT/'worker-state.json'
    try:
        record=json.loads(path.read_text())
        age=time.time()-record['heartbeat_epoch']
        record['heartbeat_age_seconds']=round(age,1)
        alive=process_alive(record.get('pid'))
        record['healthy']=bool(alive and record.get('running') and age < record.get('interval_seconds',300)+90)
        return record
    except (OSError,ValueError,KeyError):
        return {'running':False,'healthy':False,'note':'监控未启动；检查本机设置的自动推进开关，并重新启动工作台'}


def tick(refresh=None, notify=False):
    custom_refresh=refresh
    refresh=refresh or ex.refresh
    with closing(ex.db()) as c:
        rows=c.execute('SELECT body FROM attempts WHERE state IN ('+','.join('?' for _ in control.ACTIVE)+')',control.ACTIVE).fetchall()
    checked=0;errors=[]
    from . import remote_health,remote_transport
    due=set(remote_health.due())
    # Hosts advance independently; a slow/unreachable host cannot hold other hosts.
    groups={}
    for row in rows:
        value=json.loads(row[0]);groups.setdefault(value.get('config',{}).get('alias',''),[]).append(row)
    for alias in due:groups.setdefault(alias,[])
    def refresh_host(pair):
        alias,host_rows=pair
        checked=0;errors=[]
        if alias in due:remote_transport.recover(alias)
        from . import remote_monitor
        live_records=[json.loads(row[0]) for row in host_rows]
        scheduler=remote_monitor.batch([r for r in live_records if not recycle.inactive('attempts',r['id'],r)]) if custom_refresh is None else {}
        for row in host_rows:
            r=json.loads(row[0])
            if r.get('config',{}).get('scheduler')=='custom':continue
            if recycle.inactive('attempts',r['id'],r):continue
            if r['state']=='DISPATCHING':
                try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(r['dispatch_started_at'])).total_seconds()
                except (KeyError,ValueError):age=181
                # A source copy may take 900s. Include session/receipt allowance;
                # status reads must not classify an upload still in progress.
                timeout=r.get('dispatch_timeout_seconds',900 if any(item.get('mode')=='reflink-or-copy' for item in r.get('source_file_plan',{}).values()) else 180)
                if age<timeout+65:continue
            try:
                updated=refresh(r['id']) if custom_refresh is not None else ex.refresh(r['id'],poll_receipt=scheduler.get(r['id']),automatic=True)
                checked+=1
                if updated['state']=='FAILED':control.freeze_failure(r['id'])
                if notify and updated['state'] in ('FAILED','COMPLETED'):notify_terminal(r['id'])
            except Exception as exc:
                if recycle.inactive('attempts',r['id'],r):continue
                message=control.redact(str(exc))[:1000]
                errors.append({'attempt_id':r['id'],'message':message})
                with closing(ex.db()) as c:
                    control.journal(c,r['id'],'REFRESH_ERROR',{'message':message});c.commit()
        return checked,errors
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(4,len(groups)))) as pool:
        for count,failures in pool.map(refresh_host,groups.items()):
            checked+=count;errors.extend(failures)
    with closing(ex.db()) as c:
        failed=[row[0] for row in c.execute("SELECT id FROM attempts WHERE state='FAILED'")]
    for key in failed:
        if recycle.inactive('attempts',key,ex.get(key)):continue
        if not (ex.ROOT/'failure-snapshots'/(key+'.json')).exists():control.freeze_failure(key)
    from . import task_recovery
    capacity_context={}
    try:task_recovery.tick(capacity_context=capacity_context)
    except Exception as exc:errors.append({'batch':'task-recovery','message':control.redact(str(exc))[:1000]})
    from . import scans
    try:
        scans.tick(capacity_context=capacity_context)
    except Exception as exc:
        errors.append({'batch':'scan-dispatch','message':control.redact(str(exc))[:1000]})
    from . import chain_runs
    try:
        chain_runs.tick(capacity_context=capacity_context)
    except Exception as exc:
        errors.append({'batch':'chain-dispatch','message':control.redact(str(exc))[:1000]})
    from . import extension_runs
    try:extension_runs.tick()
    except Exception as exc:errors.append({'batch':'analysis','message':control.redact(str(exc))[:1000]})
    return {'checked':checked,'errors':errors}


def notify_terminal(key):
    record=ex.get(key)
    if record.get('notification') or sys.platform!='darwin':return
    message='{} {}：{}，科学结论未审核'.format(record['config']['alias'],record.get('job_id'),record['state'])
    result=subprocess.run(['osascript','-e','on run argv','-e','display notification (item 1 of argv) with title "计算工作台" sound name "Glass"','-e','end run',message],capture_output=True,text=True,timeout=10)
    record=ex.get(key);record['notification']={'returncode':result.returncode,'stderr':result.stderr[:500]};ex.save(record)


def interval_seconds(override=None):
    return control.validate_interval(override) if override is not None else control.settings()['worker_interval_seconds']


def wait_for_cycle(stop, heartbeat, override=None, parent=None):
    from . import remote_health
    started=time.monotonic()
    retry_times=[r['next_retry_at'] for r in remote_health.status() if r['state']=='COOLDOWN' and r.get('next_retry_at')]
    retry_deadline=started+max(1,min(retry_times)-time.time()) if retry_times else float('inf')
    while not stop.is_set():
        if parent and not process_alive(int(parent)):stop.set();break
        path=os.environ.get('FPILOT_STOP_FILE')
        if path and __import__('pathlib').Path(path).exists():stop.set();break
        interval=interval_seconds(override)
        if heartbeat['interval_seconds'] != interval:
            heartbeat['interval_seconds']=interval
            control.write_json(ex.ROOT/'worker-state.json',heartbeat)
        remaining=min(started+interval,retry_deadline)-time.monotonic()
        if remaining <= 0:break
        stop.wait(min(1,remaining))


def run(interval=None, once=False):
    # The platform file lock is released by the OS after a crash.
    ex.ROOT.mkdir(parents=True,exist_ok=True)
    with exclusive_lock(ex.ROOT/'worker.lock') as owned:
        if not owned:return 0
        stop=threading.Event()
        def quit_worker(*_):stop.set()
        signal.signal(signal.SIGTERM,quit_worker);signal.signal(signal.SIGINT,quit_worker)
        started=ex.now()
        heartbeat=dict(pid=os.getpid(),running=True,started_at=started,interval_seconds=interval_seconds(interval),interval_source='settings' if interval is None else 'command-line')
        try:
            while not stop.is_set():
                parent=os.environ.get('FPILOT_LAUNCHER_PID')
                if parent and not process_alive(int(parent)):break
                heartbeat.update(heartbeat_epoch=time.time(),interval_seconds=interval_seconds(interval))
                control.write_json(ex.ROOT/'worker-state.json',heartbeat)
                result=tick(notify=os.environ.get('FPILOT_NOTIFICATIONS','1')!='0')
                heartbeat.update(heartbeat_epoch=time.time(),last_cycle=result)
                control.write_json(ex.ROOT/'worker-state.json',heartbeat)
                if once:break
                wait_for_cycle(stop,heartbeat,interval,parent)
        finally:
            control.write_json(ex.ROOT/'worker-state.json',dict(heartbeat,running=False,heartbeat_epoch=time.time()))
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--interval',type=int,default=None,help='Override the saved worker period in seconds')
    args=parser.parse_args()
    if args.interval is not None:
        try:control.validate_interval(args.interval)
        except ValueError as error:parser.error(str(error))
    return run(args.interval,args.once)

if __name__=='__main__':raise SystemExit(main())
