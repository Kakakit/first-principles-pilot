"""Execution capabilities for the existing scheduler values."""


def command_mode(config):
    if not config or str(config.get('scheduler', '')).lower() not in ('custom', 'other'):
        return None
    return config.get('command_mode') or 'opaque'


def is_direct(config):
    return command_mode(config) == 'direct'


def has_execution_receipt(record):
    if not is_direct(record.get('config', {})):
        return bool(record.get('job_id'))
    runtime = record.get('direct_runtime') or {}
    return (bool(record.get('id')) and record.get('command_run_id') == record['id']
            and runtime.get('protocol_version') == 1 and bool(runtime.get('process_pid')))


def validate_mode(config):
    mode = command_mode(config)
    if mode is not None and 'command_mode' in config and config['command_mode'] not in (None, 'opaque', 'direct'):
        raise ValueError('自定义命令的运行方式无效')
    return mode


def direct_limit(value):
    if type(value) is not int or not 1 <= value <= 1000:
        raise ValueError('每台服务器的直接任务上限须为 1 到 1000 的整数')
    return value


def require_batch_scheduler(config):
    if command_mode(config):
        raise ValueError('此服务器的执行方式仅支持单任务，计算链和参数迭代暂不支持自动派发')
