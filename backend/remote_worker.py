"""Ephemeral, stdlib-only request runner. Lives only inside an OpenSSH session."""
import base64
import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import time


def execute(job):
    started = time.monotonic()
    environment = dict(os.environ)
    environment.pop('BASH_ENV', None)
    environment.pop('ENV', None)
    environment.update(LC_ALL='C', LANG='C')
    process = subprocess.Popen(['/bin/bash', '--noprofile', '--norc', '-c', job['command']],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=environment, start_new_session=True)
    try:
        output, error = process.communicate(base64.b64decode(job.get('data', '')),
                                            timeout=job.get('timeout', 90))
        code = process.returncode
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, error = process.communicate()
        code = None
        error += b'\nRemote command timeout; do not replay a dispatch.'
    limit = job.get('stdout_limit', 524288)
    return {'exit_code': code, 'stdout': base64.b64encode(output[-limit:]).decode(),
            'stdout_truncated': len(output) > limit,
            'stderr': base64.b64encode(error[-32768:]).decode(),
            'remote_seconds': time.monotonic() - started,
            'received_bytes': len(output), 'sent_bytes': len(base64.b64decode(job.get('data', '')))}


def main():
    print('FPILOT_SESSION_READY', flush=True)
    for line in sys.stdin.buffer:
        request = json.loads(line)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(execute, request['jobs']))
        print(json.dumps({'id': request['id'], 'results': results}, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
