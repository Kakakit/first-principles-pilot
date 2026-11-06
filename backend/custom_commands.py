"""Command presets share ordinary attempt files and input preparation."""
import base64
import json
from pathlib import Path
import re
import shlex
import uuid
from . import command_process, job_templates, servers
from .execution_modes import is_direct


def preview(config):
    key = servers.selected_template(config)
    if not key: raise ValueError('请选择已保存的自定义命令')
    template = job_templates.render_template(key, {}, config['alias'], config)
    return {'script': template['rendered_text'], 'profile': {'name': config.get('name') or 'command',
            'queue': '', 'node': 'command', 'ppn': None, 'walltime': config.get('walltime', '')},
            'resource_ready': template['submission_supported'], 'blockers': template['execution_blockers'],
            'probe_id': 'custom', 'note': '在冻结的任务目录后台运行并跟踪进程。' if is_direct(config)
            else '原样命令；不解析队列状态或核验资源。'}


def invocation(alias, action, root, key, *arguments):
    from .remote_transport import _interpreter
    source = Path(command_process.__file__).read_text(encoding='utf-8')
    code = 'SOURCE=' + repr(source) + '\nexec(SOURCE)'
    return ' '.join(map(shlex.quote, [_interpreter(alias), '-c', code, action, root, key, *map(str, arguments)]))


def start_command(record, key, text):
    direct = is_direct(record['config'])
    encoded = base64.b64encode((text if direct else job_templates.execution_text(text)).encode()).decode('ascii')
    return invocation(record['config']['alias'], 'start-direct' if direct else 'start', record['remote_path_absolute'], key, encoded)


def dispatch_result(record, receipt):
    from . import execution as ex
    if is_direct(record['config']):
        match = re.search(r'^FPILOT_DIRECT_START=(.+)$', receipt.get('stdout', ''), re.M)
        observed = parse_receipt(match[1], record['id']) if match and receipt.get('exit_code') == 0 else None
        record.update(dispatch_receipt=receipt, command_run_id=record['id'], job_id=None)
        return apply_direct(record, observed or {'state': 'UNKNOWN', 'note': '启动回执未确认，请核查原任务'})
    record.update(job_id=None, command_run_id=record['id'], dispatch_receipt=receipt,
                  state='COMMAND_RUNNING' if receipt.get('exit_code') == 0 else 'UNKNOWN',
                  note='命令已启动；退出码只代表命令本身，不代表队列计算完成。', updated_at=ex.now())
    record['command_runs'] = [{'id': record['id'], 'name': '提交命令', 'command': record['script'], 'created_at': ex.now()}]
    return ex.save(record)


def read(key, run_id=None, stdout_offset=0, stderr_offset=0, stdout_token='', stderr_token='', tail=False):
    from . import execution as ex
    record = ex.get(key)
    ex.assert_recovery_target(record)
    run_id = run_id or record.get('command_run_id') or key
    if run_id != key and not any(r['id'] == run_id for r in record.get('command_runs', [])):
        raise ValueError('命令记录不属于该任务')
    for offset in (stdout_offset, stderr_offset):
        if not isinstance(offset, int) or offset < 0: raise ValueError('日志位置无效')
    if is_direct(record['config']):
        if run_id != key: raise ValueError('直接任务仅有一条主命令记录')
        if record['state'] == 'PREPARED': return {'id': key, 'mode': 'direct', 'state': 'PREPARED'}
        return direct_request(record, 'logs-direct', stdout_offset, stderr_offset,
                              stdout_token, stderr_token, int(tail), stdout_limit=800000)
    response = ex.ssh(record['config']['alias'], invocation(record['config']['alias'], 'inspect',
        record['remote_path_absolute'], run_id, stdout_offset, stderr_offset), timeout=30, stdout_limit=800000)
    if response.get('exit_code') != 0:
        return {'id': run_id, 'state': 'UNKNOWN', 'stdout': '', 'stderr': response.get('stderr', ''),
                'note': '读取回执失败，未重新执行', 'runs': record.get('command_runs', [])}
    result = json.loads(response['stdout'])
    for stream in ('stdout', 'stderr'):
        result[stream] = base64.b64decode(result.get(stream, '')).decode('utf-8', errors='replace')
    result['runs'] = record.get('command_runs', [])
    if run_id == key and result.get('state') in ('RUNNING', 'STARTING', 'EXITED'):
        record.update(state='COMMAND_EXITED' if result['state'] == 'EXITED' else 'COMMAND_RUNNING',
                      command_status=result['state'], command_exit_code=result.get('exit_code'), updated_at=ex.now(),
                      note='命令已结束；这不表示队列中的计算已完成。' if result['state'] == 'EXITED' else '命令运行中')
        ex.save(record)
    return result


def refresh(key):
    from . import execution as ex
    record = ex.get(key)
    if is_direct(record['config']):
        from .attempt_operations import locked, AttemptBusy
        try:
            with locked({key}):
                record = ex.get(key)
                if record['state'] in ('PREPARED', 'CANCELLED'):
                    return record
                if record['state'] in ('COMPLETED', 'FAILED'):
                    evidence = record.get('evidence', {})
                    if evidence.get('file_evidence_checked'):
                        return record
                    runtime = record.get('direct_runtime', {})
                    if record['state'] == 'FAILED' and (
                        runtime.get('termination_reason') or evidence.get('termination_reason')
                        or type(runtime.get('exit_code', evidence.get('wrapper_exit'))) is not int
                    ):
                        return record
                    ex.assert_recovery_target(record)
                    files = direct_file_evidence(record)
                    if not files:
                        return record
                    previous = record.get('updated_at')
                    record.update(evidence={**evidence, **files}, updated_at=ex.now())
                    return ex.save(record, expected_updated_at=previous)
                ex.assert_recovery_target(record)
                return apply_direct(record, direct_request(record, 'status-direct'))
        except AttemptBusy:
            return ex.get(key)
    read(key)
    return ex.get(key)


def parse_receipt(raw, key):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) and value.get('id') == key and isinstance(value.get('state'), str) else None


def direct_request(record, action, *args, stdout_limit=16000):
    from . import execution as ex, execution_control as control
    key = record['id']
    alias = record['config']['alias']
    receipt = ex.ssh(alias, invocation(alias, action, record['remote_path_absolute'], key, *args),
                     timeout=30, stdout_limit=stdout_limit, mutating=action == 'stop-direct')
    result = parse_receipt(receipt.get('stdout'), key) if receipt.get('exit_code') == 0 else None
    if result is None:
        result = {'id': key, 'state': 'UNKNOWN', 'transport_error': True,
                  'note': control.redact(receipt.get('stderr') or '未收到完整的运行回执')[-1000:]}
        if receipt.get('transport_state') == 'NOT_SENT': result['not_sent'] = True
    return {**result, 'mode': 'direct'}


def direct_state(observed):
    state = observed.get('state')
    if state == 'EXITED' and type(observed.get('exit_code')) is int:
        return 'COMPLETED' if observed['exit_code'] == 0 else 'FAILED'
    return {'STARTING': 'DISPATCHING', 'RUNNING': 'RUNNING', 'EXITING': 'EXITING',
            'CANCELLED': 'CANCELLED', 'LOST': 'FAILED', 'START_FAILED': 'FAILED'}.get(state, 'UNKNOWN')


def direct_file_evidence(record):
    from . import execution as ex
    command = 'cd ' + shlex.quote(record['remote_path_absolute']) + ' || exit 3\n' + ex.vasp_file_evidence_command()
    try:
        receipt = ex.ssh(record['config']['alias'], command, timeout=30, stdout_limit=16000, mutating=False)
    except OSError:
        return {}
    if receipt.get('exit_code') != 0:
        return {}
    files = ex.vasp_file_evidence(receipt['stdout'])
    if files['ediff_source'] is None:
        files['ediff_reached'] = None
    return {**files, 'file_evidence_checked': True}


def apply_direct(record, observed):
    from . import execution as ex
    from .execution_cancel import PENDING
    previous = record.get('updated_at')
    state = direct_state(observed)
    now = ex.now()
    record.update(tracking_checked_at=now, updated_at=now)
    if state == 'UNKNOWN':
        record['tracking_error'] = observed.get('note') or '暂时无法确认直接任务状态'
        if record['state'] == 'DISPATCHING' and not observed.get('transport_error'):
            record.update(state='UNKNOWN', note='启动状态待核查，可刷新原任务')
        cancellation = record.get('cancellation', {})
        if cancellation.get('state') in PENDING - {'RETRYABLE'}:
            cancellation.update(state='UNKNOWN', error=record['tracking_error'], checked_at=now)
        return ex.save(record, expected_updated_at=previous)

    runtime = {k: observed[k] for k in (
        'protocol_version', 'execution_host', 'boot_id', 'supervisor_pid', 'supervisor_start_ticks',
        'process_pid', 'process_start_ticks', 'process_group_id', 'started_at', 'finished_at',
        'exit_code', 'stop') if k in observed}
    runtime['termination_reason'] = {'LOST': 'runtime_lost', 'START_FAILED': 'startup_error',
                                     'CANCELLED': 'stopped'}.get(observed['state'])
    record.update(state=state, job_id=None, command_run_id=record['id'],
                  direct_runtime={**record.get('direct_runtime', {}), **runtime}, state_checked_at=now,
                  tracking_error=observed.get('note') if state in ('RUNNING', 'EXITING') else '',
                  note=observed.get('note') or '')
    cancellation = record.get('cancellation', {})
    stop = observed.get('stop', {})
    if state in ('CANCELLED', 'COMPLETED', 'FAILED') and cancellation.get('state') in PENDING:
        cancellation.update(state='CONFIRMED' if state == 'CANCELLED' else 'FINISHED', checked_at=now)
        if state == 'CANCELLED': cancellation['confirmed_at'] = now
        cancellation.pop('error', None)
    elif (cancellation.get('state') in PENDING and stop.get('operation_id') == cancellation.get('operation_id')
          and stop.get('state') in ('ACCEPTED', 'RETRYABLE', 'UNKNOWN')):
        cancellation.update(state=stop['state'], checked_at=now)
        if stop.get('note'):
            cancellation['error'] = stop['note']
        else:
            cancellation.pop('error', None)

    if state in ('COMPLETED', 'FAILED', 'CANCELLED'):
        evidence = {**record.get('evidence', {}), 'execution_mode': 'direct', 'scientific_validation': 'NOT_PERFORMED',
                    'wrapper_exit': observed.get('exit_code'), 'termination_reason': runtime['termination_reason']}
        if observed['state'] == 'EXITED':
            evidence.update(direct_file_evidence(record))
        record['evidence'] = evidence
    return ex.save(record, expected_updated_at=previous)


def run_preset(key, template_id):
    from . import execution as ex
    from .attempt_operations import locked
    from .site_registration import host_operation
    record = ex.get(key)
    if record['config'].get('scheduler') != 'custom': raise ValueError('仅用于自定义命令任务')
    if is_direct(record['config']): raise ValueError('直接任务仅运行已冻结的主命令')
    if record['state'] in ('PREPARED', 'DISPATCHING'): raise ValueError('请先完成本次输入上传与命令启动')
    preset = job_templates.get_template(template_id)
    if preset['alias'] != record['config']['alias'] or preset['scheduler'] != 'custom':
        raise ValueError('请选择当前服务器的自定义命令')
    command_id = uuid.uuid4().hex
    with locked({key}), host_operation(record['config']['alias']):
        if record['config']['alias'] not in servers.load_registry()['hosts']:
            raise ValueError('该服务器已移除登记；恢复后才能运行新命令')
        record = ex.get(key)
        record.setdefault('command_runs', []).append({'id': command_id, 'name': preset['name'],
            'command': preset['original_text'], 'created_at': ex.now(), 'state': 'DISPATCHING'})
        ex.save(record)
        response = ex.ssh(record['config']['alias'], start_command(record, command_id, preset['original_text']), timeout=30, mutating=True)
        record['command_runs'][-1]['state'] = 'STARTED' if response.get('exit_code') == 0 else 'UNKNOWN'
        ex.save(record)
    return {'id': command_id, 'state': record['command_runs'][-1]['state']}
