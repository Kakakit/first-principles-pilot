"""Small detached shell runner; reports shell execution, never queue state."""
import base64
import codecs
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time


def write(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, ensure_ascii=False)
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def detached(root, key, action, log, *args):
    with log.open('ab') as handle:
        return subprocess.Popen([sys.executable, '-c', runner_code(), action, str(root), key, *args],
                                stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
                                start_new_session=True, close_fds=True)


def start(root, key, encoded):
    root = Path(root).resolve()
    if not root.is_dir(): raise ValueError('Run directory does not exist')
    directory = root / '_fpilot' / 'commands' / key
    directory.parent.mkdir(parents=True, exist_ok=True)
    try: directory.mkdir()
    except FileExistsError:
        return {'reserved': True, 'id': key}
    (directory / 'command.sh').write_bytes(base64.b64decode(encoded))
    write(directory / 'status.json', {'state': 'STARTING', 'created_at': time.time()})
    process = detached(root, key, 'wait', directory / 'runner.log')
    return {'reserved': True, 'id': key, 'pid': process.pid}


def wait(root, key):
    directory = Path(root) / '_fpilot' / 'commands' / key
    with (directory / 'stdout.log').open('ab') as out, (directory / 'stderr.log').open('ab') as err:
        try:
            process = subprocess.Popen(['/bin/bash', str(directory / 'command.sh')], cwd=root,
                                       stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        except OSError as error:
            err.write(str(error).encode('utf-8'))
            write(directory / 'status.json', {'state': 'EXITED', 'exit_code': 127, 'finished_at': time.time()})
            return
        write(directory / 'status.json', {'state': 'RUNNING', 'started_at': time.time(), 'pid': process.pid})
        code = process.wait()
    write(directory / 'status.json', {'state': 'EXITED', 'exit_code': code, 'finished_at': time.time()})


def inspect(root, key, stdout_offset=0, stderr_offset=0):
    directory = Path(root) / '_fpilot' / 'commands' / key
    if not directory.exists(): return {'id': key, 'state': 'UNKNOWN', 'note': '没有找到命令回执，不会重新执行'}
    try: result = json.loads((directory / 'status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): result = {'state': 'UNKNOWN'}
    result['id'] = key
    for stream, offset in (('stdout', stdout_offset), ('stderr', stderr_offset)):
        path = directory / (stream + '.log')
        data = b''
        if path.is_file():
            with path.open('rb') as handle:
                handle.seek(offset); data = handle.read(262144)
        result[stream] = base64.b64encode(data).decode('ascii')
        result[stream + '_offset'] = offset + len(data)
        result[stream + '_more'] = path.is_file() and path.stat().st_size > offset + len(data)
    return result


DIRECT_TERMINAL = {'EXITED', 'START_FAILED', 'CANCELLED', 'LOST'}
STOP_GRACE = 10
STOP_START_GRACE = 10
LOG_BYTES = 65536


def read_json(directory, filename):
    try:
        return json.loads((directory / filename).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def runner_code():
    source = Path(__file__).read_text(encoding='utf-8') if '__file__' in globals() and Path(__file__).is_file() else SOURCE
    return 'SOURCE=' + repr(source) + '\nexec(SOURCE)'


@contextmanager
def runtime_lock(directory):
    import fcntl
    with (directory / 'runtime.lock').open('ab') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def host_identity():
    return {'execution_host': socket.gethostname(),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def process_stat(pid):
    try:
        data = Path('/proc', str(pid), 'stat').read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = data.rpartition(b')')[2].split()
    return dict(pid=pid, state=fields[0], group=int(fields[2]), session=int(fields[3]), ticks=int(fields[19]))


def alive(process):
    return process is not None and process['state'] not in (b'Z', b'X', b'x')


def group_members(identity):
    pid = identity['process_pid']
    group = identity['process_group_id']
    leader = process_stat(pid)
    if leader and leader['ticks'] != identity['process_start_ticks']:
        # A new process using the numeric PID is unrelated to the old session.
        return []
    matches = []
    for entry in Path('/proc').iterdir():
        if entry.name.isascii() and entry.name.isdecimal():
            process = process_stat(int(entry.name))
            if alive(process) and process['session'] == pid and process['group'] == group:
                matches.append(process)
    # A session and group ID cannot be reused while their original members exist.
    # Recheck after scanning in case the last member exited during the scan.
    leader = process_stat(pid)
    return [] if leader and leader['ticks'] != identity['process_start_ticks'] else matches


def stop_progress(directory, stop):
    if not stop:
        request = read_json(directory, 'cancel-request.json')
        return {**request, 'state': 'RETRYABLE', 'note': '停止执行器尚未启动'} if request else {}
    if stop['state'] == 'UNKNOWN':
        # Both legacy writers recorded UNKNOWN only after the launch failed or
        # the worker finished its final signal attempt.
        return {**stop, 'state': 'RETRYABLE'}
    if stop['state'] == 'ACCEPTED' and 'worker_generation' in stop:
        if 'worker_pid' in stop:
            worker = process_stat(stop['worker_pid'])
            if not alive(worker) or worker['ticks'] != stop['worker_start_ticks']:
                return {**stop, 'state': 'RETRYABLE', 'note': '停止执行器已退出，任务进程仍在'}
        elif time.time() - stop['worker_launch_at'] >= STOP_START_GRACE:
            # No worker has claimed this generation. A retry fences out a
            # delayed child before it can send any signal.
            return {**stop, 'state': 'RETRYABLE', 'note': '停止执行器未完成启动'}
    return stop


def start_direct(root, key, encoded):
    directory = root / '_fpilot' / 'commands' / key
    if not root.is_dir():
        raise ValueError('Run directory does not exist')
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir()
    except FileExistsError:
        return observe_direct(directory)
    status = {'state': 'STARTING', 'created_at': time.time()}
    try:
        identity = host_identity()
        (directory / 'command.sh').write_bytes(base64.b64decode(encoded, validate=True))
        write(directory / 'identity.json', {'protocol_version': 1, **identity})
        write(directory / 'status.json', status)
        detached(root, key, 'wait-direct', directory / 'runner.log')
    except (OSError, ValueError) as error:
        status.update(state='START_FAILED', note=str(error), finished_at=time.time())
        write(directory / 'status.json', status)
    return status


def exec_direct(directory):
    process = process_stat(os.getpid())
    identity = read_json(directory, 'identity.json')
    identity.update(process_pid=process['pid'], process_start_ticks=process['ticks'],
                    process_group_id=process['group'], started_at=time.time())
    write(directory / 'identity.json', identity)
    os.execv('/bin/bash', ['/bin/bash', str(directory / 'command.sh')])


def wait_direct(root, key, directory):
    status = read_json(directory, 'status.json')
    supervisor = process_stat(os.getpid())
    identity = read_json(directory, 'identity.json')
    write(directory / 'identity.json', {**identity, 'supervisor_pid': supervisor['pid'],
                                      'supervisor_start_ticks': supervisor['ticks']})
    with (directory / 'stdout.log').open('ab') as out, (directory / 'stderr.log').open('ab') as err:
        try:
            process = subprocess.Popen([sys.executable, '-c', runner_code(), 'exec-direct', str(root), key],
                                       cwd=root, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       start_new_session=True, close_fds=True)
        except OSError as error:
            write(directory / 'status.json', {**status, 'state': 'START_FAILED',
                                              'note': str(error), 'finished_at': time.time()})
            return
        code = process.wait()
    identity = read_json(directory, 'identity.json')
    if 'process_pid' not in identity:
        write(directory / 'status.json', {**status, 'state': 'START_FAILED',
                                          'exit_code': code, 'finished_at': time.time()})
        return
    status.update(state='EXITING', exit_code=code)
    write(directory / 'status.json', status)
    while True:
        with runtime_lock(directory):
            if not group_members(identity):
                stop = read_json(directory, 'cancel-receipt.json')
                status.update(state='CANCELLED' if stop.get('signal_sent') else 'EXITED', finished_at=time.time())
                write(directory / 'status.json', status)
                return
        time.sleep(0.5)


def observe_direct(directory):
    status = read_json(directory, 'status.json')
    identity = read_json(directory, 'identity.json')
    stop = read_json(directory, 'cancel-receipt.json')
    result = {**identity, **status}
    if stop:
        result['stop'] = stop
    if status.get('state') in DIRECT_TERMINAL:
        return result
    try:
        host = host_identity()
        if not identity or identity.get('execution_host') != host['execution_host']:
            return {**result, 'state': 'UNKNOWN', 'note': '尚无回执，或当前 SSH 主机与启动主机不同'}
        if identity['boot_id'] != host['boot_id']:
            return {**result, 'state': 'LOST', 'note': '启动主机已重启，未取得结束回执'}
        supervisor = process_stat(identity['supervisor_pid']) if 'supervisor_pid' in identity else None
        tracking = alive(supervisor) and supervisor['ticks'] == identity['supervisor_start_ticks']
        if 'process_pid' not in identity:
            state = 'STARTING' if tracking or time.time() - status.get('created_at', 0) < 10 else 'UNKNOWN'
            return {**result, 'state': state}
        leader = process_stat(identity['process_pid'])
        leader_alive = alive(leader) and leader['ticks'] == identity['process_start_ticks']
        if leader_alive or group_members(identity):
            stop = stop_progress(directory, stop)
            if stop:
                result['stop'] = stop
            return {**result, 'state': 'RUNNING' if leader_alive else 'EXITING',
                    'note': '' if tracking else '退出回执收集器已中断，任务进程仍在'}
        final = read_json(directory, 'status.json')
        if final.get('state') in DIRECT_TERMINAL:
            return {**result, **final}
        if tracking:
            return {**result, 'state': 'EXITING'}
        stop = read_json(directory, 'cancel-receipt.json')
        if stop.get('state') == 'ACCEPTED' and time.time() - stop.get('worker_launch_at', stop.get('requested_at', 0)) < STOP_GRACE + 6:
            return {**result, 'state': 'EXITING', 'stop': stop}
        if stop.get('signal_sent'):
            return {**result, 'state': 'CANCELLED', 'stop': stop, 'finished_at': stop.get('finished_at'),
                    'note': '已确认停止；退出回执收集器已中断'}
        return {**result, 'state': 'LOST', 'note': '任务进程和收集器均已消失，未取得结束回执'}
    except (OSError, ValueError, IndexError, KeyError) as error:
        return {**result, 'state': 'UNKNOWN', 'note': '无法核对 Linux 进程身份：' + str(error)}


def pidfd_api():
    if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
        return os.pidfd_open, signal.pidfd_send_signal
    # An older Python can still use the libc wrappers where available.
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        fd_open, send_signal = libc.pidfd_open, libc.pidfd_send_signal
    except AttributeError:
        raise OSError('安全停止需要 Linux pidfd，以及支持它的 Python 或 libc') from None

    def checked(call, *args):
        result = call(*args)
        if result < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        return result

    return lambda pid: checked(fd_open, pid, 0), lambda fd, sig: checked(send_signal, fd, sig, None, 0)


def signal_members(identity, sig, open_pid, send_signal, on_sent):
    # A pidfd keeps the target identity stable across the final check and
    # signal, including when the group leader has already exited.
    for member in group_members(identity):
        pid = member['pid']
        try:
            fd = open_pid(pid)
        except ProcessLookupError:
            continue
        try:
            current = process_stat(pid)
            if alive(current) and all(current[field] == member[field] for field in ('ticks', 'group', 'session')):
                send_signal(fd, sig)
                on_sent()
        except ProcessLookupError:
            pass
        finally:
            os.close(fd)


def stop_direct(root, key, directory, operation, resume=False):
    if not directory.is_dir():
        return observe_direct(directory)
    with runtime_lock(directory):
        existing = read_json(directory, 'cancel-request.json')
        result = observe_direct(directory)
        if result['state'] not in ('RUNNING', 'EXITING'):
            return result
        stop = result.get('stop', {})
        if (existing and existing['operation_id'] != operation) or (resume and not existing):
            return {**result, 'stop': {'state': 'UNKNOWN', 'note': '无法确认原停止操作'}}
        if existing and (not resume or stop.get('state') != 'RETRYABLE'):
            return result
        try:
            open_pid, send_signal = pidfd_api()
            fd = open_pid(os.getpid())
            try:
                send_signal(fd, 0)
            finally:
                os.close(fd)
        except OSError as error:
            return {**result, 'stop': {**stop, 'state': 'RETRYABLE' if existing else 'REJECTED',
                                      'note': '此服务器无法使用 pidfd 安全停止：' + str(error)}}
        request = existing or {'operation_id': operation, 'requested_at': time.time()}
        if not existing:
            write(directory / 'cancel-request.json', request)
        generation = stop.get('worker_generation', 0) + 1
        receipt = {**request, 'state': 'ACCEPTED', 'worker_generation': generation,
                   'worker_launch_at': time.time()}
        if stop.get('signal_sent'):
            receipt.update(signal_sent=stop['signal_sent'], signalled_at=stop['signalled_at'])
        write(directory / 'cancel-receipt.json', receipt)
        try:
            detached(root, key, 'stop-worker', directory / 'runner.log', operation, str(generation))
        except OSError as error:
            receipt.update(state='RETRYABLE', note=str(error))
            write(directory / 'cancel-receipt.json', receipt)
        return {**result, 'stop': receipt}


def stop_worker(directory, operation, generation):
    def current():
        receipt = read_json(directory, 'cancel-receipt.json')
        if (receipt.get('operation_id'), receipt.get('worker_generation')) == (operation, generation):
            return receipt
        return {}

    with runtime_lock(directory):
        receipt = current()
        if receipt.get('state') != 'ACCEPTED' or 'worker_pid' in receipt:
            return
        worker = process_stat(os.getpid())
        if worker is None:
            return  # An unclaimed launch can be resumed after its startup grace.
        receipt.update(worker_pid=worker['pid'], worker_start_ticks=worker['ticks'])
        write(directory / 'cancel-receipt.json', receipt)
    identity = read_json(directory, 'identity.json')
    remaining, sig = STOP_GRACE, signal.SIGTERM
    if receipt.get('signal_sent'):
        remaining = (max(0, min(STOP_GRACE, receipt['signalled_at'] + STOP_GRACE - time.time()))
                     if receipt['signal_sent'] == 'SIGTERM' else 0)
        sig = None if remaining else signal.SIGKILL
    deadline = time.monotonic() + remaining
    try:
        open_pid, send_signal = pidfd_api()
        while True:
            with runtime_lock(directory):
                if not current():
                    return
                status = observe_direct(directory)
                if status['state'] in DIRECT_TERMINAL or not group_members(identity):
                    receipt.update(state='CONFIRMED' if receipt.get('signal_sent') else 'FINISHED',
                                   finished_at=time.time())
                    write(directory / 'cancel-receipt.json', receipt)
                    return
                if status['state'] not in ('RUNNING', 'EXITING'):
                    raise ValueError(status.get('note') or '无法核对任务组')
                if sig is not None:
                    def sent():
                        receipt.update(signal_sent=sig.name, signalled_at=time.time())
                        write(directory / 'cancel-receipt.json', receipt)
                    signal_members(identity, sig, open_pid, send_signal, sent)
            elapsed = time.monotonic() - deadline
            if elapsed > 5:
                raise ValueError('停止后仍有任务进程存活')
            sig = signal.SIGKILL if elapsed >= 0 else None
            time.sleep(0.25)
    except (OSError, ValueError, IndexError, KeyError) as error:
        with runtime_lock(directory):
            if current():
                receipt.update(state='RETRYABLE', note=str(error))
                write(directory / 'cancel-receipt.json', receipt)


def read_log(path, offset, token, tail, terminal):
    try:
        handle = path.open('rb')
    except FileNotFoundError:
        return '', 0, False, '', bool(offset or token)
    with handle:
        info = os.fstat(handle.fileno())
        identity = f'{info.st_dev}:{info.st_ino}'
        reset = offset > info.st_size or bool(token and (token != identity or info.st_size - offset > 4 * LOG_BYTES))
        if tail or reset:
            offset = max(0, info.st_size - LOG_BYTES)
        handle.seek(offset)
        data = handle.read(LOG_BYTES)
        if (tail or reset) and offset:
            trimmed = data.lstrip(bytes(range(128, 192)))
            offset += len(data) - len(trimmed)
            data = trimmed
        more = os.fstat(handle.fileno()).st_size > offset + len(data)
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    value = decoder.decode(data, final=terminal and not more)
    end = offset + len(data) - len(decoder.getstate()[0])
    return value, end, more, identity, reset


def direct_logs(directory, stdout_offset, stderr_offset, stdout_token, stderr_token, tail):
    result = observe_direct(directory)
    for stream, offset, token in (('stdout', stdout_offset, stdout_token), ('stderr', stderr_offset, stderr_token)):
        value, end, more, token, reset = read_log(directory / (stream + '.log'), int(offset), token,
                                                 tail == '1', result['state'] in DIRECT_TERMINAL)
        result.update({stream: value, stream + '_offset': end, stream + '_more': more,
                       stream + '_token': token, stream + '_reset': reset})
    return result


def main():
    action, root, key = sys.argv[1:4]
    if len(key) != 32 or any(c not in '0123456789abcdef' for c in key): raise ValueError('Invalid command ID')
    if action == 'wait': wait(root, key); return
    root_path = Path(root).resolve()
    directory = root_path / '_fpilot' / 'commands' / key
    if action == 'exec-direct': exec_direct(directory); return
    if action == 'wait-direct': wait_direct(root_path, key, directory); return
    if action == 'stop-worker': stop_worker(directory, sys.argv[4], int(sys.argv[5])); return
    if action == 'start-direct': result = start_direct(root_path, key, sys.argv[4])
    elif action == 'status-direct': result = observe_direct(directory)
    elif action == 'logs-direct': result = direct_logs(directory, *sys.argv[4:])
    elif action == 'stop-direct': result = stop_direct(root_path, key, directory, sys.argv[4], sys.argv[5:] == ['resume'])
    elif action == 'start': result = start(root, key, sys.argv[4])
    elif action == 'inspect': result = inspect(root, key, *map(int, sys.argv[4:6]))
    else: raise ValueError('Invalid command action')
    result['id'] = key
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
