"""Previewed, single-use cancellation with read-only recovery and preserved history."""
import copy
import json
import re
import shlex
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import execution as ex, execution_control as control, recycle, scans, chain_runs
from .attempt_operations import locked

PENDING = {'IN_FLIGHT', 'ACCEPTED', 'UNKNOWN'}
TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED'}


def target(project_id, key):
    if not re.fullmatch(r'[a-f0-9]{32}', key): raise ValueError('任务身份无效')
    record = ex.get(key)
    if record.get('project_id') != project_id: raise ValueError('任务不属于当前项目')
    recycle.assert_available('attempts', key, record)
    ex.assert_recovery_target(record)
    if record.get('path_schema') != 'fpilot.remote-attempt-path': raise ValueError('任务目录身份不受支持')
    from .remote_paths import absolute_attempt_path
    expected = absolute_attempt_path(record['remote_workspace_root_abs'], ex.attempt_path(record)['relative'])
    if record.get('remote_path_absolute') != expected: raise ValueError('远端目录与保存的身份不符')
    return record


def associations(key):
    result = []
    for table, kind, module, field in [('scan_batches', 'scans', scans, 'cases'), ('chain_runs', 'chains', chain_runs, 'steps')]:
        for row in recycle._rows(table):
            if any(item.get('attempt_id') == key for item in row.get(field, [])):
                result.append({'kind': kind, 'id': row['id'], 'state': row['state'],
                               'label': row.get('reason') or ('迭代批次' if kind == 'scans' else '计算链')})
    return sorted(result, key=lambda r: (r['kind'], r['id']))


@contextmanager
def serialized(key, controlling=True):
    # Same order as workers: container lock -> device guard -> attempt lock.
    with ExitStack() as stack:
        refs = associations(key)
        for ref in refs:
            module = scans if ref['kind'] == 'scans' else chain_runs
            if not stack.enter_context(module._dispatch_lock(ref['id'])):
                raise ValueError('关联计划正在派发；请待本次提交结束后重试取消')
        from .project_sync import dispatch_guard
        if controlling: stack.enter_context(dispatch_guard(ex.get(key)))
        stack.enter_context(locked({key}))
        if [(r['kind'], r['id']) for r in refs] != [(r['kind'], r['id']) for r in associations(key)]:
            raise ValueError('关联计划已变化，请重新预览')
        yield refs


def remote(record, action):
    from .remote_transport import _interpreter
    source = Path(__file__).with_name('execution_cancel_remote.py').read_text()
    payload = {'attempt_id': record['id'], 'root': record['remote_path_absolute'],
               'job_id': record.get('job_id'), 'scheduler': ex.scheduler_name(record), 'action': action}
    command = shlex.quote(_interpreter(record['config']['alias'])) + ' -c ' + shlex.quote(source) + ' ' + shlex.quote(json.dumps(payload))
    # The helper text contains scheduler commands even during inspect. Declare
    # the actual role explicitly: transport must never retry/cache cancellation.
    receipt = ex.ssh(record['config']['alias'], command, timeout=120, stdout_limit=16000, mutating=action == 'cancel')
    match = re.search(r'^FPILOT_CANCEL=(.*)$', receipt.get('stdout', ''), re.M)
    if receipt.get('transport_state') == 'NOT_SENT' and action == 'cancel':
        return {'result': 'NOT_SENT', 'error': control.redact(receipt.get('stderr'))}
    if receipt.get('exit_code') != 0 or not match:
        raise ValueError('远端回执不完整；请只读核查，不能重复发送取消命令。' + control.redact(receipt.get('stderr'))[-300:])
    result = json.loads(match[1])
    if result['result'] == 'BLOCKED': raise ValueError(result['error'])
    return result


def binding(record):
    return {k: record.get(k) for k in ('id', 'project_id', 'job_id', 'remote_path_absolute', 'dispatch_started_at')} | {'alias': record['config']['alias'], 'scheduler': ex.scheduler_name(record)}


def preview(project_id, key):
    with serialized(key, controlling=False) as refs:
        record = target(project_id, key)
        previous = record.get('cancellation', {})
        if previous.get('state') in PENDING: return recover(project_id, key)
        if record['state'] in TERMINAL: raise ValueError('任务已结束，无需取消')
        local = record['state'] == 'PREPARED' and not record.get('job_id') and (
            not record.get('dispatch_started_at') or record.get('not_dispatched') is True)
        if not local and not record.get('job_id'): raise ValueError('尚未取得准确的 job ID；请先核查原提交，不会猜测或重投')
        facts = {'result': 'LOCAL', 'terminal': False} if local else remote(record, 'inspect')
        if facts['terminal']: raise ValueError('调度器显示任务已结束，请先刷新原任务状态')
        operation = {'operation_id': uuid.uuid4().hex, 'token': uuid.uuid4().hex,
                     'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                     'state': 'PREVIEW', 'binding': binding(record), 'local': local, 'facts': facts, 'references': refs}
        record['cancellation'] = operation; record['updated_at'] = ex.now(); ex.save(record)
        return response(record)


def response(record):
    operation = copy.deepcopy(record.get('cancellation', {}))
    operation.update(attempt_id=record['id'], attempt_state=record['state'], job_id=record.get('job_id'),
                     alias=record['config']['alias'], remote_path=record.get('remote_path_absolute'))
    return operation


def journal(record, event):
    operation = record.get('cancellation', {})
    with ex.db() as db:
        control.journal(db, record['id'], event, {k: operation.get(k) for k in
                        ('operation_id', 'state', 'requested_at', 'accepted_at', 'confirmed_at', 'error')})


def pause_references(refs, key):
    for ref in refs:
        module = scans if ref['kind'] == 'scans' else chain_runs
        row = module.get(ref['id'])
        if row['state'] in ('ACTIVE', 'BLOCKED'):
            row.update(state='PAUSED', cancellation_pause={'attempt_id': key, 'at': ex.now()})
            module.save(row)


def cancel(project_id, key, token, confirm=False):
    if confirm is not True: raise ValueError('需要明确确认取消此任务')
    with serialized(key) as refs:
        record = target(project_id, key); operation = record.get('cancellation', {})
        if not token or token != operation.get('token'): raise ValueError('取消确认与预览不匹配')
        if operation.get('state') != 'PREVIEW': return response(record)  # single-use
        if datetime.fromisoformat(operation['expires_at']) <= datetime.now(timezone.utc): raise ValueError('取消预览已过期，请重新预览')
        if operation['binding'] != binding(record) or operation['references'] != refs: raise ValueError('任务身份或关联计划已变化，请重新预览')
        if record['state'] in TERMINAL: return response(record)
        if operation['local'] and record['state'] != 'PREPARED': raise ValueError('任务已开始提交，请重新核查')
        pause_references(refs, key)
        operation.update(state='IN_FLIGHT', requested_at=ex.now())
        record['updated_at'] = ex.now(); ex.save(record)  # persist BEFORE sending
        journal(record, 'CANCEL_REQUESTED')
        if operation['local']:
            record['state'] = 'CANCELLED'; operation.update(state='CONFIRMED', confirmed_at=ex.now(), result='LOCAL_CANCELLED')
        else:
            try:
                facts = remote(record, 'cancel'); operation['facts'] = facts
                operation['state'] = {'ACCEPTED': 'ACCEPTED', 'REJECTED': 'REJECTED', 'TERMINAL': 'FINISHED', 'NOT_SENT': 'NOT_SENT'}.get(facts['result'], 'UNKNOWN')
                if facts.get('error'): operation['error'] = facts['error']
                if facts['result'] == 'ACCEPTED': operation['accepted_at'] = ex.now()
            except Exception as exc:
                operation.update(state='UNKNOWN', error=control.redact(str(exc)))
        record['updated_at'] = ex.now(); record = ex.save(record)
        journal(record, 'CANCEL_RECEIPT')
        return response(record)


def recover(project_id, key):
    # Read-only remote recovery. Never invokes qdel/scancel.
    record = target(project_id, key); operation = record.get('cancellation', {})
    if operation.get('state') not in PENDING: return response(record)
    observed_at = record.get('updated_at')
    if record['state'] in TERMINAL:
        operation.update(state='CONFIRMED' if record['state'] == 'CANCELLED' else 'FINISHED', checked_at=ex.now())
        record['updated_at'] = ex.now()
        return response(ex.save(record, expected_updated_at=observed_at))
    try:
        facts = remote(record, 'inspect'); operation.update(facts=facts, checked_at=ex.now())
        if facts['terminal']:
            state = facts['scheduler_state']
            pbs_cancelled = ex.scheduler_name(record) == 'pbs' and bool(operation.get('accepted_at')) and facts.get('exit_code') not in (None, 0)
            if state == 'CANCELLED' or pbs_cancelled:
                record['state'] = 'CANCELLED'; operation.update(state='CONFIRMED', confirmed_at=ex.now())
                record['evidence'] = {**record.get('evidence', {}), 'scheduler_state': state,
                                      'scheduler_exit_code': facts.get('exit_code'), 'cancel_requested': True,
                                      'scientific_validation': 'NOT_PERFORMED'}
            else: operation['state'] = 'FINISHED'  # natural completion/failure stays distinct
        operation.pop('error', None)
    except Exception as exc:
        operation.update(state='UNKNOWN', checked_at=ex.now(), error=control.redact(str(exc)))
    record['updated_at'] = ex.now(); record = ex.save(record, expected_updated_at=observed_at)
    if record.get('cancellation', {}).get('state') == 'FINISHED' and record['state'] not in TERMINAL:
        record = ex.refresh(key)
    return response(record)


def cancel_case(project_id, batch_id, case_id, confirm=False):
    if confirm is not True: raise ValueError('需要明确确认取消此 case')
    with scans._dispatch_lock(batch_id) as acquired:
        if not acquired: raise ValueError('批次正在派发，请稍后重试')
        batch = scans.get(batch_id)
        if batch['project_id'] != project_id: raise ValueError('批次不属于当前项目')
        recycle.assert_available('scans', batch_id, batch)
        from .project_sync import locally_executable
        if not locally_executable(project_id): raise ValueError('当前设备未接管此项目')
        case = next((c for c in batch['cases'] if c['id'] == case_id), None)
        if not case or case.get('attempt_id') or case['state'] not in ('PENDING', 'CANCELLED'):
            raise ValueError('case 已登记为任务，请核查该任务并取消')
        if case['state'] == 'CANCELLED': return batch
        if not batch.get('authorization'): raise ValueError('批次尚未授权；请修改草稿重新准备')
        if batch['state'] in ('ACTIVE', 'BLOCKED'): batch['state'] = 'PAUSED'
        case.update(state='CANCELLED', cancelled_at=ex.now())
        return scans.save(batch)
