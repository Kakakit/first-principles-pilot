"""Explicit, previewed removal of one attempt and its exact remote directory."""
from __future__ import annotations

import json
import re
import shlex
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from . import execution as ex, remote_paths
from .storage import ROOT


def _target(project_id, attempt_id):
    if not re.fullmatch(r'[a-f0-9]{32}', attempt_id):
        raise ValueError('attempt ID 无效')
    record = ex.get(attempt_id)
    if record.get('project_id') != project_id:
        raise ValueError('attempt 不属于当前项目')
    if record.get('state') not in ('PREPARED', 'COMPLETED', 'FAILED', 'CANCELLED', 'UNKNOWN', 'EXITING'):
        raise ValueError('调度器或工作台仍显示此任务在途；不能删除')
    if record.get('path_schema') != 'fpilot.remote-attempt-path':
        raise ValueError('远端目录布局不受支持')
    paths = ex.attempt_path(record)
    expected = remote_paths.absolute_attempt_path(record['remote_workspace_root_abs'], paths['relative'])
    if expected != record.get('remote_path_absolute'):
        raise ValueError('冻结远端路径与当前记录不一致')
    return record


def _pending_children(attempt_id):
    with closing(ex.db()) as connection:
        rows = [json.loads(row[0]) for row in connection.execute('SELECT body FROM attempts')]
    return [row['id'] for row in rows if row['id'] != attempt_id and row.get('state') not in ('COMPLETED', 'FAILED', 'CANCELLED')
            and (row.get('parent_attempt_id') == attempt_id or
                 (row.get('dependency_snapshot') or {}).get('upstream_attempt_id') == attempt_id or
                 (row.get('restart_snapshot') or {}).get('source_attempt_id') == attempt_id)]


def _script(record, expected_marker=None, delete=False, force=False):
    root = remote_paths.shell_path(record['remote_workspace_root'])
    destination = remote_paths.shell_path(record['remote_path_absolute'])
    identity = shlex.quote('"id": "' + record['id'] + '"')
    expected_root = shlex.quote(record['remote_workspace_root_abs'])
    expected_path = shlex.quote(record['remote_path_absolute'])
    scheduler = ex.scheduler_name(record)
    job_id = str(record.get('job_id') or '')
    if job_id and not re.fullmatch(r'\d+(?:\.[A-Za-z0-9_.-]+)?', job_id):
        raise ValueError('任务 job ID 格式不受支持，不能自动删除')
    if scheduler == 'pbs' and job_id:
        queue = (('' if force else 'command -v qstat >/dev/null 2>&1 || { printf "FPILOT_DELETE_BLOCKED_JOB=调度器不可用\\n"; exit 7; }\n') +
                 'state=$(qstat -f ' + shlex.quote(job_id) +
                 ' 2>/dev/null | sed -n -E "s/^[[:space:]]*job_state[[:space:]]*=[[:space:]]*([A-Z]).*/\\1/p" | head -1 || true)\n'
                 'case "$state" in ""|C|F) ;; *) printf "FPILOT_DELETE_BLOCKED_JOB=%s\\n" "$state"; exit 7;; esac\n'
                 'printf "FPILOT_DELETE_JOB_STATE=%s\\n" "${state:-UNAVAILABLE}"\n')
    elif scheduler == 'slurm' and job_id:
        queue = (('' if force else 'command -v squeue >/dev/null 2>&1 || { printf "FPILOT_DELETE_BLOCKED_JOB=调度器不可用\\n"; exit 7; }\n') +
                 'state=$(squeue -h -j ' + shlex.quote(job_id) + ' -o %T 2>/dev/null | head -1 || true)\n'
                 'test -z "$state" || { printf "FPILOT_DELETE_BLOCKED_JOB=%s\\n" "$state"; exit 7; }\n'
                 'printf "FPILOT_DELETE_JOB_STATE=%s\\n" "${state:-UNAVAILABLE}"\n')
    else:
        queue = ''
    script = (f'''set -e
root={root}
dest={destination}
test -d "$root" && test ! -L "$root"
test "$(cd "$root" && pwd -P)" = {expected_root}
''')
    if record['state'] == 'PREPARED':
        script += 'test ! -e "$dest"\nmarker=' + shlex.quote('0'*64) + '\nbytes=0\n'
    else:
        script += (f'''test -d "$dest" && test ! -L "$dest"
test "$(cd "$dest" && pwd -P)" = {expected_path}
{queue}if test -s "$dest/.job-id"; then
  remote_job=$(cat "$dest/.job-id")
  if test "$remote_job" != {shlex.quote(job_id)}; then
    printf 'FPILOT_DELETE_BLOCKED_JOB=远端 job ID 与本机记录不一致\\n'; exit 7
  fi
fi
''')
        if not force:
            script += 'test -s "$dest/_fpilot/attempt.json"\ngrep -Fq -- ' + identity + ' "$dest/_fpilot/attempt.json"\n'
        script += ('''if test -s "$dest/_fpilot/attempt.json"; then
  grep -Fq -- ''' + identity + ''' "$dest/_fpilot/attempt.json"
  marker=$(sha256sum "$dest/_fpilot/attempt.json" | awk '{print $1}')
else
  marker=$(find "$dest" -maxdepth 2 -type f -printf '%P|%s|%T@\\n' | LC_ALL=C sort | sha256sum | awk '{print $1}')
fi
bytes=$(du -s -B1 "$dest" | awk '{print $1}')
''')
        if not delete:
            script += '''find "$dest" -maxdepth 2 -type f -printf 'FPILOT_DELETE_FILE=%P|%s\\n' | head -20
'''
    if expected_marker:
        if not re.fullmatch(r'[a-f0-9]{64}', expected_marker):
            raise ValueError('远端身份哈希无效')
        script += 'test "$marker" = ' + shlex.quote(expected_marker) + '\n'
    if delete:
        script += ('rm -rf -- "$dest"\ntest ! -e "$dest"\n' if record['state'] != 'PREPARED' else '')
        script += 'printf "FPILOT_DELETE_DONE=%s|%s\\n" "$marker" "$bytes"\n'
    else:
        script += 'printf "FPILOT_DELETE_PREVIEW=%s|%s\\n" "$marker" "$bytes"\n'
    return script


def _check_receipt(receipt, label):
    if receipt.get('exit_code') != 0:
        blocked = re.search(r'^FPILOT_DELETE_BLOCKED_JOB=([^\n]+)$', receipt.get('stdout', ''), re.M)
        if blocked:
            raise ValueError('调度器仍显示任务状态 ' + blocked.group(1) + '；未删除')
        raise ValueError('远端目录身份、路径、调度状态或权限核对失败；未确认删除。' + receipt.get('stderr', '')[-250:])
    match = re.search(r'^FPILOT_DELETE_' + label + r'=([a-f0-9]{64})\|(\d+)$', receipt.get('stdout', ''), re.M)
    if not match:
        raise ValueError('远端删除回执不完整；请只读检查该目录状态，不要盲目重试')
    return match.group(1), int(match.group(2))


def preview(project_id, attempt_id):
    record = _target(project_id, attempt_id)
    children = _pending_children(attempt_id)
    if children:
        return {'blocked_children': children, 'attempt_id': attempt_id}
    force = record['state'] not in ('PREPARED', 'COMPLETED', 'FAILED')
    receipt = ex.ssh(record['config']['alias'], _script(record, force=force), timeout=120, stdout_limit=5000)
    marker, bytes_used = _check_receipt(receipt, 'PREVIEW')
    token = uuid.uuid4().hex
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    ROOT.joinpath('deletion-previews').mkdir(parents=True, exist_ok=True)
    ROOT.joinpath('deletion-previews', token + '.json').write_text(json.dumps({
        'project_id': project_id, 'attempt_id': attempt_id, 'marker': marker,
        'bytes': bytes_used, 'force': force, 'expires_at': expires.isoformat()}))
    return {'token': token, 'attempt_id': attempt_id, 'alias': record['config']['alias'],
            'remote_path': record['remote_path_absolute'],
            'confirm_text': record['remote_path_absolute'] if force else record['attempt_segment'],
            'force': force, 'state': record['state'], 'job_id': record.get('job_id'),
            'scheduler_state': (re.search(r'^FPILOT_DELETE_JOB_STATE=([^\n]+)$', receipt.get('stdout', ''), re.M).group(1)
                                if re.search(r'^FPILOT_DELETE_JOB_STATE=([^\n]+)$', receipt.get('stdout', ''), re.M) else None),
            'dispatch_receipt': record.get('dispatch_receipt'),
            'files': re.findall(r'^FPILOT_DELETE_FILE=([^\n]+)$', receipt.get('stdout', ''), re.M),
            'directory_bytes': bytes_used, 'expires_at': expires.isoformat(),
            'note': '未提交的 PREPARED 只删除本机记录；其他状态删除精确远端目录。硬链接共享数据的实际释放量可能较小。'}


def remove(project_id, attempt_id, token, confirm_text):
    if not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('删除预览令牌无效')
    path = ROOT / 'deletion-previews' / (token + '.json')
    if not path.is_file():
        raise ValueError('请重新生成此任务的只读删除预览')
    frozen = json.loads(path.read_text())
    if (frozen.get('project_id'), frozen.get('attempt_id')) != (project_id, attempt_id) or \
            datetime.fromisoformat(frozen['expires_at']) < datetime.now(timezone.utc):
        raise ValueError('删除预览已过期或与任务不符')
    record = _target(project_id, attempt_id)
    force = record['state'] not in ('PREPARED', 'COMPLETED', 'FAILED')
    if force != frozen.get('force'):
        raise ValueError('attempt 状态已变化，请重新预览')
    if confirm_text != (record['remote_path_absolute'] if force else record['attempt_segment']):
        raise ValueError('请输入完整的远端路径或 attempt 目录名确认删除')
    children = _pending_children(attempt_id)
    if children:
        raise ValueError('新的下游 attempt 已引用此来源；未删除')
    receipt = ex.ssh(record['config']['alias'], _script(record, frozen['marker'], delete=True, force=force), timeout=900, stdout_limit=3000)
    marker, bytes_used = _check_receipt(receipt, 'DONE')
    tombstone = ROOT / 'deleted-attempts' / (attempt_id + '.json')
    tombstone.parent.mkdir(parents=True, exist_ok=True)
    tombstone.write_text(json.dumps({'attempt_id': attempt_id, 'project_id': project_id,
        'alias': record['config']['alias'], 'remote_path': record['remote_path_absolute'],
        'marker_sha256': marker, 'directory_bytes_before': bytes_used,
        'deleted_at': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False))
    with closing(ex.db()) as connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('DELETE FROM execution_events WHERE attempt_id=?', (attempt_id,))
        connection.execute('DELETE FROM attempts WHERE id=?', (attempt_id,))
        connection.commit()
    path.unlink(missing_ok=True)
    (ROOT / 'recycle' / 'attempts' / attempt_id).unlink(missing_ok=True)
    return {'attempt_id': attempt_id, 'deleted': True, 'remote_path': record['remote_path_absolute'],
            'directory_bytes_before': bytes_used, 'note': '远端目录已删除；本机只保留最小删除回执。'}
