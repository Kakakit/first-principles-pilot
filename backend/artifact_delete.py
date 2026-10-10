"""Confirmed single-file removal; attempts and frozen scientific records survive."""
import json
import os
import re
import shlex
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from . import artifacts, execution as ex, recycle, remote_paths
from .attempt_operations import locked
from .storage import ROOT

ACTIVE = {'SUBMITTED', 'QUEUED', 'RUNNING', 'HELD', 'DISPATCHING', 'EXITING'}


def relative(value):
    if (not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value
            or '\n' in value or value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('文件必须是当前 attempt 下明确的相对路径')
    return value


def target(project_id, key):
    if not re.fullmatch(r'[a-f0-9]{32}', key):
        raise ValueError('attempt ID 无效')
    record = ex.get(key)
    if record.get('project_id') != project_id:
        raise ValueError('attempt 不属于当前项目')
    if record.get('path_schema') != 'fpilot.remote-attempt-path':
        raise ValueError('远端目录布局不受支持')
    root = artifacts._root(record)
    if root != remote_paths.absolute_attempt_path(record['remote_workspace_root_abs'], ex.attempt_path(record)['relative']):
        raise ValueError('冻结目录与本机记录不符')
    return record


def ensure_stoppable(record):
    if record.get('state') in ACTIVE or record.get('state') not in ('COMPLETED', 'FAILED', 'UNKNOWN'):
        raise ValueError('任务仍在途或尚未形成可核验终态；禁止删除文件')


def _bound_files(value, key, root):
    """Exact v2 roles, v1 snapshots, and conservative legacy references."""
    found, potential = set(), False
    if isinstance(value, dict):
        bound = any(value.get(k) == key for k in ('attempt_id', 'source_attempt_id', 'upstream_attempt_id', 'parent_attempt_id'))
        bound = bound or value.get('source_path') == root
        if bound:
            names = set()
            if isinstance(value.get('file'), str):
                names.add(value['file'])
            if isinstance(value.get('required_files'), list):
                names.update(n for n in value['required_files'] if isinstance(n, str))
            if isinstance(value.get('files'), dict):
                names.update(value['files'])
            elif isinstance(value.get('files'), list):
                names.update(n for n in value['files'] if isinstance(n, str))
            # source_file_plan is keyed by origin filename.
            for name, info in value.items():
                if isinstance(info, dict) and info.get('source_path') == root:
                    names.add(name)
            found.update(names)
            potential = not names
        for item in value.values():
            names, uncertain = _bound_files(item, key, root)
            found.update(names); potential = potential or uncertain
    elif isinstance(value, list):
        for item in value:
            names, uncertain = _bound_files(item, key, root)
            found.update(names); potential = potential or uncertain
    return found, potential


def references(record, path):
    refs = []
    def add(kind, key, body, state=None):
        names, potential = _bound_files(body, record['id'], artifacts._root(record))
        # If explicit file contracts exist, don't overstate legacy parent-only links.
        if path in names or (potential and not names):
            refs.append({'kind': kind, 'id': key, 'state': state,
                         'relation': '历史引用' if state in ('COMPLETED', 'FAILED') else '待执行或冻结来源',
                         'certainty': '已知文件引用' if path in names else '旧记录潜在影响'})
    for table in ('attempts', 'chain_runs', 'scan_batches'):
        for item in recycle._rows(table):
            if table == 'attempts' and item.get('id') == record['id']:
                continue
            add(table, item['id'], item, item.get('state'))
    for file in (ROOT / 'inputs').glob('*.zip'):
        with zipfile.ZipFile(file) as archive:
            add('冻结输入', file.stem, json.loads(archive.read('manifest.json')))
    for base, pattern in (('wannier-bundles', '*.json'), ('analyses-v2', '*/analysis.json'),
                          ('analyses', '*/analysis.json'), ('plot-datasets', '*/record.json'),
                          ('wannier-comparisons', '*/record.json')):
        for file in (ROOT / base).glob(pattern):
            add(base, file.stem if base == 'wannier-bundles' else file.parent.name, json.loads(file.read_text()))
    return sorted(refs, key=lambda item: (item['kind'], item['id']))


def _path(operation_id):
    if not re.fullmatch(r'[a-f0-9]{32}', operation_id):
        raise ValueError('删除操作 ID 无效')
    return ROOT / 'artifact-deletions' / (operation_id + '.json')


def _save(operation):
    path = _path(operation['operation_id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        json.dump(operation, handle, ensure_ascii=False); handle.flush(); os.fsync(handle.fileno())
        temporary = handle.name
    os.replace(temporary, path)
    return operation


def _load(project_id, key, operation_id):
    try:
        operation = json.loads(_path(operation_id).read_text())
    except FileNotFoundError:
        raise ValueError('删除预览不存在，请重新预览')
    if (operation['project_id'], operation['attempt_id']) != (project_id, key):
        raise ValueError('删除预览与所选项目或 attempt 不符')
    return operation


def _remote(record, path, action, operation=None):
    from .remote_transport import _interpreter
    payload = {'root': artifacts._root(record), 'path': path, 'action': action,
               'attempt_id': record['id'], 'job_id': record.get('job_id'),
               'scheduler': ex.scheduler_name(record),
               'terminal_evidence': (record.get('evidence') or {}).get('scheduler_state')}
    if operation:
        payload.update(file_identity=operation['file_identity'], root_identity=operation['root_identity'])
    source = Path(__file__).with_name('artifact_delete_remote.py').read_text()
    command = shlex.quote(_interpreter(record['config']['alias'])) + ' -c ' + shlex.quote(source) + ' ' + shlex.quote(json.dumps(payload))
    receipt = ex.ssh(record['config']['alias'], command, timeout=120, stdout_limit=16000)
    match = re.search(r'^FPILOT_ARTIFACT_DELETE=(.*)$', receipt.get('stdout', ''), re.M)
    if receipt.get('exit_code') != 0 or not match:
        raise ValueError('远端回执不完整或连接中断，请只读核查删除结果')
    result = json.loads(match[1])
    if result['result'] == 'BLOCKED':
        raise ValueError(result.get('error', '远端核查受阻'))
    return result


def preview(project_id, key, path):
    path = relative(path)
    with locked({key}):
        record = target(project_id, key)
        ensure_stoppable(record)
        # Recover an uncertain operation first; never create a fresh destructive retry.
        for file in (ROOT / 'artifact-deletions').glob('*.json'):
            previous = json.loads(file.read_text())
            if (previous.get('attempt_id'), previous.get('path')) == (key, path) and previous.get('result') in ('IN_FLIGHT', 'UNKNOWN'):
                recovered = status(project_id, key, previous['operation_id'])
                if recovered['result'] != 'NOT_DELETED':
                    return recovered
        facts = _remote(record, path, 'preview')
        token = uuid.uuid4().hex
        stamp = datetime.now(timezone.utc)
        return _save({**facts, 'operation_id': token, 'token': token, 'project_id': project_id,
                      'attempt_id': key, 'alias': record['config']['alias'], 'root': artifacts._root(record),
                      'source_identity': artifacts._identity(record), 'path': path, 'name': PurePosixPath(path).name,
                      'remote_path': artifacts._root(record) + '/' + path, 'state': record['state'],
                      'job_id': record.get('job_id'), 'references': references(record, path),
                      'created_at': stamp.isoformat(), 'expires_at': (stamp + timedelta(minutes=10)).isoformat()})


def invalidate(key):
    (artifacts.ROOT / 'artifact-cache' / (key + '.json')).unlink(missing_ok=True)
    for file in (artifacts.ROOT / 'artifact-cache' / 'previews').glob('*.json'):
        if json.loads(file.read_text()).get('attempt_id') == key:
            file.unlink()
    for file in (artifacts.ROOT / 'artifact-cache' / 'input-owners').glob('*.json'):
        if json.loads(file.read_text()).get('attempt_id') == key:
            (artifacts.ROOT / 'artifact-cache' / 'inputs' / file.stem).unlink(missing_ok=True)
            file.unlink()
    # Only invalidate caches, never frozen inputs, result datasets or evidence archives.
    record = ex.get(key)
    for file in (ROOT / 'remote-cache').glob('*/*.json'):
        identity = json.dumps(json.loads(file.read_text()).get('identity', {}))
        if key in identity or artifacts._root(record) in identity:
            file.unlink()


def _deleted(operation):
    operation.update(result='DELETED', checked_at=ex.now())
    if operation.get('note'):
        operation['recovery_note'] = operation.pop('note')
    _save(operation)  # Success survives a later cache/refresh failure.
    try:
        invalidate(operation['attempt_id'])
        operation.pop('cache_error', None)
    except (OSError, ValueError) as exc:
        operation['cache_error'] = str(exc)
    return _save(operation)


def remove(project_id, key, path, token, confirmed=False):
    path = relative(path)
    if confirmed is not True:
        raise ValueError('请明确确认永久删除此文件')
    with locked({key}):
        operation = _load(project_id, key, token)
        if operation['path'] != path:
            raise ValueError('确认令牌与所选文件不符')
        if operation['result'] != 'PREVIEWED':
            return operation  # At most once: repeated POST never unlinks again.
        if datetime.fromisoformat(operation['expires_at']) < datetime.now(timezone.utc):
            raise ValueError('删除预览已过期，请重新预览')
        record = target(project_id, key)
        ensure_stoppable(record)
        if (artifacts._identity(record) != operation['source_identity'] or record['state'] != operation['state']
                or references(record, path) != operation['references']):
            raise ValueError('任务身份、状态或下游引用发生变化，请重新预览')
        operation.update(result='IN_FLIGHT', started_at=ex.now())
        _save(operation)
        try:
            result = _remote(record, path, 'delete', operation)
        except Exception as exc:
            operation.update(result='UNKNOWN', note=str(exc), checked_at=ex.now())
            return _save(operation)
        operation.update(result)
        return _deleted(operation) if result['result'] == 'DELETED' else _save(operation)


def status(project_id, key, operation_id):
    with locked({key}):
        operation = _load(project_id, key, operation_id)
        if operation['result'] in ('PREVIEWED', 'DELETED', 'NOT_DELETED', 'CONFLICT'):
            return _deleted(operation) if operation['result'] == 'DELETED' else operation
        record = target(project_id, key)
        if artifacts._identity(record) != operation['source_identity']:
            operation.update(result='CONFLICT', note='本机来源身份已变化')
        else:
            try:
                operation.update(_remote(record, operation['path'], 'status', operation), checked_at=ex.now())
            except Exception as exc:
                operation.update(result='UNKNOWN', note=str(exc), checked_at=ex.now())
        return _deleted(operation) if operation['result'] == 'DELETED' else _save(operation)
