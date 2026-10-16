"""Explicit project handoff; local and remote execution fencing share one gate."""
import base64
import copy
import json
import platform
import shlex
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from . import execution as ex, project_snapshot as snapshot, remote_paths, servers
from .attempt_operations import AttemptBusy, locked
from .execution_control import write_json
from .platform_runtime import exclusive_lock
from .storage import WorkspaceStore


class SyncStopped(AttemptBusy):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def local_path(pid):
    return ex.ROOT / 'project-sync' / (snapshot.digest(pid.encode()) + '.json')


def local(pid):
    path = local_path(pid)
    return json.loads(path.read_bytes()) if path.exists() else None


def device():
    path = ex.ROOT / 'project-sync' / 'device.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(path.with_suffix('.lock')) as acquired:
        if not acquired:
            raise SyncStopped('设备身份正在初始化，请重试')
        if not path.exists():
            write_json(path, {'id': uuid.uuid4().hex, 'name': platform.node()})
        return json.loads(path.read_bytes())


def registered(alias):
    host = servers.load_registry()['hosts'].get(alias)
    if not host:
        raise ValueError('请先在本机登记同一服务器的 SSH 别名')
    root = remote_paths.validate_root(host.get('remote_workspace_root', '$HOME'))
    return {'alias': alias, 'root': root}


def remote(config, action, pid=None, data=b'', **values):
    from .remote_transport import _interpreter
    source = Path(__file__).with_name('project_sync_remote.py').read_text()
    from .snapshot_sync import shell_directory
    path=shell_directory(config['root']) if config.get('snapshot_directory') else remote_paths.shell_path(config['root'])
    command = shlex.quote(_interpreter(config['alias'])) + ' -c ' + shlex.quote(source) + ' ' + path
    request = {'action': action, **values}
    if config.get('snapshot_directory'):request['snapshot_directory']=True
    if pid is not None:request['project_id'] = pid
    receipt = ex.ssh(config['alias'], command, snapshot.packed(request) + b'\n' + data,
                     timeout=180, stdout_limit=4 * 1024 * 1024, mutating=action not in ('status', 'catalog', 'missing', 'get', 'attempt', 'versions'))
    if receipt.get('exit_code') is None or receipt.get('stdout_truncated'):
        from .execution_control import redact
        detail=redact(receipt.get('stderr',''))[-600:]
        raise SyncStopped('SSH 回执不明，请核查原地址；未确认的传输不要重复执行'+('：'+detail if detail else ''))
    try:
        value = json.loads(receipt.get('stdout', ''))
    except ValueError:
        raise SyncStopped('同步回执不可读，已停止派发；请先核查同步状态')
    if not value.get('ok'):
        raise ValueError(value.get('error') or receipt.get('stderr') or 'SSH 同步失败')
    return value


def verify_server(config, fact):
    expected = config.get('server')
    if expected and expected != fact:
        raise ValueError('SSH 别名对应的服务器或工作区身份不同，禁止接续执行')


def server_bindings(manifest):
    result = {}
    for row in manifest['tables'].get('attempts', []):
        record = json.loads(row['body'])
        alias = record.get('config', {}).get('alias')
        if alias and alias not in result:
            fact = remote(registered(alias), 'status', manifest['project']['id'])['server']
            if record.get('remote_workspace_root_abs') and record['remote_workspace_root_abs'] != fact['root']:
                raise ValueError('登记工作区与历史任务实际根目录不符：' + alias)
            result[alias] = fact
    return result


def continuation_connection(saved, current, alias):
    """Read-only adapter: original authorizations retain their origin metadata.

Different private-key file names are normal across devices. Actual server/root
identity must be checked afresh before permitting that difference.
"""
    # Multiplexing is a local connection preference, not a different server.
    def target(value):return {k:v for k,v in value.items() if k not in ('controlpath','controlmaster','controlpersist')}
    saved,current=target(saved),target(current)
    if saved == current:return True
    for path in (ex.ROOT / 'project-sync').glob('*.json'):
        value = json.loads(path.read_bytes())
        if value.get('transfer_mode')=='snapshot' and value.get('imported'):
            known=value.get('calculation_connections',{}).get(alias,[])
            if any(target(v)==saved for v in known) and {k:v for k,v in saved.items() if k!='identityfile'}=={k:v for k,v in current.items() if k!='identityfile'}:return True
        binding = value.get('servers', {}).get(alias)
        if value.get('imported') and binding:
            fact = remote(registered(alias), 'status', 'connection-verification')['server']
            verify_server({'server': binding}, fact)
            return True
    return False


@contextmanager
def project_lock(pid):
    with locked({'project-sync:' + pid}):
        yield


@contextmanager
def dispatch_guard(record):
    """Hold the distributed gate through preflight, staging and qsub/sbatch."""
    pid = record.get('project_id')
    if not pid:
        yield;return
    with project_lock(pid):
        value = local(pid)
        if not value:
            yield;return  # Projects never opted into sync retain existing behavior.
        if value.get('transfer_mode')=='snapshot':
            if not locally_executable(pid):raise SyncStopped('快照操作回执待核验，自动流程保持暂停')
            # A worker may have passed its state check before this lock was
            # acquired. Recheck the owning plan here, immediately before dispatch.
            from contextlib import closing
            from .attempt_operations import AttemptBusy
            with closing(ex.db()) as db:
                for field,table in (('chain_run_id','chain_runs'),('scan_batch_id','scan_batches')):
                    if not record.get(field):continue
                    row=db.execute('SELECT body FROM '+table+' WHERE id=?',(record[field],)).fetchone()
                    plan=json.loads(row[0]) if row else None
                    if plan and plan.get('state')=='PAUSED' and plan.get('snapshot_pause_reason'):
                        raise AttemptBusy('该自动流程已因快照传输暂停，请通过原入口手动继续')
            yield;return
        if value.get('mode') != 'ACTIVE':
            raise SyncStopped('项目已交接或回执待核验；本机只能查看和刷新')
        token = uuid.uuid4().hex
        try:
            fact = remote(value, 'status', pid)
            verify_server(value, fact['server'])
            state = fact['state']
            if state['owner'] != device()['id'] or state['version'] != value.get('version'):
                value['mode'] = 'OBSERVER';write_json(local_path(pid), value)
                raise SyncStopped('项目执行权已交接给其他设备')
            alias = (record.get('config') or record.get('runtime', {}).get('config') or {}).get('alias')
            binding = value.get('servers', {}).get(alias)
            if binding:
                target = remote(registered(alias), 'status', pid)['server']
                verify_server({'server': binding}, target)
            value.update(mode='DISPATCHING', operation=token, purpose='dispatch')
            write_json(local_path(pid), value)
            remote(value, 'acquire', pid, generation=state['generation'], version=state['version'],
                   device=device()['id'], token=token, purpose='dispatch')
        except Exception as exc:
            value['mode'] = 'OBSERVER' if value.get('mode') == 'OBSERVER' else 'STOPPED'
            value['error'] = str(exc);write_json(local_path(pid), value)
            raise SyncStopped(str(exc)) from exc
        try:
            yield
        finally:
            try:
                remote(value, 'release', pid, token=token, device=device()['id'])
                value.update(mode='ACTIVE', operation=None, error=None)
            except Exception as exc:
                value.update(mode='STOPPED', error=str(exc))
            write_json(local_path(pid), value)


def hosts():
    return {'device': device(), 'hosts': [{'alias': alias, 'root': host.get('remote_workspace_root', '$HOME')}
                                         for alias, host in servers.load_registry()['hosts'].items()]}


def locally_executable(pid):
    if not pid:return True
    value = local(pid)
    return value is None or (value.get('mode') in ('ACTIVE','SNAPSHOT') and not value.get('restore_required'))


def assert_draft_edits(root, workspace):
    current = WorkspaceStore(root).read()['workspace']
    old = {p['id']: p for p in (current or {}).get('projects', [])}
    for project in workspace['projects']:
        path = Path(root) / 'project-sync' / (snapshot.digest(project['id'].encode()) + '.json')
        if path.exists() and project['id'] in old:
            value=json.loads(path.read_bytes());mode=value.get('mode')
            writable=(mode in ('ACTIVE','SNAPSHOT') or (value.get('transfer_mode')=='snapshot' and mode=='STOPPED' and not value.get('restore_started'))) and not value.get('restore_required')
            if not writable and snapshot.packed(project) != snapshot.packed(old[project['id']]):
                raise ValueError('项目已交接或正在核验，草稿只读；请先下载并接管')


@contextmanager
def draft_gate(root, workspace):
    from contextlib import ExitStack
    from .attempt_operations import HELD
    base = Path(root) / 'attempt-operation-locks'
    base.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        for project in sorted(workspace['projects'], key=lambda p: p['id']):
            identity = snapshot.digest(project['id'].encode())
            if not (Path(root) / 'project-sync' / (identity + '.json')).exists():continue
            key = 'project-sync:' + project['id']
            if key in HELD.get():continue
            if not stack.enter_context(exclusive_lock(base / (snapshot.digest(key.encode()) + '.lock'))):
                raise SyncStopped('项目正在交接，请待核验结束后保存')
        yield


def catalog(alias, snapshot_directory=None):
    from .snapshot_sync import location
    return remote(location(alias,snapshot_directory) if snapshot_directory is not None else registered(alias), 'catalog')


def versions(pid, alias, snapshot_directory=None):
    from .snapshot_sync import location
    return remote(location(alias,snapshot_directory) if snapshot_directory is not None else registered(alias), 'versions', pid)


def status(pid, check=False):
    value = local(pid)
    attempts = ex.history(pid, limit=1)
    result = {'device': device(), 'local': value, 'pending': None,
              'default_alias': attempts[0].get('config', {}).get('alias') if attempts else servers.read_config().get('alias')}
    result['needs_conversion']=bool(value and value.get('transfer_mode')!='snapshot')
    result['draft_read_only']=bool(value and (value.get('restore_required') or (value.get('mode') not in ('ACTIVE','SNAPSHOT') and not (value.get('transfer_mode')=='snapshot' and value.get('mode')=='STOPPED' and not value.get('restore_started')))))
    preference=ex.ROOT/'project-sync/locations'/(snapshot.digest(pid.encode())+'.json')
    if preference.exists():result['location']=json.loads(preference.read_bytes())
    if value:
        try:
            if value.get('transfer_mode')=='snapshot':
                from .snapshot_sync import capture
                manifest,_=capture(pid)
            else:manifest, _ = snapshot.capture(ex.ROOT, pid)
            result['pending'] = snapshot.fingerprint(manifest) != value.get('fingerprint')
        except ValueError as exc:result['local_error'] = str(exc)
        if check:
            fact = remote(value, 'status', pid)
            verify_server(value, fact['server'])
            result['remote'] = fact
    return result


def get_object(config, pid, sha):
    raw = bytearray()
    while True:
        result = remote(config, 'get', pid, sha256=sha, offset=len(raw))
        if result['size'] > snapshot.MAX_FILE:
            raise ValueError('服务器附件超过大小上限')
        chunk = base64.b64decode(result['data'], validate=True)
        if not chunk and len(raw) < result['size']:
            raise ValueError('服务器附件读取不完整')
        raw.extend(chunk)
        if len(raw) >= result['size']:break
    if snapshot.digest(raw) != sha:
        raise ValueError('服务器附件身份校验失败')
    return bytes(raw)


def preview(pid, alias, direction, version=None, snapshot_directory=None):
    if snapshot_directory is not None:
        from .snapshot_sync import preview as snapshot_preview
        return snapshot_preview(pid,alias,direction,snapshot_directory,version)
    with project_lock(pid):
        config = registered(alias)
        existing = local(pid)
        if existing and (existing['alias'], existing['root']) != (config['alias'], config['root']):
            raise ValueError('项目同步服务器已绑定，不能隐式更换')
        fact = remote(config, 'status', pid)
        if existing:verify_server(existing, fact['server'])
        config['server'] = fact['server']
        state = fact['state']
        current = WorkspaceStore(ex.ROOT).read()
        local_manifest, objects = None, {}
        if any(p['id'] == pid for p in (current['workspace'] or {}).get('projects', [])):
            local_manifest, objects = snapshot.capture(ex.ROOT, pid)
        if direction == 'upload':
            if not local_manifest:raise ValueError('上传项目不在本机')
            if state['version'] and (not existing or existing.get('version') != state['version']):
                raise ValueError('本机不是服务器最新版本，请先下载并接管')
            if existing and existing['mode'] in ('STOPPED', 'DISPATCHING', 'TRANSFERRING'):
                raise ValueError('上次回执待核验，请先核查同步状态')
            if existing and existing['mode'] == 'OBSERVER':
                raise ValueError('本机已交接，请先下载并接管后再上传')
            manifest = local_manifest
            manifest['servers'] = server_bindings(manifest)
        elif direction == 'download':
            selected = version or state['version']
            if not selected:raise ValueError('服务器尚无项目快照')
            if selected != state['version'] and not any(v['version'] == selected for v in versions(pid, alias)['versions']):
                raise ValueError('已发布历史版本不存在')
            manifest = json.loads(get_object(config, pid, selected))
            if manifest.get('schema') != snapshot.SCHEMA or manifest['project']['id'] != pid:
                raise ValueError('服务器项目快照身份不符')
        else:raise ValueError('请选择上传或下载')
        token = uuid.uuid4().hex
        info = {'id': token, 'project_id': pid, 'config': config, 'direction': direction, 'state': state,
                'version': version or state['version'], 'workspace_revision': current['revision'],
                'local_fingerprint': snapshot.fingerprint(local_manifest) if local_manifest else None,
                'manifest': manifest, 'created_at': now()}
        write_json(ex.ROOT / 'project-sync' / 'previews' / (token + '.json'), info)
        pending = local_manifest is not None and (not existing or snapshot.fingerprint(local_manifest) != existing.get('fingerprint'))
        return {'preview_id': token, 'project_id': pid, 'name': manifest['project']['name'], 'direction': direction,
                'counts': manifest['counts'], 'files': len(manifest['files']), 'bytes': sum(v['size'] for v in manifest['files'].values()),
                'missing': manifest['missing'], 'local_changes': pending, 'local_counts': local_manifest['counts'] if local_manifest else None,
                'version': info['version'], 'remote_owner': state['owner'], 'remote_operation': fact.get('operation'),
                'updated_at': state.get('updated_at'), 'device': device(), 'read_only': direction == 'download' and info['version'] != state['version']}


def save_backup(pid, manifest, objects):
    base = ex.ROOT / 'project-sync' / 'backups' / snapshot.digest(pid.encode()) / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    write_json(base / 'manifest.json', manifest)
    (base / 'objects').mkdir()
    for sha, raw in objects.items():(base / 'objects' / sha).write_bytes(raw)
    return str(base)


def transfer(pid, preview_id, choose_server=False):
    if not isinstance(preview_id, str) or len(preview_id) != 32 or any(c not in '0123456789abcdef' for c in preview_id):
        raise ValueError('同步预览身份无效')
    path = ex.ROOT / 'project-sync' / 'previews' / (preview_id + '.json')
    info = json.loads(path.read_bytes())
    if info['project_id'] != pid or (datetime.now(timezone.utc) - datetime.fromisoformat(info['created_at'])).total_seconds() > 600:
        raise ValueError('同步预览已过期，请重新预览')
    if info.get('transfer_mode')=='snapshot':
        from .snapshot_sync import transfer as snapshot_transfer
        return snapshot_transfer(pid,path,info,choose_server)
    with project_lock(pid):
        current = WorkspaceStore(ex.ROOT).read()
        if current['revision'] != info['workspace_revision']:
            raise ValueError('草稿已变化，请保存后重新预览')
        config, state, direction = info['config'], info['state'], info['direction']
        old_manifest, old_objects = None, {}
        if any(p['id'] == pid for p in (current['workspace'] or {}).get('projects', [])):
            old_manifest, old_objects = snapshot.capture(ex.ROOT, pid)
        if (snapshot.fingerprint(old_manifest) if old_manifest else None) != info['local_fingerprint']:
            raise ValueError('本机任务或分析记录已变化，请重新预览')
        previous = local(pid)
        dirty = old_manifest is not None and (not previous or snapshot.fingerprint(old_manifest) != previous.get('fingerprint'))
        if direction == 'download' and dirty and not choose_server:
            raise ValueError('本机存在未同步修改，须明确选择服务器版本；会先保留本机完整备份')
        if any(json.loads(r['body'])['state'] == 'DISPATCHING' for r in (old_manifest or {}).get('tables', {}).get('attempts', [])):
            raise ValueError('项目正在派发，请等待该次回执后重试')
        token = uuid.uuid4().hex
        value = {**config, 'mode': 'TRANSFERRING', 'version': state['version'], 'operation': token,
                 'purpose': direction, 'fingerprint': (previous or {}).get('fingerprint'), 'started_at': now()}
        write_json(local_path(pid), value)  # Fail closed before any remote mutation.
        acquired = False
        try:
            fact = remote(config, 'status', pid);verify_server(config, fact['server'])
            if fact['state'] != state:raise ValueError('服务器版本或设备归属已变化，请重新预览')
            remote(config, 'acquire', pid, generation=state['generation'], version=state['version'],
                   device=device()['id'], token=token, purpose=direction if direction == 'download' else 'upload')
            acquired = True
            if old_manifest:value['backup'] = save_backup(pid, old_manifest, old_objects)
            if direction == 'upload':
                manifest, objects = snapshot.capture(ex.ROOT, pid)
                manifest['servers'] = info['manifest'].get('servers', {})
                manifest.update(snapshot_id=uuid.uuid4().hex, snapshot_created_at=now())
                if snapshot.fingerprint(manifest) != info['local_fingerprint']:
                    raise ValueError('快照生成期间记录变化，请重新预览')
                raw = snapshot.packed(manifest);version = snapshot.digest(raw);objects[version] = raw
                value.update(target_version=version, fingerprint=snapshot.fingerprint(manifest));write_json(local_path(pid), value)
                hashes = list(objects)
                missing = remote(config, 'missing', pid, hashes=hashes)['missing']
                for sha in missing:
                    remote(config, 'put', pid, data=objects[sha], sha256=sha, token=token, device=device()['id'])
                # Frozen files and local records must still match the selected preview.
                if snapshot.fingerprint(snapshot.capture(ex.ROOT, pid)[0]) != info['local_fingerprint']:
                    raise ValueError('上传期间本机记录变化，尚未发布；请重新预览')
                result = remote(config, 'publish', pid, version=version, token=token, device=device()['id'], at=now())
                value.update(mode='OBSERVER', version=result['state']['version'])
            else:
                manifest = info['manifest']
                for alias, binding in manifest.get('servers', {}).items():
                    actual = remote(registered(alias), 'status', pid)
                    verify_server({'server': binding}, actual['server'])
                objects = {sha: get_object(config, pid, sha) for sha in {v['sha256'] for v in manifest['files'].values()}}
                snapshot.validate(manifest, objects)
                value.update(target_version=info['version'], imported=False);write_json(local_path(pid), value)
                snapshot.restore(ex.ROOT, manifest, objects, current['revision'])
                value.update(imported=True, servers=manifest.get('servers', {}), fingerprint=snapshot.fingerprint(snapshot.capture(ex.ROOT, pid)[0]));write_json(local_path(pid), value)
                if info['version'] == state['version']:
                    result = remote(config, 'claim', pid, version=info['version'], token=token, device=device()['id'], at=now())
                    value.update(mode='ACTIVE', version=result['state']['version'])
                else:
                    result = {'state': state}
                    value.update(mode='OBSERVER', version=info['version'], historical_restore=True)
            remote(config, 'release', pid, token=token, device=device()['id']);acquired = False
            value.update(operation=None, error=None, synced_at=now());write_json(local_path(pid), value)
            path.unlink()  # One-use preview; uncertain operations are recovered read-only.
            return {'local': value, 'state': result['state'], 'workspace': WorkspaceStore(ex.ROOT).read(), 'backup': value.get('backup')}
        except Exception as exc:
            value.update(mode='STOPPED', error=str(exc));write_json(local_path(pid), value)
            # Never resend release/publish/claim after an uncertain receipt.
            # Recovery first inspects the immutable pointer and our operation token.
            raise ValueError(str(exc) + '；本机已停止派发，请核查同步状态') from exc


def recover(pid):
    if (local(pid) or {}).get('transfer_mode')=='snapshot':
        from .snapshot_sync import recover as snapshot_recover
        return snapshot_recover(pid)
    with project_lock(pid):
        value = local(pid)
        if not value:raise ValueError('项目尚未登记同步')
        fact = remote(value, 'status', pid);verify_server(value, fact['server'])
        operation = fact.get('operation')
        if operation and (operation['token'] != value.get('operation') or operation['device'] != device()['id']):
            return {**status(pid), 'remote': fact, 'message': '其他设备操作尚未完成，仅核查；本机保持停止'}
        workspace = WorkspaceStore(ex.ROOT).read()['workspace']
        manifest = snapshot.capture(ex.ROOT, pid)[0] if any(p['id'] == pid for p in (workspace or {}).get('projects', [])) else None
        if any(json.loads(r['body'])['state'] == 'DISPATCHING' for r in (manifest or {}).get('tables', {}).get('attempts', [])):
            return {**status(pid), 'remote': fact, 'message': '派发回执仍待核查，保持停止'}
        state = fact['state']
        complete = state['version'] == value.get('target_version')
        mode = 'STOPPED'
        if state['version'] is None and value.get('purpose') == 'upload':
            mode = 'RETRY_UPLOAD'
        elif value.get('historical_restore'):
            mode = 'OBSERVER'
        elif complete and value.get('purpose') == 'upload' and state['owner'] is None:
            mode = 'OBSERVER'
        elif state['owner'] == device()['id'] and (value.get('purpose') == 'dispatch' or (complete and value.get('imported'))):
            mode = 'ACTIVE'
        elif state['owner'] != device()['id']:
            mode = 'OBSERVER'
        if operation:
            # Only release our completed/aborted operation after the read-only check;
            # never retry a publish, qsub or ownership mutation.
            remote(value, 'release', pid, token=operation['token'], device=device()['id'])
        value.update(mode=mode, version=state['version'], operation=None, error=None)
        write_json(local_path(pid), value)
        return {**status(pid), 'remote': fact, 'message': '已核查；未重新发送上传、接管或提交命令'}


def refresh(pid):
    from . import artifacts
    results = []
    # Exact known IDs only, never enumerate or adopt unknown remote directories.
    manifest, _ = snapshot.capture(ex.ROOT, pid)
    for row in manifest['tables'].get('attempts', []):
        record = json.loads(row['body'])
        try:
            config = {'alias':record['config']['alias'],'root':record.get('remote_workspace_root_abs') or record.get('remote_workspace_root') or registered(record['config']['alias'])['root']}
            fact = remote(config, 'status', pid)
            binding = (local(pid) or {}).get('servers', {}).get(config['alias']) if (local(pid) or {}).get('transfer_mode')!='snapshot' else None
            if binding:verify_server({'server': binding}, fact['server'])
            elif record.get('remote_workspace_root_abs') and record['remote_workspace_root_abs'] != fact['server']['root']:
                raise ValueError('登记目录与原任务根目录不符')
            identity = remote(config, 'attempt', pid, relative=ex.attempt_path(record)['relative'], attempt_id=record['id'], job_id=record.get('job_id'))
            if not identity['available']:
                results.append({'id': record['id'], 'state': record['state'], 'error': identity['reason']});continue
            value = ex.refresh(record['id'])
            listing = artifacts.refresh(record['id'])
            results.append({'id': record['id'], 'state': value['state'], 'files': len(listing.get('files', []))})
        except Exception as exc:
            results.append({'id': record['id'], 'state': record['state'], 'error': str(exc)})
    return {'records': results, 'status': status(pid)}
