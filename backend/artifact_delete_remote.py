"""Standalone bounded remote helper: inspect or unlink exactly one regular file."""
import json
import os
import re
import stat
import subprocess
import sys


def directory(path):
    if not path.startswith('/') or any(p in ('.', '..', '') for p in path.split('/')[1:]):
        raise ValueError('远端目录路径无效')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.split('/')[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def parent(root_fd, relative):
    parts = relative.split('/')
    if any(p in ('', '.', '..') for p in parts) or '\x00' in relative or '\n' in relative:
        raise ValueError('文件路径无效')
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd, parts[-1]
    except Exception:
        os.close(fd)
        raise


def small(root_fd, relative, limit):
    fd, name = parent(root_fd, relative)
    try:
        try:
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError('远端身份标记不是有限普通文件')
            return os.read(handle, limit + 1).decode('utf-8')
        finally:
            os.close(handle)
    finally:
        os.close(fd)


def identity(info):
    return {k: getattr(info, 'st_' + k) for k in ('dev', 'ino', 'size', 'mtime_ns', 'ctime_ns')}


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return result.returncode, result.stdout[-262144:], result.stderr[-2000:]
    except (OSError, subprocess.TimeoutExpired):
        return None, '', '调度器不可用或超时'


def stopped(payload):
    job = payload.get('job_id')
    if not job or not re.fullmatch(r'\d+(?:\.[A-Za-z0-9_.-]+)?', job):
        raise ValueError('没有可核对的精确 job ID，无法证明任务已停止')
    if payload['scheduler'] == 'pbs':
        code, text, error = command(['qstat', '-f', job])
        match = re.search(r'job_state\s*=\s*([A-Z])', text)
        if code == 0:
            if match and match[1] in ('C', 'F'):
                return match[1]
            raise ValueError('调度器仍显示任务在途或状态不明；禁止删除')
        # Absence is not completion. Require exact history or persisted terminal evidence.
        for args in (['qstat', '-x', '-f', job], ['tracejob', '-n', '30', job]):
            history_code, history, _ = command(args)
            state = re.search(r'job_state\s*=\s*([A-Z])', history)
            if history_code == 0 and state and state[1] in ('C', 'F'):
                return state[1]
            if (history_code == 0 and 'Job: ' + job in history
                    and 'state COMPLETE' in history and re.search(r'Exit_status=-?\d+', history)):
                return 'C'
        if (payload.get('terminal_evidence') in ('C', 'F') and code is not None
                and re.search(r'unknown\s+job|invalid\s+job|unknown\s+job\s+id', error + text, re.I)):
            return payload['terminal_evidence']
    elif payload['scheduler'] == 'slurm':
        code, text, _ = command(['squeue', '-h', '-j', job, '-o', '%T'])
        if code != 0 or text.strip():
            raise ValueError('调度器仍显示任务在途或查询不可用；禁止删除')
        code, history, _ = command(['sacct', '-n', '-P', '-j', job, '--format=JobIDRaw,State,ExitCode'])
        terminal = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'NODE_FAIL', 'OUT_OF_MEMORY', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}
        for line in history.splitlines() if code == 0 else []:
            parts = line.split('|')
            if len(parts) >= 3 and parts[0].strip() == job and parts[1].strip().split('+')[0] in terminal:
                return parts[1].strip().split('+')[0]
        if payload.get('terminal_evidence') in terminal:
            return payload['terminal_evidence']
    raise ValueError('无法证明任务已停止：没有可靠的调度终态证据；禁止删除')


def run(payload):
    import fcntl
    root_fd = directory(payload['root'])
    try:
        # No remote lock file or file contents are created. An in-flight helper owns this directory lock.
        fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        root_info = os.fstat(root_fd)
        root_identity = {'dev': root_info.st_dev, 'ino': root_info.st_ino}
        if payload.get('root_identity') and payload['root_identity'] != root_identity:
            raise ValueError('attempt 目录身份变化；请重新预览')
        action = payload['action']
        if action != 'status':
            try:
                marker = small(root_fd, '_fpilot/attempt.json', 262144)
            except FileNotFoundError:
                marker = None
            if marker is not None and json.loads(marker).get('id') != payload['attempt_id']:
                raise ValueError('远端 attempt 身份与本机记录不符')
            job = small(root_fd, '.job-id', 256)
            if job is not None and job.strip().split(';')[0] != payload.get('job_id'):
                raise ValueError('远端 job ID 与本机记录不符')
        fd, name = parent(root_fd, payload['path'])
        try:
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                if action == 'status':
                    return {'result': 'DELETED', 'root_identity': root_identity}
                raise ValueError('所选文件不存在；请刷新文件列表')
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('只允许普通文件；拒绝目录及符号链接')
            current = identity(info)
            if action == 'status':
                return {'result': 'NOT_DELETED' if current == payload['file_identity'] else 'CONFLICT', 'file_identity': current}
            scheduler_state = stopped(payload)
            if action == 'delete':
                # Scheduler checks may take time: compare again immediately before unlink.
                fresh = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(fresh.st_mode) or identity(fresh) != payload['file_identity']:
                    raise ValueError('文件身份已变化；未删除，请重新预览')
                os.unlink(name, dir_fd=fd)
                os.fsync(fd)
                try:
                    os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    return {'result': 'DELETED', 'scheduler_state': scheduler_state}
                return {'result': 'CONFLICT', 'scheduler_state': scheduler_state}
            return {'result': 'PREVIEWED', 'file_identity': current, 'root_identity': root_identity,
                    'size_bytes': info.st_size, 'link_count': info.st_nlink, 'scheduler_state': scheduler_state}
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)


if __name__ == '__main__':
    try:
        result = run(json.loads(sys.argv[1]))
    except Exception as exc:
        result = {'result': 'BLOCKED', 'error': str(exc)}
    print('FPILOT_ARTIFACT_DELETE=' + json.dumps(result, ensure_ascii=False))
