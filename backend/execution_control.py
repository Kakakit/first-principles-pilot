"""Local execution limits, event journal and bounded failure reports. No SSH."""
import hashlib
import json
import os
import re
import tempfile
from contextlib import closing
from . import execution as ex

ACTIVE = ('COMMAND_RUNNING','DISPATCHING','SUBMITTED','QUEUED','RUNNING','HELD','EXITING','UNKNOWN')
DEFAULT = {'max_inflight_total':None, 'max_inflight_per_host':None, 'max_cores_per_host':None}


def settings():
    path = ex.ROOT / 'execution-settings.json'
    return validate_settings(json.loads(path.read_text())) if path.exists() else dict(DEFAULT)


def validate_settings(value):
    if not isinstance(value,dict) or set(value) != set(DEFAULT):
        raise ValueError('需要总在途上限、每主机在途上限和每主机核数上限')
    for key, item in value.items():
        if item is None: continue
        if type(item) is not int or item < 1 or item > (100000 if key == 'max_cores_per_host' else 1000):
            raise ValueError(key+' 必须为正整数；各项可为 null 表示不额外限制')
    return dict(value)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
            temp=f.name
            f.write((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode());f.flush();os.fsync(f.fileno())
        # Windows cannot rename a NamedTemporaryFile while it is still open.
        # Close first, then atomically replace; preserve the old record on error.
        os.replace(temp,path)
    finally:
        if temp and os.path.exists(temp):os.unlink(temp)


def save_settings(value):
    value=validate_settings(value)
    write_json(ex.ROOT/'execution-settings.json',value)
    return value


def journal(c, key, event, details=None):
    c.execute('INSERT INTO execution_events(attempt_id,at,event,details) VALUES(?,?,?,?)',
              (key,ex.now(),event,json.dumps(details or {},ensure_ascii=False)))


def enforce_limits(c, record):
    limits=settings()
    rows=c.execute('SELECT body FROM attempts WHERE state IN ('+','.join('?' for _ in ACTIVE)+')',ACTIVE).fetchall()
    active=[json.loads(row[0]) for row in rows]
    from .extension_runs import active_usage
    active += [r for r in active_usage() if r['id']!=record.get('id')]
    active=[r for r in active if not r.get('quota_released')]
    same=[r for r in active if r['config']['alias']==record['config']['alias']]
    if (limits['max_inflight_total'] is not None and len(active)>=limits['max_inflight_total']) or (limits['max_inflight_per_host'] is not None and len(same)>=limits['max_inflight_per_host']):
        raise ValueError('在途任务数已达上限；UNKNOWN 也占用额度，未提交')
    cap=limits['max_cores_per_host']
    if cap is not None:
        counts=[r.get('profile',{}).get('ppn',r.get('profile',{}).get('ntasks')) for r in same+[record]]
        if any(type(n) is not int or n<1 for n in counts) or sum(counts)>cap:
            raise ValueError('每主机在途核数预算不足或历史核数未知；未提交')


def events(key):
    ex.get(key)
    with closing(ex.db()) as c:
        rows=c.execute('SELECT at,event,details FROM execution_events WHERE attempt_id=? ORDER BY id DESC LIMIT 100',(key,)).fetchall()
    return [dict(at=at,event=event,details=json.loads(details)) for at,event,details in rows]


def redact(text):
    text=str(text or '')
    text=re.sub(r'(?m)^\s*Variable_List\s*=.*(?:\n\t.*)*', '[environment omitted]', text)
    text=re.sub(r'(?i)(password|passwd|token|secret|api[_-]?key|authorization)(\s*[:=]\s*)[^\s,;]+',r'\1\2[REDACTED]',text)
    text=re.sub(r'(?i)Bearer\s+\S+', 'Bearer [REDACTED]',text)
    return text[-8192:]


def snapshot(key):
    r=ex.get(key)
    fields=('id','state','job_id','created_at','updated_at','dispatch_started_at','bundle_id','script_sha256',
            'project_id','flow_id','node_id','remote_path','remote_path_absolute','remote_workspace_root','retry_of','evidence','cancellation')
    result={k:r[k] for k in fields if k in r}
    result.update(schema='fpilot.failure-snapshot.v1',exported_at=ex.now(),software_version='1.0.0',
        runtime={k:r['config'].get(k) for k in ('alias','mpi_executable','vasp_executable','environment_script')},
        input_hashes=r.get('input_audit',{}).get('input_hashes',{}),
        audit_status=r.get('input_audit',{}).get('status'),
        logs={kind:{k:redact(r.get(kind,{}).get(k,'')) for k in ('stdout','stderr')} for kind in ('dispatch_receipt','status_receipt')},
        error_codes={kind:r.get(kind,{}).get('exit_code') for kind in ('dispatch_receipt','status_receipt')},
        analysis=notes(key),
        events=events(key), includes_potcar=False, includes_large_artifacts=False)
    def clean(obj):
        if isinstance(obj,str):return redact(obj)
        if isinstance(obj,list):return [clean(item) for item in obj]
        if isinstance(obj,dict):return {key:clean(item) for key,item in obj.items()}
        return obj
    return clean(result)


def notes(key):
    ex.get(key)
    from . import problem_records
    archived=problem_records.raw(problem_records.identity('attempts',key)) if re.fullmatch(r'[a-f0-9]{32}',key) else None
    if archived is not None:return archived['notes']
    path=ex.ROOT/'failure-notes'/(key+'.json')
    return json.loads(path.read_text()) if path.exists() else {'user_note':'','suspected_cause':'','confirmed_cause':'','confirmation_evidence':''}


def save_notes(key,value):
    ex.get(key)
    from . import problem_records
    value=problem_records.validate_notes(value)
    if re.fullmatch(r'[a-f0-9]{32}',key):problem_records.capture('attempts',key,manual=True,notes=value)
    write_json(ex.ROOT/'failure-notes'/(key+'.json'),value)
    return value


def freeze_failure(key):
    # A fixed per-attempt receipt is immutable; re-reading a terminal task adds no copies.
    path=ex.ROOT/'failure-snapshots'/(key+'.json')
    if not path.exists():write_json(path,snapshot(key))
    from . import problem_records
    problem_records.try_capture('attempts',key)
    return path
