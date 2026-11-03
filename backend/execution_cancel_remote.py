"""Exact-job scheduler adapter. No process matching, file deletion or resubmission."""
import getpass
import json
import os
import re
import stat
import subprocess
import sys


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=25)
    return result.returncode, result.stdout[-262144:], result.stderr[-2000:]


def small(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 262144:
            raise ValueError('作业身份文件格式或大小无效')
        return os.read(fd, 262145).decode('utf-8')
    finally:
        os.close(fd)


def inspect(payload):
    root, job = payload['root'], payload['job_id']
    if not re.fullmatch(r'\d+(?:\.[A-Za-z0-9_.-]+)?', job):
        raise ValueError('需要准确的单个作业号')
    if not root.startswith('/') or os.path.realpath(root) != root:
        raise ValueError('远端目录或符号链接身份不符')
    identity_verified = False
    try:
        marker = json.loads(small(root + '/_fpilot/attempt.json'))
        if marker.get('id') != payload['attempt_id'] or small(root + '/.job-id').strip() != job:
            raise ValueError('远端 attempt 或作业号与记录不符')
        identity_verified = True
    except FileNotFoundError:
        if payload['action'] != 'inspect' or payload.get('allow_missing_identity') is not True:
            raise
    user = getpass.getuser()
    if not identity_verified and payload.get('verified_owner') != user:
        raise ValueError('取消受理时核对的作业所有者与当前用户不符')
    if payload['scheduler'] == 'pbs':
        code, text, error = command(['qstat', '-f', job])
        def row(raw):
            found = re.search(r'^Job Id:\s*(\S+)', raw, re.M)
            state = re.search(r'job_state\s*=\s*([A-Z])', raw)
            owner = re.search(r'Job_Owner\s*=\s*([^@\s]+)', raw)
            exit_status = re.search(r'Exit_status\s*=\s*(-?\d+)', raw)
            if not found or found[1] != job or not state or not owner or owner[1] != user:
                raise ValueError('调度器未返回匹配的作业身份、所有者或状态')
            return {'scheduler_state': state[1], 'terminal': state[1] in ('C', 'F'),
                    'exit_code': int(exit_status[1]) if exit_status else None, 'owner': user}
        if code == 0: return row(text)
        code, history, _ = command(['qstat', '-x', '-f', job])
        if code == 0: return row(history)
        code, history, _ = command(['tracejob', '-n', '30', job])
        found = re.search(r'^\s*Job:\s*(\S+)', history, re.M)
        exits = re.findall(r'Exit_status\s*=\s*(-?\d+)', history)
        owner = re.search(r'Job_Owner\s*=\s*([^@\s]+)', history)
        if code == 0 and found and found[1] == job and 'state COMPLETE' in history and exits:
            if owner and owner[1] != user:
                raise ValueError('accounting 的作业所有者不匹配')
            return {'scheduler_state': 'C', 'terminal': True, 'exit_code': int(exits[-1]), 'owner': user,
                    'identity_source': 'attempt_files' if identity_verified else 'accepted_cancellation_and_accounting'}
        raise ValueError('无法核对原作业状态；队列中缺失不证明已停止。' + error[-250:])
    if payload['scheduler'] != 'slurm' or not job.isdigit():
        raise ValueError('调度器或作业号格式不受支持')
    code, text, _ = command(['scontrol', 'show', 'job', job, '-o'])
    if code == 0:
        found = re.search(r'\bJobId=(\S+)', text)
        owner = re.search(r'\bUserId=([^\s(]+)', text)
        state = re.search(r'\bJobState=(\S+)', text)
        exit_status = re.search(r'\bExitCode=(\d+):(\d+)', text)
        if not found or found[1] != job or not owner or owner[1] != user or not state:
            raise ValueError('调度器作业身份或所有者不匹配')
        status = state[1]
        return {'scheduler_state': status, 'terminal': status in TERMINAL,
                'exit_code': (int(exit_status[1]) or int(exit_status[2])) if exit_status else None, 'owner': user}
    code, text, _ = command(['sacct', '-n', '-P', '-j', job, '--format=JobIDRaw,State,ExitCode,User'])
    for line in text.splitlines() if code == 0 else []:
        parts = line.split('|')
        if len(parts) >= 4 and parts[0].strip() == job and parts[3].strip() == user:
            status = parts[1].strip().split('+')[0].split()[0]
            return {'scheduler_state': status, 'terminal': status in TERMINAL,
                    'exit_code': parts[2].strip(), 'owner': user}
    raise ValueError('无法核对原作业状态；队列中缺失不证明已停止')


TERMINAL = {'COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT', 'NODE_FAIL', 'OUT_OF_MEMORY',
            'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}


def run(payload):
    facts = inspect(payload)
    if payload['action'] == 'cancel' and not facts['terminal']:
        args = ['qdel', payload['job_id']] if payload['scheduler'] == 'pbs' else ['scancel', payload['job_id']]
        # Exactly one scheduler command. Any ambiguous reply is recovered with
        # inspect only; it is never sent again by this operation.
        try:
            code, text, error = command(args)
            facts.update(result='ACCEPTED' if code == 0 else 'REJECTED', command_exit_code=code,
                         command_output=(text + error)[-2000:])
        except (OSError, subprocess.TimeoutExpired) as exc:
            facts.update(result='UNKNOWN', error=str(exc))
    else:
        facts['result'] = 'TERMINAL' if facts['terminal'] else 'LIVE'
    return facts


if __name__ == '__main__':
    try:
        result = run(json.loads(sys.argv[1]))
    except Exception as exc:
        result = {'result': 'BLOCKED', 'error': str(exc)}
    print('FPILOT_CANCEL=' + json.dumps(result, ensure_ascii=False))
