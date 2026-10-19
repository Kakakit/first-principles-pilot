"""Portable stdlib supervisor for a single direct extension invocation.

Copied into an independent analysis directory. It imports no workbench module.
The launch reservation is never removed, including after a lost SSH receipt.
"""
import argparse
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time


def atomic(path, value):
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as stream:
        json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())
        name = stream.name
    os.replace(name, path)


def identity(pid):
    if type(pid) is not int or pid < 1:return None
    try:
        stat = pathlib.Path('/proc', str(pid), 'stat').read_text()
        fields = stat[stat.rfind(')') + 2:].split()
        return {'pid': int(pid), 'start_ticks': fields[19],
                'boot': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'state': fields[0]}
    except (OSError, ValueError, IndexError):
        return None


def alive(marker):
    current = identity(marker.get('pid')) if isinstance(marker, dict) else None
    return bool(current and current['state'] != 'Z' and all(
        current.get(k) == marker.get(k) for k in ('pid', 'start_ticks', 'boot')))


def read(path):
    try:return json.loads(path.read_text()) if path.is_file() else None
    except (OSError,ValueError):return None


def inspect(directory):
    root = pathlib.Path(directory)
    marker = read(root / 'launch.json')
    receipt = None
    log = root / 'stdout.log'
    if log.is_file():
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size - 8192))
            for line in reversed(stream.read().decode(errors='replace').splitlines()):
                try:
                    candidate = json.loads(line)
                    if candidate.get('schema') == 'fpilot.extension-run.v2':
                        receipt = candidate; break
                except (ValueError, AttributeError):
                    pass
    logs = {}
    for name in ('doctor.err', 'check.err', 'stderr.log'):
        path = root / name
        if path.is_file():
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 1500))
                logs[name] = stream.read().decode(errors='replace')
    return {'exit': read(root / 'exit.json'), 'launch': marker,
            'stop_unconfirmed': read(root / 'stop-unconfirmed.json'),
            'process_alive': alive(marker), 'run_receipt': receipt,
            'job_id': (root / 'job.id').read_text().strip().split(';')[0] if (root / 'job.id').is_file() else None,
            'doctor': read(root / 'doctor.json'), 'check': read(root / 'check.json'), 'logs': logs}


def stop_descendants(process, root=None):
    """Stop owned groups only. A D-state child must never make this wait unbounded."""
    groups={process.pid};deadline=time.monotonic()+2
    if root is not None:
        marker=read(root/'cli-group.json')
        if marker:
            current=identity(marker['pid'])
            if current is None or alive(marker):groups.add(marker['pid'])
    for group in groups:
        try:os.killpg(group,signal.SIGKILL)
        except ProcessLookupError:pass
    try:process.wait(timeout=max(.001,deadline-time.monotonic()))
    except subprocess.TimeoutExpired:return False
    while time.monotonic()<deadline:
        remaining=[]
        for group in groups:
            try:os.killpg(group,0);remaining.append(group)
            except ProcessLookupError:pass
        if not remaining:return True
        time.sleep(.02)
    return False


def cli(seconds, command):
    root=pathlib.Path.cwd()
    process=subprocess.Popen(command,start_new_session=True)
    atomic(root/'cli-group.json',identity(process.pid) or {'pid':process.pid})
    try:
        try:return process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            stopped=stop_descendants(process)
            if not stopped:atomic(root/'stop-unconfirmed.json',{'pid':process.pid,'reason':'CLI timeout'})
            print('Extension CLI timeout; '+('process group stopped' if stopped else 'stop unconfirmed'),file=sys.stderr)
            return 124
    finally:
        if not (root/'stop-unconfirmed.json').exists():(root/'cli-group.json').unlink(missing_ok=True)


def supervise(root, timeout):
    marker = identity(os.getpid()) or {'pid': os.getpid()}
    marker['started_at'] = time.time()
    atomic(root / 'launch.json', marker)
    code = 127
    try:
        process = subprocess.Popen(['bash', 'launch.sh'], cwd=root, stdin=subprocess.DEVNULL,start_new_session=True)
        try: code = process.wait(timeout=timeout + 120)
        except subprocess.TimeoutExpired:
            stopped=stop_descendants(process,root);code=124
            if not stopped:atomic(root/'stop-unconfirmed.json',{'pid':process.pid,'reason':'supervisor timeout'})
            print('Extension supervisor timeout; '+('groups stopped' if stopped else 'stop unconfirmed'),file=sys.stderr)
    finally:
        if not (root / 'exit.json').is_file():
            atomic(root / 'exit.json', {'exit_code': code})


def start(root, timeout):
    try:
        descriptor = os.open(str(root / 'launch.once'), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {'started': False, 'reserved': True, 'launch': read(root / 'launch.json')}
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(str(time.time())); stream.flush(); os.fsync(stream.fileno())
    with (root / 'supervisor.log').open('ab') as log:
        subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), 'wait', str(root),
                          '--timeout', str(timeout)], cwd=root, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True, close_fds=True)
    deadline = time.monotonic() + 3
    while not (root / 'launch.json').is_file() and time.monotonic() < deadline:
        time.sleep(.02)
    return {'started': True, 'reserved': True, 'launch': read(root / 'launch.json')}


def main():
    if len(sys.argv)>1 and sys.argv[1]=='cli':
        sys.exit(cli(int(sys.argv[2]),sys.argv[3:]))
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['start', 'wait', 'inspect'])
    parser.add_argument('directory'); parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args(); root = pathlib.Path(args.directory).resolve()
    if not root.is_dir() or not 1 <= args.timeout <= 86400: raise ValueError('Invalid directory or deadline')
    if args.action == 'wait': supervise(root, args.timeout)
    else: print(json.dumps(start(root, args.timeout) if args.action == 'start' else inspect(root)))


if __name__ == '__main__':
    main()
