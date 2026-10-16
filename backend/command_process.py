"""Small detached shell runner; reports shell execution, never queue state."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


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
    with (directory / 'runner.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, '-c', Path(__file__).read_text() if '__file__' in globals() and Path(__file__).is_file() else SOURCE,
                                    'wait', str(root), key], stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True, close_fds=True)
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


def main():
    action, root, key = sys.argv[1:4]
    if len(key) != 32 or any(c not in '0123456789abcdef' for c in key): raise ValueError('Invalid command ID')
    if action == 'wait': wait(root, key); return
    result = start(root, key, sys.argv[4]) if action == 'start' else inspect(root, key, *map(int, sys.argv[4:6]))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
