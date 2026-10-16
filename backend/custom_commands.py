"""Opaque command presets share ordinary attempt files and input preparation."""
import base64
import json
from pathlib import Path
import shlex
import uuid
from . import command_process, job_templates, servers


def preview(config):
    key = servers.selected_template(config)
    if not key: raise ValueError('请选择已保存的自定义命令')
    template = job_templates.render_template(key, {}, config['alias'], config)
    return {'script': template['rendered_text'], 'profile': {'name': config.get('name') or 'command',
            'queue': '', 'node': 'command', 'ppn': None, 'walltime': config.get('walltime', '')},
            'resource_ready': template['submission_supported'], 'blockers': template['execution_blockers'],
            'probe_id': 'custom', 'note': '原样命令；不解析队列状态或核验资源。'}


def invocation(alias, action, root, key, *arguments):
    from .remote_transport import _interpreter
    source = Path(command_process.__file__).read_text(encoding='utf-8')
    code = 'SOURCE=' + repr(source) + '\nexec(SOURCE)'
    return ' '.join(map(shlex.quote, [_interpreter(alias), '-c', code, action, root, key, *map(str, arguments)]))


def start_command(record, key, text):
    encoded = base64.b64encode(job_templates.execution_text(text).encode()).decode('ascii')
    return invocation(record['config']['alias'], 'start', record['remote_path_absolute'], key, encoded)


def dispatch_result(record, receipt):
    from . import execution as ex
    record.update(job_id=None, command_run_id=record['id'], dispatch_receipt=receipt,
                  state='COMMAND_RUNNING' if receipt.get('exit_code') == 0 else 'UNKNOWN',
                  note='命令已启动；退出码只代表命令本身，不代表队列计算完成。', updated_at=ex.now())
    record['command_runs'] = [{'id': record['id'], 'name': '提交命令', 'command': record['script'], 'created_at': ex.now()}]
    return ex.save(record)


def read(key, run_id=None, stdout_offset=0, stderr_offset=0):
    from . import execution as ex
    record = ex.get(key)
    ex.assert_recovery_target(record)
    run_id = run_id or record.get('command_run_id') or key
    if run_id != key and not any(r['id'] == run_id for r in record.get('command_runs', [])):
        raise ValueError('命令记录不属于该任务')
    for offset in (stdout_offset, stderr_offset):
        if not isinstance(offset, int) or offset < 0: raise ValueError('日志位置无效')
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
    read(key)
    return ex.get(key)


def run_preset(key, template_id):
    from . import execution as ex
    from .attempt_operations import locked
    from .site_registration import host_operation
    record = ex.get(key)
    if record['config'].get('scheduler') != 'custom': raise ValueError('仅用于自定义命令任务')
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
