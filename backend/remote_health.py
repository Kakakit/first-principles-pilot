"""Shared, durable host cooldown. Reconnection never replays a submission."""
import contextlib
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from filelock import FileLock
from . import storage

ROOT = None  # Tests may supply an isolated workspace.
INTERVAL = 900
MAX_RETRIES = 5
PROBE_COMMAND = 'printf FPILOT_CONNECTION_OK'


def root():
    return Path(ROOT) if ROOT is not None else storage.ROOT


def identity(alias, connection):
    return hashlib.sha256(json.dumps([alias, connection], sort_keys=True).encode()).hexdigest()


def _read():
    try:return json.loads((root()/'connection-health.json').read_text())
    except (OSError, ValueError):return {}


@contextlib.contextmanager
def edit():
    folder=root();folder.mkdir(parents=True,exist_ok=True)
    with FileLock(str(folder/'connection-health.lock'),timeout=5):
        values=_read()
        try:yield values
        finally:
            temporary=folder/('connection-health.'+uuid.uuid4().hex+'.tmp')
            try:
                temporary.write_text(json.dumps(values,ensure_ascii=False))
                os.replace(temporary,folder/'connection-health.json')
            finally:temporary.unlink(missing_ok=True)



class Deferred(ConnectionError):pass


def status(alias=None):
    rows=[r for r in _read().values() if not r.get('superseded')]
    if alias:rows=[r for r in rows if r['alias']==alias]
    # Commands are private recovery inputs, never part of status/metrics APIs.
    public=[{k:v for k,v in row.items() if k not in ('probe_command','claim','claim_until')} for row in rows]
    return sorted(public,key=lambda r:r.get('updated_at',0),reverse=True)


def claim(alias, connection, retry=False, timeout=90):
    key=identity(alias,connection);now=time.time();token=uuid.uuid4().hex
    with edit() as values:
        for other_key,other in values.items():
            if other_key!=key and other['alias']==alias:other['superseded']=True
        row=values.setdefault(key,dict(alias=alias,state='READY',retry_count=0,next_retry_at=None,counters={}))
        row.pop('superseded',None)
        recovering=row['state']!='READY'
        if recovering or retry == 'manual':
            if not retry:
                raise Deferred('主机自动连接已暂停' if row['state']=='PAUSED' else '主机连接冷却中；每 15 分钟重试，最多 5 次。可手动检测并恢复连接')
            if row.get('claim_until',0)>now:raise Deferred('本主机已有恢复检测在进行')
            if retry!='manual' and row['retry_count']>=MAX_RETRIES:
                row.update(state='PAUSED',next_retry_at=None)
                raise Deferred('已达 5 次重试上限，请手动检测并恢复连接')
            if retry!='manual' and (row['state']=='PAUSED' or (row.get('next_retry_at') or 0)>now):
                raise Deferred('尚未到下一次自动恢复时间')
            row.update(claim=token,claim_until=now+timeout+50)
            if retry!='manual':row.update(retry_count=row['retry_count']+1,next_retry_at=now+INTERVAL)
        counters=row['counters'];counters['requests']=counters.get('requests',0)+1
        if recovering and retry!='manual':counters['reconnect_attempts']=counters.get('reconnect_attempts',0)+1
        row['updated_at']=now
    return key,token,recovering,retry=='manual'


def finish(ticket, error=None, probe_command=None, counters=None):
    key,token,recovering,manual=ticket;now=time.time()
    with edit() as values:
        row=values[key]
        for name,count in (counters or {}).items():row['counters'][name]=row['counters'].get(name,0)+count
        owns=row.get('claim')==token
        if owns and (recovering or manual):
            row['last_recovery_at']=now;row['last_recovery_error']=str(error)[-2000:] if error else ''
        if error:
            row['counters']['failures']=row['counters'].get('failures',0)+1
            if row['state']=='READY':row.update(state='COOLDOWN',retry_count=0,next_retry_at=now+INTERVAL)
            if manual and owns:
                row.update(state='PAUSED',next_retry_at=None)
            elif owns:
                row['state']='PAUSED' if row['retry_count']>=MAX_RETRIES else 'COOLDOWN'
                row['next_retry_at']=None if row['state']=='PAUSED' else now+INTERVAL
            row['last_error']=str(error)[-2000:];row['last_failure_at']=now
            # Old records may contain an expensive failed business command. Never replay it.
            row.pop('probe_command',None)
        elif owns or row['state']=='READY':
            row.update(state='READY',retry_count=0,next_retry_at=None,last_success_at=now,last_error='')
            row.pop('probe_command',None)
        if owns:row.pop('claim',None);row.pop('claim_until',None)
        row['updated_at']=now


def due():
    now=time.time()
    with edit() as rows:
        for row in rows.values():
            if not row.get('superseded') and row['state']=='COOLDOWN' and row['retry_count']>=MAX_RETRIES and row.get('claim_until',0)<=now:
                row.update(state='PAUSED',next_retry_at=None)
    return [r['alias'] for r in _read().values() if not r.get('superseded') and r['state']=='COOLDOWN'
            and (r.get('next_retry_at') or 0)<=now and r.get('claim_until',0)<=now]


def probe_command(alias, connection):
    return PROBE_COMMAND
