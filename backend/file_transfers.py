"""Resumable, version-pinned collection. Scientific execution is never retried here."""
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from .storage import ROOT
from . import remote_transport, site_registration, attempt_operations, execution as ex
from .platform_runtime import exclusive_lock

ACTIVE = {'PENDING', 'RUNNING'}
_event = threading.Event()
_stop = threading.Event()
_thread = None


def _base():
    return ex.ROOT / 'file-transfers'


@contextmanager
def _db():
    _base().mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_base()/'index.sqlite3', timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        yield db


def _save(row):
    row['updated_at'] = time.time()
    with _db() as db:
        db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?)', (row['id'], json.dumps(row)))
    return row


def _get(key):
    if not re.fullmatch(r'[a-f0-9]{64}', str(key)):
        raise ValueError('传输任务 ID 无效')
    with _db() as db:
        value = db.execute('SELECT body FROM jobs WHERE id=?', (key,)).fetchone()
    if not value:
        raise ValueError('传输任务不存在')
    return json.loads(value[0])


def listing(project_id=None):
    with _db() as db:
        rows = [json.loads(r[0]) for r in db.execute('SELECT body FROM jobs')]
    return [public(r) for r in sorted(rows, key=lambda r:r['created_at'], reverse=True)
            if project_id is None or r['project_id'] == project_id]


def public(row):
    result = dict(row)
    part = _base()/row['id']/'payload.part'
    result['bytes_transferred'] = row.get('bytes_transferred', 0)
    if row['state'] != 'COMPLETED' and part.is_file():
        result['bytes_transferred'] = min(part.stat().st_size, row['source']['size_bytes'])
    result['size_bytes'] = row['source']['size_bytes']
    result['download_url'] = '/api/file-transfers/'+row['id']+'/download' if row['state']=='COMPLETED' else None
    return result


def get(key):
    return public(_get(key))


def _stamp(path):
    if path.is_symlink():raise ValueError('来源必须是普通文件')
    value = path.stat()
    if not stat.S_ISREG(value.st_mode):raise ValueError('来源必须是普通文件')
    return {'path':str(path.resolve()), 'size_bytes':value.st_size, 'mtime_ns':value.st_mtime_ns}


def observe(location):
    if location['kind']=='local':
        return {**_stamp(Path(location['path'])), 'kind':'local'}
    alias = location['alias']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', str(alias)):
        raise ValueError('SSH 别名无效')
    code = """import os,stat,json,sys
p=os.path.expandvars(sys.argv[1]);s=os.stat(p,follow_symlinks=False)
if not stat.S_ISREG(s.st_mode):raise ValueError('not a regular file')
print(json.dumps({'path':os.path.realpath(p),'size_bytes':s.st_size,'mtime_ns':s.st_mtime_ns}))"""
    with site_registration.host_operation(alias):
        connection = dict(site_registration.connection_identity(alias))
        receipt = remote_transport.run(alias, shlex.quote(remote_transport._interpreter(alias))+' -c '+shlex.quote(code)+' '+shlex.quote(location['path']), timeout=30, mutating=False)
        if receipt.get('exit_code') != 0 or receipt.get('stdout_truncated'):
            raise ValueError('无法读取来源文件：'+receipt.get('stderr','')[-500:])
        fact = json.loads(receipt['stdout'])
        if not isinstance(fact.get('size_bytes'),int) or fact['size_bytes']<0 or not fact.get('path','').startswith('/'):
            raise ValueError('来源文件回执无效')
        return {**fact, 'kind':'remote', 'alias':alias, 'connection':connection}


def _enqueue(project_id, source, producer_kind, producer_id, name, reference=None, background=True):
    identity = [project_id, source, producer_kind, producer_id, name]
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    with exclusive_lock(_base_lock(key)) as owned:
        if not owned:raise ValueError('正在准备同一文件，请稍后重试')
        try:
            row = _get(key)
            if row['state']=='COMPLETED' and not (_base()/key/'payload').is_file():
                row.update(state='FAILED',error='已收集的本机副本缺失，请继续收集');_save(row)
        except ValueError:
            row = _save({'id':key,'project_id':project_id,'producer_kind':producer_kind,
                         'producer_id':producer_id,'name':name,'reference':reference,
                         'source':source,'background':background,'state':'PENDING','created_at':time.time(),'bytes_transferred':0})
    _event.set()
    return public(row)


def _base_lock(key):
    base = _base()/'locks';base.mkdir(parents=True, exist_ok=True)
    return base/(key+'.lock')


def request(project_id, reference):
    from . import artifact_refs
    if not isinstance(reference,dict):raise ValueError('请选择明确产物引用')
    with attempt_operations.locked({reference.get('producer_id','')}):
        resolved = artifact_refs.resolve(project_id, reference)
        location = next((x for x in resolved['locations'] if x['kind']=='local'),resolved['locations'][0])
        source = observe(location)
        expected = reference['version']
        if expected.get('size_bytes') is not None and expected['size_bytes']!=source['size_bytes']:
            raise ValueError('来源大小已变化，请刷新文件清单再选择')
        if (source['kind']=='remote' or resolved.get('origin_kind','local')=='local') and expected.get('mtime_ns') is not None and expected['mtime_ns']!=source['mtime_ns']:
            raise ValueError('来源修改时间已变化，请刷新文件清单再选择')
        if expected.get('modified_at') and source['kind']=='remote':
            from datetime import datetime
            if int(datetime.fromisoformat(expected['modified_at']).timestamp())!=source['mtime_ns']//10**9:
                raise ValueError('来源修改时间已变化，请刷新文件清单再选择')
        return _enqueue(project_id, source, reference['producer_kind'],reference['producer_id'],reference['artifact_id'],reference)


def dependents(kind, key):
    return ['文件收集 '+r['name'] for r in listing() if r['producer_kind']==kind and r['producer_id']==key
            and r['state'] in ACTIVE|{'FAILED'}]


def action(key, operation):
    if operation not in ('retry','cancel'):raise ValueError('未知传输操作')
    _get(key)
    with exclusive_lock(_base_lock(key)) as available:
        if operation=='retry' and not available:raise ValueError('正在结束原传输，请稍后继续收集')
    # Lock differs from the worker's file lock, so cancel can reach a running process.
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = json.loads(db.execute('SELECT body FROM jobs WHERE id=?',(key,)).fetchone()[0])
        if operation=='cancel' and row['state']!='COMPLETED':row['state']='CANCELLED'
        elif operation=='retry' and row['state'] in ('FAILED','CANCELLED'):row.update(state='PENDING',error=None,background=True)
        db.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(row),key))
    _event.set()
    return get(key)


def _running(key):
    return not _stop.is_set() and _get(key)['state']=='RUNNING'


def _copy_local(row, part):
    source = Path(row['source']['path'])
    offset = part.stat().st_size if part.exists() else 0
    if offset>row['source']['size_bytes']:part.unlink();offset=0
    with source.open('rb') as reader, part.open('ab') as writer:
        reader.seek(offset)
        while True:
            if not _running(row['id']):raise InterruptedError('文件收集已暂停；部分文件已保留')
            block = reader.read(1024*1024)
            if not block:break
            writer.write(block)


def _copy_remote(row, part):
    if os.environ.get('FPILOT_REMOTE_DISABLED')=='1':raise ValueError('隔离验收禁止远端文件传输')
    source = row['source'];alias = source['alias']
    if not shutil.which('rsync'):raise ValueError('本机尚无 rsync，无法进行可恢复文件传输')
    # Only the private .part is modified in-place. Readers see payload only after verification.
    argv = ['rsync','--partial','--inplace','--no-whole-file','--timeout=60','-t',
            '-e','ssh -o BatchMode=yes -o ConnectTimeout=15', '--',
            alias+':'+shlex.quote(source['path']),str(part)]
    with (part.parent/'transport.log').open('wb') as log:
        process = subprocess.Popen(argv,stdout=log,stderr=log)
        try:
            while process.poll() is None:
                if not _running(row['id']):
                    process.terminate();raise InterruptedError('文件收集已暂停；部分文件已保留')
                _stop.wait(.25)
            if process.returncode:
                with (part.parent/'transport.log').open('rb') as reader:
                    reader.seek(max(0,reader.seek(0,2)-2000));message=reader.read().decode(errors='replace')
                raise ValueError('文件传输失败（两端须支持 rsync），可继续收集：'+message)
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()


def run(key):
    """Claim once across processes. Restart leaves the private partial available."""
    with exclusive_lock(_base_lock(key)) as owned:
        if not owned:return get(key)
        row = _get(key)
        if row['state'] not in ACTIVE:return public(row)
        folder = _base()/key;folder.mkdir(exist_ok=True)
        part = folder/'payload.part'
        try:
            with attempt_operations.locked({row['producer_id']}), (site_registration.host_operation(row['source']['alias']) if row['source']['kind']=='remote' else nullcontext()):
                source=row['source']
                if source['kind']=='remote' and site_registration.connection_identity(source['alias'])!=source['connection']:
                    raise ValueError('服务器别名的连接已改变，请重新选择产物')
                from . import recycle, file_lifecycle
                owner='attempts' if row['producer_kind']=='attempt' else 'analyses'
                if recycle.inactive(owner,row['producer_id']) or (source['kind']=='remote' and file_lifecycle.remote_is_cleaned(owner,row['producer_id'])):raise ValueError('来源已回收或清理，请先恢复原记录')
                if observe(source)!=source:raise ValueError('来源文件版本已变化，请重新选择产物')
                with _db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current=json.loads(db.execute('SELECT body FROM jobs WHERE id=?',(key,)).fetchone()[0])
                    if current['state'] not in ACTIVE:return public(current)
                    row.update(state='RUNNING',error=None)
                    db.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(row),key))
                (_copy_local if source['kind']=='local' else _copy_remote)(row,part)
                if observe(source)!=source:raise ValueError('传输期间来源文件已变化，请重新选择产物')
                if part.stat().st_size!=source['size_bytes']:raise ValueError('收集文件大小不完整')
                # Serialize publication with cancel so a cancelled task cannot report completed.
                with _db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current=json.loads(db.execute('SELECT body FROM jobs WHERE id=?',(key,)).fetchone()[0])
                    if current['state']!='RUNNING' or _stop.is_set():raise InterruptedError('文件收集已暂停')
                    os.replace(part,folder/'payload')
                    row.update(state='COMPLETED',bytes_transferred=source['size_bytes'],error=None)
                    db.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(row),key))
        except Exception as exc:
            with _db() as db:
                db.execute('BEGIN IMMEDIATE')
                current=json.loads(db.execute('SELECT body FROM jobs WHERE id=?',(key,)).fetchone()[0])
                current.update(error=str(exc)[-2500:])
                if current['state']!='CANCELLED':current['state']='PENDING' if _stop.is_set() else 'FAILED'
                db.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(current),key))
        return get(key)


def download(key):
    row=_get(key);path=_base()/key/'payload'
    if row['state']!='COMPLETED' or not path.is_file() or path.stat().st_size!=row['source']['size_bytes']:
        raise ValueError('文件尚未收集完成，请继续收集')
    return path,Path(row['name']).name


def prepare_source(source, name, destination):
    """Host input adapter: same queue/cache, then a create-only local copy."""
    from datetime import datetime
    fact=next(f for f in source['files'] if f['name']==name)
    location={'kind':'remote','alias':source['alias'],'path':source['remote_root'].rstrip('/')+'/'+name}
    with attempt_operations.locked({source['attempt_id']}):
        stamp=observe(location)
        if stamp['size_bytes']!=fact['size_bytes'] or stamp['mtime_ns']//10**9!=int(datetime.fromisoformat(fact['modified_at']).timestamp()):
            raise ValueError('来源文件版本已变化，请刷新来源后重新准备')
        row=_enqueue(source.get('project_id'),stamp,'attempt',source['attempt_id'],name,background=False)
        if row['state'] in ('FAILED','CANCELLED'):
            fresh=_get(row['id']);fresh.update(state='PENDING',error=None,background=False);_save(fresh)
        row=run(row['id'])
        if row['state']!='COMPLETED':raise ValueError(row.get('error') or '文件正在收集，请稍后继续准备')
        path,_=download(row['id']);destination.parent.mkdir(parents=True,exist_ok=True)
        temporary=destination.with_name('.'+destination.name+'.preparing')
        try:
            with path.open('rb') as reader,temporary.open('wb') as writer:shutil.copyfileobj(reader,writer)
            os.link(temporary,destination)
        finally:temporary.unlink(missing_ok=True)
    return destination


def _worker():
    while not _stop.is_set():
        # One transfer at a time bounds disk/network usage and avoids duplicate claims.
        for row in listing():
            if _stop.is_set():break
            if row['state'] in ACTIVE and row.get('background',True):run(row['id'])
        _event.wait(2);_event.clear()


def start():
    global _thread
    if _thread and _thread.is_alive():return
    _stop.clear();_thread=threading.Thread(target=_worker,name='file-collection',daemon=True);_thread.start()


def stop():
    _stop.set();_event.set()
    if _thread:_thread.join(timeout=7)
