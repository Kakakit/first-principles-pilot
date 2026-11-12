"""Durable ownership and explicit cleanup of frozen, individually owned remote directories."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import re
import shlex
import sqlite3
import time
import uuid
from threading import RLock

from . import attempt_operations, execution as ex, recycle, remote_cleanup, remote_paths, site_registration

ATTEMPT_END = {'PREPARED', 'COMPLETED', 'FAILED', 'CANCELLED'}
ANALYSIS_END = {'COMPLETED', 'FAILED', 'NOT_APPLICABLE'}
OWNERS = {'attempts', 'analyses', 'local-analyses', 'plots'}
RETRY = {'pending', 'running', 'waiting', 'unknown'}
DONE = {'deleted', 'already_missing'}
RECOVERY_TTL = 600
_DB_LOCK = RLock()


def _db():
    recycle.ROOT.mkdir(parents=True,exist_ok=True)
    connection = sqlite3.connect(str(recycle.ROOT/'cleanup.sqlite3'),timeout=10)
    connection.execute('''CREATE TABLE IF NOT EXISTS cleanup_assets(
        id TEXT PRIMARY KEY, state TEXT NOT NULL, body TEXT NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS cleanup_receipts(
        operation TEXT NOT NULL, asset_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(operation, asset_id))''')
    connection.execute("CREATE INDEX IF NOT EXISTS cleanup_owner_state ON cleanup_assets(json_extract(body,'$.kind'),json_extract(body,'$.key'),state)")
    return connection


def remote_is_cleaned(kind,key):
    database=recycle.ROOT/'cleanup.sqlite3'
    if not database.is_file():return False
    with _DB_LOCK, closing(sqlite3.connect(str(database),timeout=10)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='cleanup_assets'").fetchone():return False
        return bool(db.execute("SELECT 1 FROM cleanup_assets WHERE json_extract(body,'$.kind')=? AND json_extract(body,'$.key')=? AND state IN ('deleted','already_missing') LIMIT 1",
                               (kind,key)).fetchone())


def _assets(where='', values=()):
    with _DB_LOCK, closing(_db()) as db:
        return [json.loads(row[0]) for row in db.execute('SELECT body FROM cleanup_assets '+where, values)]


def _store(asset, previous=None):
    with _DB_LOCK, closing(_db()) as db, db:
        if previous:
            db.execute('INSERT INTO cleanup_receipts VALUES (?,?,?)',
                       (previous['operation'],previous['id'],json.dumps(_public(previous),ensure_ascii=False)))
        db.execute('INSERT OR REPLACE INTO cleanup_assets VALUES (?,?,?)',
                   (asset['id'],asset['state'],json.dumps(asset,ensure_ascii=False)))


def _stored(asset_id):
    return next(iter(_assets('WHERE id=?',(asset_id,))),None)


def _analysis_rows():
    for path in (recycle.ROOT/'analyses-v2').glob('*/analysis.json'):
        yield json.loads(path.read_text())


def _analysis_owner(record):
    # Source is lineage. A multi-node analysis has no exclusive node owner.
    if 'owner_node_id' in record:return record['owner_node_id']
    if record.get('node_id'):return record['node_id']
    from .calculation_nodes import owner
    sources=[s.get('attempt_id') for s in record.get('sources',[]) if s.get('kind')=='attempt']
    identities=set()
    for key in sources:
        try: attempt=ex.get(key)
        except (ValueError,KeyError,TypeError):return None
        identities.add((attempt.get('project_id'),owner(attempt)))
    if len(identities)==1:
        project,node=identities.pop()
        if project==record.get('project_id'):return node
    return None


def _record(kind,key):
    if kind=='temporary':return None
    if kind=='attempts':
        try:return ex.get(key)
        except ValueError:return None
    try:return recycle._record(kind,key)
    except FileNotFoundError:return None


def _candidate(kind,record):
    from .calculation_nodes import owner
    if kind=='attempts':
        alias=(record.get('config') or {}).get('alias')
        path=record.get('remote_path_absolute')
        path=path or record.get('remote_path') or record.get('workdir')
        node=owner(record)
        connection=(record.get('settings_snapshot') or {}).get('connection')
    else:
        alias=(record.get('runtime') or {}).get('alias') or record.get('alias') or (record.get('config') or {}).get('alias')
        path=record.get('remote_dir') or record.get('remote_dir_planned') or record.get('remote_path_absolute')
        if not path and kind=='analyses' and record.get('remote_analysis_root'):
            path=remote_paths.join_workspace(record['remote_analysis_root'],record['id'])
        node=_analysis_owner(record) if kind=='analyses' else record.get('node_id')
        connection=record.get('remote_connection')
    if not alias or not isinstance(path,str) or (kind=='analyses' and record.get('location')=='local'):
        return None
    key=record['id']
    asset_id=hashlib.sha256(json.dumps([kind,key,alias,path]).encode()).hexdigest()
    return {'id':asset_id,'kind':kind,'key':key,'project_id':record.get('project_id'),'node_id':node,
            'alias':alias,'path':path,'connection':connection,'state':'retained','owner_state':record.get('state'),
            'label':record.get('job_id') or record.get('title') or (record.get('manifest') or {}).get('name') or key[:12]}


def remember(kind,key,record=None):
    """Persist before hiding or purging, independently of the owner record."""
    if kind not in OWNERS:return
    record=record or _record(kind,key)
    if not record:return
    asset=_candidate(kind,record)
    if not asset:return
    with attempt_operations.locked({asset['id']}):
        saved=_stored(asset['id'])
        if saved:
            _store({**asset,**saved,'owner_state':record.get('state'),'target':_target(record,kind)})
        else:_store({**asset,'recorded_at':ex.now(),'retained_at':ex.now(),'target':_target(record,kind)})


def before_purge(kind,key):
    if kind not in OWNERS:return
    record=_record(kind,key)
    if not record:return
    remember(kind,key,record)
    active=(kind=='attempts' and record.get('state') not in ATTEMPT_END or
            kind=='analyses' and record.get('state') not in ANALYSIS_END|{'QUEUED','WAIT_RESOURCE'})
    if active:
        raise ValueError('远端任务尚无明确终态，保留本地跟踪记录；终态核实后可彻底清理')


def _attempt_dependents(key, recycling_analyses=()):
    refs=[]
    for row in recycle._rows('attempts'):
        if row['id']!=key and row.get('state') not in ATTEMPT_END-{'PREPARED'} and ('attempts',key) in recycle.source_ids(row):
            refs.append('下游任务 '+row['id'][:12])
    for row in _analysis_rows():
        if (row.get('state') in ('QUEUED','WAIT_RESOURCE') and not row.get('submission_started') and not row.get('dispatch_started_at')
                and (row['id'] in recycling_analyses or recycle.inactive('analyses',row['id'],row))):
            continue
        if row.get('state') not in ANALYSIS_END and any(s.get('attempt_id')==key for s in row.get('sources',[])):
            refs.append('分析 '+row['id'][:12])
    for table in ('scan_batches','chain_runs'):
        for row in recycle._rows(table):
            if row.get('state') not in ('COMPLETE','COMPLETED','CANCELLED','FAILED') and ('attempts',key) in recycle.source_ids(row):
                refs.append('计算链或批次 '+row['id'][:12])
    return sorted(set(refs))


def _check(asset,record,recycling_analyses=()):
    kind,key=asset['kind'],asset['key']
    if record and (_candidate(kind,record) or {}).get('id')!=asset['id']:
        return '原记录的主机或目录已改变'
    if kind=='temporary':
        if not re.fullmatch(r'[a-f0-9]{32}',key) or not asset['path'].endswith('/extension-checks/'+key):
            return '自检目录身份不明'
        return None
    if kind=='attempts':
        frozen=(record or asset.get('target'))
        if not frozen or frozen.get('state') not in ATTEMPT_END:
            return '等待任务结束并确认终态'
        try:remote_cleanup.validate_record(frozen)
        except (ValueError,TypeError,KeyError) as exc:return str(exc)
        refs=_attempt_dependents(key,recycling_analyses)
        if refs:return '仍有来源引用 · '+'、'.join(refs[:8])
    elif kind=='analyses':
        analysis=record or asset.get('target') or {}
        if analysis.get('state') not in ANALYSIS_END:
            return '等待分析结束并确认终态'
        if not re.fullmatch(r'[a-f0-9]{32}',key) or not asset['path'].endswith('/'+key):
            if analysis.get('adopted_completed_analysis') is True and analysis.get('original_analysis_remote_path'):
                return '这是补登记的分析，记录引用外部结果。原始分析目录不属于此回收操作。'
            return '不是已登记的独立分析目录'
        try:
            if remote_paths.validate_root(asset['path'])!=asset['path']:return '目录路径无法核对'
        except ValueError:return '目录路径无法核对'
    else:
        return '历史工具目录仅保留位置，无法自动核对归属'
    return None


def _target(record,kind):
    fields=('id','state','config','job_id','path_schema','remote_path_absolute','remote_path_relative',
            'remote_workspace_root','remote_workspace_root_abs','attempt_segment','attempt_relpath',
            'path_version','remote_relative_path','project_id','kind','attempt_path','remote_project_relative',
            'settings_snapshot','remote_project_path_absolute','remote_path','remote_path_policy',
            'project_slug','stage_slug','stage_sequence','flow_slug','stage_segment','path_schema_version')
    if kind=='analyses':
        fields=('id','state','runtime','location','remote_dir','remote_dir_planned',
                'adopted_completed_analysis','original_analysis_remote_path')
    return {k:record[k] for k in fields if k in record}


def _public(asset):
    fields=('target','connection','token','verify_only','recovery_preview','expected_identity')
    return {**{k:v for k,v in asset.items() if k not in fields},
            'recovery_required':asset['state']=='blocked' and bool(asset.get('verify_only'))}


def preview(kind,key,project_id=None):
    if kind=='nodes':
        if not project_id:raise ValueError('缺少所属项目')
        candidates=list(recycle.node_objects(project_id,key))
    elif kind=='projects':
        recycle.marker(kind,key)
        candidates=list(recycle._project_objects(key))
    elif kind in OWNERS:
        recycle.marker(kind,key)
        candidates=[(kind,key)]
    else:raise ValueError('此记录类型没有远端目录清理入口')
    recycling_analyses={key for kind,key in candidates if kind=='analyses'}
    assets=[]
    remote_note=None
    for owner_kind,owner_key in candidates:
        if owner_kind not in OWNERS:continue
        record=_record(owner_kind,owner_key)
        if not record:continue
        if project_id and record.get('project_id')!=project_id:raise ValueError('记录不属于所选项目')
        asset=_candidate(owner_kind,record)
        if asset:
            saved=_stored(asset['id'])
            assets.append({**_public(saved or asset),'blocked':_check(asset,record,recycling_analyses)})
        elif kind=='analyses' and record.get('location')=='local':
            remote_note='这次分析在本机运行，结果只保存在本机。'
    plan={'kind':kind,'key':key,'records':[{'kind':k,'key':v} for k,v in candidates],'assets':assets}
    if remote_note:plan['remote_note']=remote_note
    return plan


def queue(asset_ids,allowed=None,check_operation=None):
    if not isinstance(asset_ids,list) or any(not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{64}',key) for key in asset_ids):
        raise ValueError('请选择预览中登记的远端目录')
    if allowed is not None and not set(asset_ids).issubset(allowed):
        raise ValueError('清理对象已变化，请重新打开删除预览')
    if check_operation is not None and (not isinstance(check_operation,str) or len(asset_ids)!=1):
        raise ValueError('请选择一条原操作')
    operation=uuid.uuid4().hex
    for key in dict.fromkeys(asset_ids):
        with attempt_operations.locked({key}):
            asset=_stored(key)
            if check_operation is not None and (not asset or not asset.get('verify_only') or asset.get('operation')!=check_operation):
                raise ValueError('原清理状态已变化，请刷新清理状态')
            if not asset:
                historical=next((a for a in list_assets() if a['id']==key and a.get('historical_candidate')),None)
                if not historical:raise ValueError('远端目录登记不存在')
                record=_record(historical['kind'],historical['key'])
                asset=_candidate(historical['kind'],record) if record else None
                if not asset or asset['id']!=key:raise ValueError('远端目录登记已变化')
                asset['recorded_at']=asset['retained_at']=ex.now()

            if asset['state'] in DONE|RETRY:continue
            record=_record(asset['kind'],asset['key'])
            if record:
                asset['target']=_target(record,asset['kind'])
            blocker=_check(asset,record)
            original=asset.get('operation') if asset.get('verify_only') else None
            asset.update(operation=original or operation,state='blocked' if blocker else 'pending',error=blocker,
                         requested_at=ex.now(),token=asset.get('token') or uuid.uuid4().hex)
            _store(asset)
    assets=[_public(_stored(key)) for key in dict.fromkeys(asset_ids)]
    operations={asset.get('operation') for asset in assets}
    return {'operation':next(iter(operations)) if len(operations)==1 else operation,'assets':assets}


def execute(kind,key,project=None,project_id=None,asset_ids=None):
    selected=[] if asset_ids is None else asset_ids
    plan=preview(kind,key,project_id)
    allowed={a['id'] for a in plan['assets']}
    if not isinstance(selected,list) or any(not isinstance(value,str) or value not in allowed for value in selected):
        raise ValueError('远端位置已变化，请重新打开删除预览')
    result=recycle.move(kind,key,project,project_id=project_id)
    queued=queue(selected,allowed)
    return {**result,**queued}


def list_assets():
    values={a['id']:a for a in _assets("WHERE state!='temporary' OR json_extract(body,'$.recorded_at')<? ORDER BY rowid DESC",(ex.now()[:10],))}
    # Old recycle markers still have their original owner records. Expose
    # their frozen paths even before the first purge or explicit cleanup.
    for a in values.values():
        if a['state']=='temporary':a.update(state='retained',error='旧自检回执未返回；清理时核对自检锁')
    for row in recycle.all_items():
        kind,key=row['kind'],row['key']
        if kind not in OWNERS:continue
        record=_record(kind,key)
        asset=_candidate(kind,record) if record else None
        if asset and asset['id'] in values:values[asset['id']]['owner_state']=record.get('state')
        if asset and asset['id'] not in values:
            values[asset['id']]={**asset,'historical_candidate':True}
    return [_public(a) for a in values.values()]


def operation_status(operation):
    with _DB_LOCK, closing(_db()) as db:
        rows=db.execute("""SELECT body, 1 FROM cleanup_receipts WHERE operation=?
                          UNION ALL
                          SELECT body, 0 FROM cleanup_assets WHERE json_extract(body,'$.operation')=?""",(operation,operation))
        return [json.loads(body) if archived else _public(json.loads(body)) for body,archived in rows]


def _recovery_asset(asset_id):
    asset=_stored(asset_id)
    if not asset or asset['state']!='blocked' or not asset.get('verify_only') or not asset.get('operation'):
        raise ValueError('此目录当前不需要重新预览，请刷新清理状态')
    return asset


def preview_recovery(asset_id):
    """Read the current directory identity without continuing the old deletion."""
    asset=_recovery_asset(asset_id)
    with attempt_operations.locked({asset_id,asset['key']}),site_registration.host_operation(asset['alias']):
        asset=_recovery_asset(asset_id)
        record=_record(asset['kind'],asset['key'])
        target=record or asset['target']
        connection=site_registration.connection_identity(asset['alias'])
        blocker=_check(asset,record) or _host_blocker(asset,target,connection)
        if blocker:raise ValueError(blocker)
        script=_directory_setup(asset)+'''test ! -e "$hold" && test ! -L "$hold"
test -d "$dest" && test ! -L "$dest"
'''+_ownership_checks(asset,target)+_directory_identity()+'printf "FPILOT_CLEANUP_IDENTITY=%s\\n" "$identity"\n'
        receipt=ex.ssh(asset['alias'],script,timeout=30,stdout_limit=3000,mutating=False)
        match=re.search(r'^FPILOT_CLEANUP_IDENTITY=([0-9: .+\-]+)$',receipt.get('stdout',''),re.M)
        if receipt.get('exit_code')!=0 or receipt.get('stdout_truncated') or not match:
            raise ValueError('无法确认当前目录归属，或原操作仍有待核对的目录。可先核对原操作。')
        frozen={'token':uuid.uuid4().hex,'identity':match[1],'connection':connection,'expires_at':time.time()+RECOVERY_TTL}
        _store({**asset,'recovery_preview':frozen})
        return {'token':frozen['token'],'alias':asset['alias'],'path':asset['path']}


def confirm_recovery(asset_id,token):
    asset=_recovery_asset(asset_id)
    with attempt_operations.locked({asset_id,asset['key']}):
        asset=_recovery_asset(asset_id)
        frozen=asset.get('recovery_preview')
        if not frozen or frozen['token']!=token or frozen['expires_at']<=time.time():
            raise ValueError('当前目录的预览已失效，请重新预览')
        record=_record(asset['kind'],asset['key'])
        blocker=_check(asset,record)
        if blocker:raise ValueError(blocker)
        new={**asset,'operation':uuid.uuid4().hex,'token':uuid.uuid4().hex,'state':'pending','error':None,
             'verify_only':False,'expected_identity':frozen['identity'],'connection':frozen['connection'],'requested_at':ex.now()}
        for key in ('recovery_preview','checked_at','finished_at'):new.pop(key,None)
        _store(new,previous=asset)
        return {'operation':new['operation'],'assets':[_public(new)]}


def temporary(alias,path,key,connection,python):
    asset_id=hashlib.sha256(json.dumps(['temporary',key,alias,path]).encode()).hexdigest()
    asset={'id':asset_id,'kind':'temporary','key':key,'alias':alias,'path':path,'connection':connection,
           'label':'扩展环境自检','state':'temporary','recorded_at':ex.now(),'target':{'id':key,'python':python}}
    _store(asset)
    return asset


def finish_temporary(asset,retained,error=None):
    if retained:
        _store({**asset,'state':'retained','error':error})
    else:
        with _DB_LOCK, closing(_db()) as db, db:
            db.execute('DELETE FROM cleanup_assets WHERE id=?',(asset['id'],))



def _directory_setup(asset):
    path=remote_paths.shell_path(asset['path'])
    suffix=shlex.quote('.fpilot-cleanup-'+asset['token'])
    return f'''set -e
dest={path}
hold="$dest"{suffix}
parent=$(dirname -- "$dest")
test -d "$parent" && test -x "$parent" || exit 9
test "$(cd "$parent" && pwd -P)" = "$parent"
'''


def _directory_identity():
    return '''identity=$(stat -c '%d:%i:%z' -- "$dest" 2>/dev/null || stat -f '%d:%i:%Fc:%B' "$dest")
'''


def _owned_script(asset,target,verify_only):
    setup=_directory_setup(asset)+'''if test -e "$hold" || test -L "$hold"; then
  test -d "$hold" && test ! -L "$hold"
  test "$(cd "$hold" && pwd -P)" = "$hold"
  rm -rf -- "$hold"
  test ! -e "$hold" && test ! -L "$hold"
  printf 'FPILOT_CLEANUP=deleted\n'
  exit 0
fi
if test ! -e "$dest" && test ! -L "$dest"; then
  printf 'FPILOT_CLEANUP=already_missing\n'
  exit 0
fi
'''
    if verify_only:
        return setup+'printf "FPILOT_CLEANUP=unverified\\n"\n'
    checks=_ownership_checks(asset,target)
    if asset.get('expected_identity'):
        checks+=_directory_identity()+f'''if test "$identity" != {shlex.quote(asset['expected_identity'])}; then
  printf 'FPILOT_CLEANUP=changed\n'
  exit 7
fi
'''
    return setup+checks+'''
test ! -e "$hold" && test ! -L "$hold"
printf 'FPILOT_CLEANUP_STARTED\\n'
mv -- "$dest" "$hold"
rm -rf -- "$hold"
test ! -e "$hold" && test ! -L "$hold"
printf 'FPILOT_CLEANUP=deleted\n'
'''


def _ownership_checks(asset,target):
    if asset['kind']=='attempts':
        checks=remote_cleanup._script(target,list_files=False)
    elif asset['kind']=='temporary':
        identity=shlex.quote(asset['key'])
        python=shlex.quote(target['python'])
        lock=shlex.quote("import fcntl,sys; lock=open(sys.argv[1]); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)")
        checks=f'''test -d "$dest" && test ! -L "$dest"
test "$(cd "$dest" && pwd -P)" = "$dest"
test ! -e "$dest/.fpilot-doctor-unconfirmed"
test -f "$dest/.fpilot-doctor-owner" && test ! -L "$dest/.fpilot-doctor-owner"
test -f "$dest/.fpilot-doctor.lock" && test ! -L "$dest/.fpilot-doctor.lock"
test "$(cat "$dest/.fpilot-doctor-owner")" = {identity}
{python} -c {lock} "$dest/.fpilot-doctor.lock"
'''
    else:
        identity=shlex.quote(target['id'])
        interpreter=shlex.quote((target.get('runtime') or {}).get('python') or 'python3')
        code="import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get('analysis_id')==sys.argv[2] else 1)"
        checks=f'''test -d "$dest" && test ! -L "$dest"
test "$(cd "$dest" && pwd -P)" = "$dest"
if test -f "$dest/.fpilot-analysis-owner" && test ! -L "$dest/.fpilot-analysis-owner"; then
  test "$(cat "$dest/.fpilot-analysis-owner")" = {identity}
else
  test -f "$dest/request.json" && test ! -L "$dest/request.json"
  {interpreter} -c {shlex.quote(code)} "$dest/request.json" {identity}
fi
'''
    return checks


def _host_blocker(asset,target,connection=None):
    from .project_sync import locally_executable, continuation_connection
    if asset['alias'] in site_registration.removed_records():
        return '服务器登记已移除'
    if asset.get('project_id') and not locally_executable(asset['project_id']):
        return '项目已交接或待核验'
    saved=asset.get('connection') or (target.get('settings_snapshot') or {}).get('connection')
    if saved and not continuation_connection(saved,connection or site_registration.connection_identity(asset['alias']),asset['alias']):
        return 'SSH 别名已指向其他服务器'
    return None


def _advance(asset):
    with attempt_operations.locked({asset['id'],asset['key']}),site_registration.host_operation(asset['alias']):
        asset=_stored(asset['id'])
        if asset['state'] not in RETRY:return
        verify_only=asset['state'] in ('running','unknown') or asset.get('verify_only',False)
        asset={**asset,'verify_only':verify_only}
        record=_record(asset['kind'],asset['key'])
        blocker=_check(asset,record)
        if blocker:
            _store({**asset,'state':'blocked','error':blocker});return
        target=record or asset['target']
        blocker=_host_blocker(asset,target)
        if blocker:
            state='waiting' if asset['alias'] in site_registration.removed_records() else 'blocked'
            _store({**asset,'state':state,'error':blocker});return
        asset={**asset,'checked_at':ex.now()}
        _store({**asset,'state':'running','error':None})
        try:
            receipt=ex.ssh(asset['alias'],_owned_script(asset,target,verify_only),timeout=90,stdout_limit=3000,mutating=True)
        except Exception as exc:
            _store({**asset,'state':'unknown','verify_only':True,'error':str(exc)[:500]});return
        lines=receipt.get('stdout','').splitlines()
        result=next((line.split('=',1)[1] for line in lines if line.startswith('FPILOT_CLEANUP=')),None)
        if receipt.get('exit_code')==0 and result in DONE:
            _store({**asset,'state':result,'error':None,'finished_at':ex.now()})
        elif result=='changed':
            _store({**asset,'state':'blocked','verify_only':True,'error':'目录在预览后已变化，请重新预览当前目录。'})
        elif receipt.get('exit_code') is None:
            state='unknown' if verify_only or receipt.get('transport_state')!='NOT_SENT' else 'waiting'
            _store({**asset,'state':state,'verify_only':state=='unknown','error':receipt.get('stderr') or '连接中断，等待核对原目录'})
        elif receipt.get('stdout_truncated') or 'FPILOT_CLEANUP_STARTED' in lines or (receipt.get('exit_code')==0 and result!='unverified'):
            _store({**asset,'state':'unknown','verify_only':True,'error':'远端移除回执未完整返回，等待核对原目录'})
        else:
            verify_only=verify_only or bool(asset.get('expected_identity'))
            _store({**asset,'state':'failed' if not verify_only else 'blocked','verify_only':verify_only,
                    'error':('原目录仍存在或无法核对，仅保留回执。' if verify_only else '目录身份、调度状态或权限未通过核对。')+(receipt.get('stderr') or '')[-300:]})


def tick():
    rows=_assets("WHERE state IN ('pending','running','waiting','unknown') ORDER BY CASE state WHEN 'pending' THEN 0 WHEN 'running' THEN 1 WHEN 'unknown' THEN 2 ELSE 3 END, json_extract(body,'$.checked_at'), rowid LIMIT 32")
    def advance(asset):
        try:_advance(asset)
        except attempt_operations.AttemptBusy:pass
        except Exception as exc:
            current=_stored(asset['id'])
            if current and current['state'] in RETRY:
                _store({**current,'error':str(exc)[:500]})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(advance,rows))
